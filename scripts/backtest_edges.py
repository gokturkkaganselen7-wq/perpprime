#!/usr/bin/env python3
"""Mechanical edge tests: funding carry, opening-range breakout, range fade.

The trend engine (``backtest_trend.py``) already validates one hypothesis and
lost money. This script tests three structurally different ideas with the same
honesty rules and the same cost model:

  carry  funding harvest: short when 8h funding is rich, long when it is
         negative, exit when the carry stops paying
  orb    opening-range breakout: first N hours of the UTC day define a range,
         a close outside it with volume enters, the day end closes the trade
  range  mean reversion: in a 4h ``range`` regime only, fade a >= N ATR
         deviation from EMA20 after a reclaim candle, target the mean

Shared rules
  * signal on a CLOSED bar, fill at the NEXT bar open
  * stop-first when both stop and target sit inside one bar
  * gap-aware stops, isolated-margin liquidation, intrabar drawdown
  * taker 5.5 bps + 2 bps slippage per fill, real Bybit funding

Usage:
  PYTHONPATH=src .venv/bin/python scripts/backtest_edges.py --strategy carry \
      --mode single --start 2025-11-14 --end 2026-11-14
  PYTHONPATH=src .venv/bin/python scripts/backtest_edges.py --strategy orb \
      --mode walkforward --start 2025-11-01 --end 2026-11-14
"""

from __future__ import annotations

import argparse
import itertools
import json
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from backtest_trend import (  # noqa: E402
    DAY,
    FEE_BPS,
    FOUR_H,
    LIQ_FEE_BPS,
    SLIP_BPS,
    TF_MS,
    _metrics,
    fetch_funding,
    fetch_ohlcv,
    iso,
    liquidation_price,
    parse_day,
    prepare,
    slip,
)

STRATEGIES = ("carry", "orb", "range", "squeeze")


@dataclass(frozen=True)
class EdgeParams:
    name: str
    stop_atr: float = 1.5
    tp_r: float = 2.0
    max_bars: int = 24
    entry_bp: float = 1.0
    exit_bp: float = 0.0
    or_hours: int = 4
    dev_atr: float = 2.0
    vol_mult: float = 1.2
    lookback_bars: int = 120
    squeeze_pct: float = 0.04
    require_regime: bool = True
    risk_pct: float = 0.5
    max_leverage: float = 3.0

    def label(self) -> str:
        if self.name == "carry":
            return f"carry_f{self.entry_bp:g}_x{self.exit_bp:g}_h{self.max_bars}_s{self.stop_atr:g}"
        if self.name == "orb":
            return f"orb_h{self.or_hours}_v{self.vol_mult:g}_s{self.stop_atr:g}_t{self.tp_r:g}"
        if self.name == "squeeze":
            return (f"sq_l{self.lookback_bars}_w{self.squeeze_pct:g}_"
                    f"s{self.stop_atr:g}_t{self.tp_r:g}")
        return f"range_d{self.dev_atr:g}_s{self.stop_atr:g}_t{self.tp_r:g}_rg{int(self.require_regime)}"


GRIDS: dict[str, list[EdgeParams]] = {
    "carry": [
        EdgeParams("carry", entry_bp=e, max_bars=h, stop_atr=s, tp_r=t)
        for e, h, s, t in itertools.product(
            [1.0, 2.0, 5.0], [8, 24, 72], [1.0, 2.0], [2.0, 4.0]
        )
    ],
    "orb": [
        EdgeParams("orb", or_hours=o, vol_mult=v, stop_atr=s, tp_r=t)
        for o, v, s, t in itertools.product(
            [2, 4], [1.0, 1.5], [1.0, 1.5], [1.5, 2.5]
        )
    ],
    "range": [
        EdgeParams("range", dev_atr=d, stop_atr=s, tp_r=t, max_bars=m)
        for d, s, t, m in itertools.product(
            [1.5, 2.0, 2.5], [1.0, 1.5], [1.0, 2.0], [24, 72]
        )
    ],
    "squeeze": [
        EdgeParams("squeeze", lookback_bars=l, squeeze_pct=w, stop_atr=s,
                   tp_r=t, max_bars=120)
        for l, w, s, t in itertools.product(
            [72, 120, 168], [0.04, 0.08], [1.5], [2.0, 4.0]
        )
    ],
}


# --------------------------------------------------------------------------- #
# per-bar context
# --------------------------------------------------------------------------- #
def build_context(prep: dict[str, Any], params: EdgeParams) -> dict[str, Any]:
    rows = prep["rows"]
    n = len(rows)
    fbb = prep["funding_by_bar"]

    # last funding rate seen, so the carry signal does not flicker on bars
    # that simply have no 8h stamp
    last_funding = [0.0] * n
    current = 0.0
    for i in range(n):
        if fbb[i]:
            current = fbb[i]
        last_funding[i] = current

    vol_ma: list[float | None] = [None] * n
    for i in range(n):
        window = [r[5] for r in rows[max(0, i - 19): i + 1]]
        if len(window) == 20 and sum(window) > 0:
            vol_ma[i] = sum(window) / 20.0

    day_id = [int(r[0] // DAY) for r in rows]
    bar_in_day = [0] * n
    seen: dict[int, int] = {}
    for i, d in enumerate(day_id):
        bar_in_day[i] = seen.get(d, 0)
        seen[d] = bar_in_day[i] + 1

    or_high: list[float | None] = [None] * n
    or_low: list[float | None] = [None] * n
    day_end: list[bool] = [False] * n
    sq_high: list[float | None] = [None] * n
    sq_low: list[float | None] = [None] * n
    sq_width: list[float | None] = [None] * n
    if params.name == "squeeze":
        lb = params.lookback_bars
        for i in range(lb, n):
            window = rows[i - lb: i]          # excludes the signal bar itself
            hi = max(r[2] for r in window)
            lo = min(r[3] for r in window)
            base = rows[i - 1][4]
            if base > 0:
                sq_high[i] = hi
                sq_low[i] = lo
                sq_width[i] = (hi - lo) / base
    if params.name == "orb":
        first: dict[int, list[list[float]]] = {}
        for i, d in enumerate(day_id):
            first.setdefault(d, []).append(rows[i])
        for i, d in enumerate(day_id):
            bars = first[d]
            if bar_in_day[i] >= params.or_hours and len(bars) >= params.or_hours:
                opening = bars[: params.or_hours]
                or_high[i] = max(r[2] for r in opening)
                or_low[i] = min(r[3] for r in opening)
            if i + 1 >= n or day_id[i + 1] != d:
                day_end[i] = True

    return {"last_funding": last_funding, "vol_ma": vol_ma, "day_id": day_id,
            "bar_in_day": bar_in_day, "or_high": or_high, "or_low": or_low,
            "day_end": day_end, "sq_high": sq_high, "sq_low": sq_low,
            "sq_width": sq_width}


# --------------------------------------------------------------------------- #
# signals (evaluated on a closed bar)
# --------------------------------------------------------------------------- #
@dataclass
class Setup:
    side: str
    stop: float
    tp: float | None
    max_bars: int
    tag: str


def signal(prep: dict[str, Any], ctx: dict[str, Any], params: EdgeParams,
           i: int) -> Setup | None:
    rows = prep["rows"]
    e20, a14 = prep["e20"][i], prep["a14"][i]
    if e20 is None or a14 is None or a14 <= 0:
        return None
    _ts, o, h, l, c, v = rows[i]
    atr_i = a14

    if params.name == "carry":
        rate_bp = ctx["last_funding"][i] * 10_000
        if rate_bp >= params.entry_bp:
            side = "short"
        elif rate_bp <= -params.entry_bp:
            side = "long"
        else:
            return None
        stop = c + params.stop_atr * atr_i if side == "short" \
            else c - params.stop_atr * atr_i
        return Setup(side, stop, None, params.max_bars, f"funding_{rate_bp:.1f}bp")

    if params.name == "orb":
        high, low = ctx["or_high"][i], ctx["or_low"][i]
        vol_ma = ctx["vol_ma"][i]
        if high is None or low is None or vol_ma is None:
            return None
        if ctx["bar_in_day"][i] + 1 < params.or_hours:
            return None
        enough_volume = v >= params.vol_mult * vol_ma
        if c > high and enough_volume:
            side = "long"
        elif c < low and enough_volume:
            side = "short"
        else:
            return None
        stop = c - params.stop_atr * atr_i if side == "long" \
            else c + params.stop_atr * atr_i
        return Setup(side, stop, None, params.max_bars, "orb")

    if params.name == "squeeze":
        hi, lo, width = ctx["sq_high"][i], ctx["sq_low"][i], ctx["sq_width"][i]
        vol_ma = ctx["vol_ma"][i]
        if hi is None or lo is None or width is None or vol_ma is None:
            return None
        regime = _regime(prep, rows[i][0])
        if params.require_regime and regime not in ("squeeze", "range"):
            return None
        if width > params.squeeze_pct:
            return None
        if v < params.vol_mult * vol_ma:
            return None
        if c > hi:
            side = "long"
        elif c < lo:
            side = "short"
        else:
            return None
        stop = c - params.stop_atr * atr_i if side == "long" \
            else c + params.stop_atr * atr_i
        return Setup(side, stop, None, params.max_bars,
                     f"squeeze_{width * 100:.1f}pct_{regime}")

    # range mean reversion
    regime = _regime(prep, rows[i][0])
    if params.require_regime and regime != "range":
        return None
    stretch = (e20 - c) / atr_i if e20 > c else (c - e20) / atr_i
    if e20 - c >= params.dev_atr * atr_i and c > o:
        side, stop = "long", c - params.stop_atr * atr_i
    elif c - e20 >= params.dev_atr * atr_i and c < o:
        side, stop = "short", c + params.stop_atr * atr_i
    else:
        return None
    tp = e20
    return Setup(side, stop, tp, params.max_bars, f"stretch_{stretch:.1f}")


def _regime(prep: dict[str, Any], ts: int) -> str:
    idx = (((int(ts) // FOUR_H) * FOUR_H - FOUR_H) - prep["h4_start"]) // FOUR_H
    if not (0 <= idx < len(prep["regime_at"])):
        return "warmup"
    return prep["regime_at"][idx]["regime"]


# --------------------------------------------------------------------------- #
# simulation
# --------------------------------------------------------------------------- #
def simulate_edge(prep: dict[str, Any], params: EdgeParams,
                  start_ms: int, end_ms: int,
                  equity0: float = 10_000.0) -> dict[str, Any]:
    rows = prep["rows"]
    step = TF_MS[prep["tf"]]
    ctx = build_context(prep, params)
    fbb = prep["funding_by_bar"]

    equity = equity0
    trades: list[dict[str, Any]] = []
    curve: list[list[float]] = []
    pos: dict[str, Any] | None = None
    pending_exit: str | None = None
    peak_equity = equity0
    max_dd = 0.0
    ruined = False
    last_entry_day: int | None = None

    def unrealized(side: str, entry: float, price: float, qty: float) -> float:
        return (price - entry) * qty if side == "long" else (entry - price) * qty

    def close_position(px: float, ts: int, qty: float, reason: str,
                       extra_fee_bps: float = 0.0) -> None:
        nonlocal equity, pos, pending_exit, ruined
        assert pos is not None
        fill = slip(px, pos["side"], entering=False)
        gross = (fill - pos["entry"]) * qty if pos["side"] == "long" \
            else (pos["entry"] - fill) * qty
        fee = fill * qty * (FEE_BPS + extra_fee_bps) / 10_000
        pos["realized"] += gross - fee
        pos["fees"] += fee
        pos["exits"].append({"ts": ts, "price": round(fill, 4),
                             "qty": round(qty, 6), "reason": reason})
        if qty >= pos["qty"] - 1e-12:
            equity += pos["realized"]
            if equity <= 0:
                equity = 0.0
                ruined = True
            trades.append({
                "side": pos["side"],
                "entry_ts": iso(pos["entry_ts"]), "exit_ts": iso(ts),
                "entry": round(pos["entry"], 4),
                "pnl": round(pos["realized"], 4),
                "fees": round(pos["fees"], 4),
                "funding": round(pos["funding"], 4),
                "r": round(pos["realized"] / pos["risk_amount"], 3)
                if pos["risk_amount"] else 0.0,
                "exit_reason": pos["exits"][-1]["reason"],
                "partials": len(pos["exits"]),
                "bars_held": int((ts - pos["entry_ts"]) / step),
                "setup": pos["tag"],
            })
            pos = None
            pending_exit = None

    end_i = len(rows) - 1
    while end_i > 0 and rows[end_i][0] > end_ms:
        end_i -= 1

    for i in range(1, end_i + 1):
        if ruined:
            break
        ts, o, h, l, c, _v = rows[i]
        equity_at_start = equity
        if pos is not None and ts > start_ms:
            side_start = pos["side"]
            entry_start = pos["entry"]
            stop_start = pos["stop"]
            qty_start = pos["qty"]

            rate = fbb[i]
            if rate:
                sign = 1.0 if pos["side"] == "long" else -1.0
                pay = sign * pos["qty"] * c * rate
                pos["realized"] -= pay
                pos["funding"] -= pay
            realized_start = pos["realized"]

            liq_hit = False
            if pos["liq_price"] is not None:
                liq_hit = (o <= pos["liq_price"]) if pos["side"] == "long" \
                    else (o >= pos["liq_price"])
            if liq_hit:
                close_position(o, ts, pos["qty"], "liquidation", LIQ_FEE_BPS)
            else:
                if pending_exit:
                    close_position(o, ts, pos["qty"], pending_exit)
                    pending_exit = None

                if pos is not None:
                    stop_hit = (l <= pos["stop"]) if pos["side"] == "long" \
                        else (h >= pos["stop"])
                    if stop_hit:
                        if pos["side"] == "long":
                            fill_px, gapped = min(o, pos["stop"]), o < pos["stop"]
                        else:
                            fill_px, gapped = max(o, pos["stop"]), o > pos["stop"]
                        close_position(fill_px, ts, pos["qty"],
                                       "stop_gap" if gapped else "stop")
                    elif pos["tp"] is not None and (
                        (h >= pos["tp"]) if pos["side"] == "long" else (l <= pos["tp"])
                    ):
                        close_position(pos["tp"], ts, pos["qty"], "tp")
                    elif params.name == "orb" and ctx["day_end"][i]:
                        pending_exit = "day_end"
                    elif params.name == "carry" and _carry_done(ctx, params, i,
                                                                pos["side"]):
                        pending_exit = "carry_done"
                    elif i - pos["entry_index"] >= pos["max_bars"]:
                        pending_exit = "time_stop"

            if ts >= start_ms:
                if pos is not None:
                    adverse_pre = l if side_start == "long" else h
                    adverse_pre = (max(adverse_pre, stop_start)
                                   if side_start == "long" else min(adverse_pre, stop_start))
                    pre_mark = (equity_at_start + realized_start
                                + unrealized(side_start, entry_start, adverse_pre,
                                             qty_start))
                    adverse_post = l if pos["side"] == "long" else h
                    adverse_post = (max(adverse_post, pos["stop"])
                                    if pos["side"] == "long"
                                    else min(adverse_post, pos["stop"]))
                    post_mark = (equity_at_start + pos["realized"]
                                 + unrealized(pos["side"], pos["entry"],
                                              adverse_post, pos["qty"]))
                    worst = max(min(pre_mark, post_mark), 0.0)
                    close_mark = (equity_at_start + pos["realized"]
                                  + unrealized(pos["side"], pos["entry"], c,
                                               pos["qty"]))
                else:
                    worst = max(min(equity, equity), 0.0)
                    close_mark = equity
                peak_equity = max(peak_equity, close_mark)
                if peak_equity > 0:
                    max_dd = max(max_dd, (peak_equity - worst) / peak_equity * 100.0)
                curve.append([int(ts), round(close_mark, 2), round(worst, 2)])
        elif ts >= start_ms:
            peak_equity = max(peak_equity, equity)
            curve.append([int(ts), round(equity, 2), round(equity, 2)])

        if ruined or pos is not None or ts < start_ms or ts >= end_ms:
            continue
        if i + 1 >= len(rows):
            continue
        if params.name == "orb" and ctx["day_id"][i] == last_entry_day:
            # The hypothesis is the FIRST breakout of the day, not every
            # re-entry after a stop-out.
            continue

        setup = signal(prep, ctx, params, i)
        if setup is None:
            continue

        entry = slip(rows[i + 1][1], setup.side, entering=True)
        risk_dist = (entry - setup.stop) if setup.side == "long" \
            else (setup.stop - entry)
        atr_i = prep["a14"][i]
        if risk_dist <= 0 or risk_dist > 2.5 * atr_i:
            continue

        risk_amount = equity * params.risk_pct / 100.0
        qty = risk_amount / risk_dist
        if qty * entry > equity * params.max_leverage:
            qty = equity * params.max_leverage / entry
        if qty * entry < 10:
            continue

        tp = None
        if setup.tp is not None:
            # A mean-reversion target that the entry has already gapped past
            # is not a target any more: the setup is invalid.
            if (setup.side == "long" and setup.tp <= entry) or \
                    (setup.side == "short" and setup.tp >= entry):
                continue
            tp = setup.tp
        elif params.tp_r > 0:
            tp = entry + params.tp_r * risk_dist if setup.side == "long" \
                else entry - params.tp_r * risk_dist

        lev = (qty * entry) / equity if equity > 0 else 0.0
        pos = {
            "side": setup.side, "entry_ts": int(rows[i + 1][0]), "entry": entry,
            "stop": setup.stop, "tp": tp, "qty": qty, "max_bars": setup.max_bars,
            "entry_index": i + 1, "risk_amount": risk_amount, "tag": setup.tag,
            "fees": 0.0, "funding": 0.0, "realized": 0.0, "exits": [],
            "liq_price": liquidation_price(setup.side, entry, lev),
        }
        fee = entry * qty * FEE_BPS / 10_000
        pos["fees"] += fee
        pos["realized"] -= fee
        last_entry_day = ctx["day_id"][i]

    if pos is not None:
        close_position(rows[end_i][4], rows[end_i][0], pos["qty"], "window_end")

    return _metrics(trades, equity0, equity, max_dd=max_dd, curve=curve)


def _carry_done(ctx: dict[str, Any], params: EdgeParams, i: int, side: str) -> bool:
    rate_bp = ctx["last_funding"][i] * 10_000
    if side == "short":
        return rate_bp <= params.exit_bp
    return rate_bp >= -params.exit_bp


# --------------------------------------------------------------------------- #
# walk-forward
# --------------------------------------------------------------------------- #
def score(res: dict[str, Any], min_trades: int) -> tuple[float, float]:
    if res["trades"] < min_trades:
        return (-1.0, -1.0)
    pf = res["profit_factor"] if res["profit_factor"] is not None else 0.0
    return (pf, res["avg_r"])


def walk_forward(prep: dict[str, Any], params: EdgeParams, start_ms: int,
                 end_ms: int, train_days: int, test_days: int, step_days: int,
                 equity0: float = 10_000.0,
                 min_train_trades: int = 10) -> dict[str, Any]:
    grid = GRIDS[params.name]
    windows: list[dict[str, Any]] = []
    t0 = start_ms
    while True:
        train_end = t0 + train_days * DAY
        test_end = train_end + test_days * DAY
        if test_end > end_ms:
            break
        best = None
        best_score = (-2.0, -2.0)
        for candidate in grid:
            r = simulate_edge(prep, candidate, t0, train_end, equity0)
            s = score(r, min_train_trades)
            if s > best_score:
                best_score, best = s, (candidate, r)
        chosen = best[0] if best else grid[0]
        oos = simulate_edge(prep, chosen, train_end, test_end, equity0)
        fixed = simulate_edge(prep, params, train_end, test_end, equity0)
        windows.append({
            "train": f"{iso(t0)[:10]} -> {iso(train_end)[:10]}",
            "test": f"{iso(train_end)[:10]} -> {iso(test_end)[:10]}",
            "chosen": chosen.label(),
            "train_pf": best[1]["profit_factor"] if best else None,
            "train_trades": best[1]["trades"] if best else 0,
            "oos": {k: oos[k] for k in
                    ("trades", "win_rate", "avg_r", "profit_factor",
                     "max_drawdown_pct", "total_return_pct")},
            "baseline": {k: fixed[k] for k in
                         ("trades", "win_rate", "avg_r", "profit_factor",
                          "max_drawdown_pct", "total_return_pct")},
        })
        t0 += step_days * DAY
    return {"windows": windows}


def aggregate(windows: list[dict[str, Any]]) -> dict[str, Any]:
    out = {}
    for key in ("oos", "baseline"):
        rows = [w[key] for w in windows]
        n = sum(r["trades"] for r in rows)
        rets = [float(r["total_return_pct"]) for r in rows]
        out[key] = {
            "windows": len(rows),
            "trades": n,
            "avg_win_rate": round(sum(r["win_rate"] for r in rows) / len(rows), 2)
            if rows else 0.0,
            "avg_return_pct": round(sum(rets) / len(rets), 2) if rets else 0.0,
            "positive_windows": sum(1 for r in rets if r > 0),
            "worst_return_pct": round(min(rets), 2) if rets else 0.0,
            "best_return_pct": round(max(rets), 2) if rets else 0.0,
            "worst_dd_pct": round(max(r["max_drawdown_pct"] for r in rows), 2)
            if rows else 0.0,
        }
    return out


# --------------------------------------------------------------------------- #
# cli
# --------------------------------------------------------------------------- #
def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--strategy", choices=STRATEGIES, required=True)
    ap.add_argument("--mode", choices=["single", "walkforward"], default="single")
    ap.add_argument("--symbols", nargs="+",
                    default=["BTC/USDT:USDT", "ETH/USDT:USDT", "SOL/USDT:USDT"])
    ap.add_argument("--timeframes", nargs="+", default=["1h"])
    ap.add_argument("--start", required=True, help="YYYY-MM-DD")
    ap.add_argument("--end", required=True, help="YYYY-MM-DD")
    ap.add_argument("--warmup-days", type=int, default=75)
    ap.add_argument("--train-days", type=int, default=90)
    ap.add_argument("--test-days", type=int, default=30)
    ap.add_argument("--step-days", type=int, default=30)
    ap.add_argument("--json-out", default="")
    ap.add_argument("--fee-bps", type=float, default=None,
                    help="override taker fee for cost-sensitivity runs")
    ap.add_argument("--slip-bps", type=float, default=None,
                    help="override slippage for cost-sensitivity runs")
    args = ap.parse_args()

    if args.fee_bps is not None or args.slip_bps is not None:
        import backtest_trend as bt
        if args.fee_bps is not None:
            globals()["FEE_BPS"] = args.fee_bps
            bt.FEE_BPS = args.fee_bps
        if args.slip_bps is not None:
            bt.SLIP_BPS = args.slip_bps

    params = EdgeParams(args.strategy)
    start_ms = parse_day(args.start)
    end_ms = parse_day(args.end)
    fetch_start = start_ms - args.warmup_days * DAY

    payload: dict[str, Any] = {
        "generated": datetime.now(timezone.utc).isoformat(),
        "strategy": args.strategy,
        "mode": args.mode,
        "range": {"start": args.start, "end": args.end},
        "config": {"fee_bps": FEE_BPS, "slip_bps": SLIP_BPS,
                   "warmup_days": args.warmup_days,
                   "default_params": params.label()},
        "results": [],
    }

    for symbol in args.symbols:
        for tf in args.timeframes:
            t = time.time()
            print(f"[data] {symbol} {tf} {args.start}..{args.end}", file=sys.stderr)
            rows = fetch_ohlcv(symbol, tf, fetch_start, end_ms)
            funding = fetch_funding(symbol, fetch_start, end_ms)
            print(f"[prep] bars={len(rows)} funding={len(funding)}", file=sys.stderr)
            prep = prepare(symbol, tf, rows, funding)

            if args.mode == "single":
                res = simulate_edge(prep, params, start_ms, end_ms)
                res.update({"symbol": symbol, "timeframe": tf,
                            "params": params.label()})
                payload["results"].append(res)
                print(f"[res ] {symbol} {tf} trades={res['trades']} "
                      f"pf={res['profit_factor']} ret={res['total_return_pct']}% "
                      f"dd={res['max_drawdown_pct']}% ({time.time()-t:.0f}s)",
                      file=sys.stderr)
            else:
                wf = walk_forward(prep, params, start_ms, end_ms,
                                  args.train_days, args.test_days, args.step_days)
                wf.update({"symbol": symbol, "timeframe": tf,
                           "aggregate": aggregate(wf["windows"])})
                payload["results"].append(wf)
                agg = wf["aggregate"]
                print(f"[wf  ] {symbol} {tf} windows={agg['oos']['windows']} "
                      f"oos_trades={agg['oos']['trades']} "
                      f"oos_avg={agg['oos']['avg_return_pct']}% "
                      f"base_avg={agg['baseline']['avg_return_pct']}% "
                      f"({time.time()-t:.0f}s)", file=sys.stderr)

    if args.json_out:
        Path(args.json_out).write_text(json.dumps(payload, indent=2))
    print(json.dumps({k: v for k, v in payload.items() if k != "results"}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
