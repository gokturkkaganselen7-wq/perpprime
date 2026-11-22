#!/usr/bin/env python3
"""Cross-sectional momentum across the 10-symbol USDT perp universe.

Every ``rebalance_bars`` the engine ranks the universe by its trailing
``lookback_bars`` return, goes long the top ``k`` and short the bottom ``k``,
and holds until the next rebalance. Positions that stay in the target are
kept (no churn); the rest are closed at the next open.

Risk and honesty rules match the other engines:
  * rank on a CLOSED bar, trade at the NEXT bar open
  * 0.5% equity risk per leg, stop 1.5 ATR, 3x portfolio notional cap
  * gap-aware stops, stop-first ambiguity, intrabar drawdown
  * taker 5.5 bps + 2 bps slippage per fill, real funding per 8h stamp

With ``k=1`` the book holds at most two positions, which fits the live
``max_open_positions=3`` gate. Larger ``k`` is a research variant only.

Usage:
  PYTHONPATH=src .venv/bin/python scripts/backtest_xsec.py --mode single \
      --start 2025-11-14 --end 2026-11-14
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
    MMR,
    SLIP_BPS,
    _metrics,
    atr_series,
    fetch_funding,
    fetch_ohlcv,
    iso,
    parse_day,
)

HOUR = 3_600_000
UNIVERSE = [
    "BTC/USDT:USDT", "ETH/USDT:USDT", "SOL/USDT:USDT", "BNB/USDT:USDT",
    "XRP/USDT:USDT", "DOGE/USDT:USDT", "AVAX/USDT:USDT", "LINK/USDT:USDT",
    "ARB/USDT:USDT", "OP/USDT:USDT",
]


@dataclass(frozen=True)
class XsecParams:
    lookback_bars: int = 168      # 7 days on 1h bars
    # Daily rebalancing costs ~0.15% round trip per leg and dominated the
    # first test; 3 days is the cost-aware default.
    rebalance_bars: int = 72
    k: int = 1
    stop_atr: float = 1.5
    risk_pct: float = 0.5
    max_leg_leverage: float = 1.5

    def label(self) -> str:
        return (f"lb{self.lookback_bars}/rb{self.rebalance_bars}/k{self.k}/"
                f"s{self.stop_atr:g}")


GRID = [
    XsecParams(lookback_bars=lb, rebalance_bars=rb, stop_atr=s)
    for lb, rb, s in itertools.product([72, 168, 336], [24, 72], [1.5, 2.5])
]


def _funding_by_bar(rows: list[list[float]], funding: list[list[float]]) -> list[float]:
    out = [0.0] * len(rows)
    fi = 0
    for i, r in enumerate(rows):
        lo = int(r[0])
        hi = lo + HOUR
        total = 0.0
        while fi < len(funding) and funding[fi][0] <= lo:
            fi += 1
        j = fi
        while j < len(funding) and funding[j][0] <= hi:
            total += funding[j][1]
            j += 1
        out[i] = total
        fi = j
    return out


def load_universe(start_ms: int, end_ms: int,
                  symbols: list[str]) -> dict[str, Any]:
    data = {}
    for sym in symbols:
        print(f"[data] {sym}", file=sys.stderr)
        rows = fetch_ohlcv(sym, "1h", start_ms, end_ms)
        funding = fetch_funding(sym, start_ms, end_ms)
        data[sym] = {
            "rows": rows,
            "by_ts": {int(r[0]): r for r in rows},
            "atr": atr_series(rows, 14),
            "funding": _funding_by_bar(rows, funding),
        }
    common = sorted(set.intersection(
        *(set(d["by_ts"]) for d in data.values())
    ))
    return {"data": data, "index": common}


def simulate_xsec(uni: dict[str, Any], params: XsecParams,
                  start_ms: int, end_ms: int,
                  equity0: float = 10_000.0) -> dict[str, Any]:
    data, index = uni["data"], uni["index"]
    n = len(index)
    symbols = list(data)

    equity = equity0
    positions: dict[str, dict[str, Any]] = {}
    trades: list[dict[str, Any]] = []
    curve: list[list[float]] = []
    pending_close: list[str] = []
    pending_open: dict[str, str] = {}
    peak = equity0
    max_dd = 0.0
    rebal = params.rebalance_bars

    def bar(sym: str, i: int):
        return data[sym]["by_ts"][index[i]]

    def atr_at(sym: str, i: int) -> float | None:
        # data[sym]["atr"] is aligned with that symbol's own row list; map by ts
        ts = index[i]
        rows = data[sym]["rows"]
        lo, hi = 0, len(rows) - 1
        while lo <= hi:              # the arrays are monotonically indexed
            mid = (lo + hi) // 2
            if rows[mid][0] < ts:
                lo = mid + 1
            elif rows[mid][0] > ts:
                hi = mid - 1
            else:
                return data[sym]["atr"][mid]
        return None

    def close_pos(sym: str, px: float, ts: int, reason: str,
                  extra_fee_bps: float = 0.0) -> None:
        nonlocal equity
        p = positions.pop(sym)
        fill = px * (1 - SLIP_BPS / 10_000) if p["side"] == "long" \
            else px * (1 + SLIP_BPS / 10_000)
        gross = (fill - p["entry"]) * p["qty"] if p["side"] == "long" \
            else (p["entry"] - fill) * p["qty"]
        fee = fill * p["qty"] * (FEE_BPS + extra_fee_bps) / 10_000
        equity += gross - fee + p["funding"]
        pnl = gross - fee - p["entry_fee"] + p["funding"]
        trades.append({
            "side": p["side"],
            "entry_ts": iso(p["entry_ts"]), "exit_ts": iso(ts),
            "entry": round(p["entry"], 6),
            "pnl": round(pnl, 4),
            "fees": round(p["entry_fee"] + fee, 4),
            "funding": round(p["funding"], 4),
            "r": round(pnl / p["risk_amount"], 3) if p["risk_amount"] else 0.0,
            "exit_reason": reason,
            "partials": 1,
            "bars_held": int((ts - p["entry_ts"]) / HOUR),
            "symbol": p["symbol"],
        })

    def open_pos(sym: str, side: str, i: int) -> None:
        nonlocal equity
        r = bar(sym, i)
        atr = atr_at(sym, i - 1)
        if atr is None or atr <= 0:
            return
        entry = r[1] * (1 + SLIP_BPS / 10_000) if side == "long" \
            else r[1] * (1 - SLIP_BPS / 10_000)
        risk_dist = params.stop_atr * atr
        if risk_dist <= 0:
            return
        risk_amount = equity * params.risk_pct / 100.0
        qty = risk_amount / risk_dist
        notional = qty * entry
        if notional > equity * params.max_leg_leverage:
            qty = equity * params.max_leg_leverage / entry
            notional = qty * entry
        if notional < 10:
            return
        fee = notional * FEE_BPS / 10_000
        equity -= fee
        positions[sym] = {
            "symbol": sym, "side": side, "entry": entry, "qty": qty,
            "stop": entry - risk_dist if side == "long" else entry + risk_dist,
            "entry_ts": int(index[i]), "risk_amount": risk_amount,
            "entry_fee": fee, "funding": 0.0,
            "liq": _liq(side, entry, notional / max(equity, 1e-9)),
        }

    end_i = n - 1
    while end_i > 0 and index[end_i] > end_ms:
        end_i -= 1

    for i in range(1, end_i + 1):
        ts = index[i]

        # 1. pending orders execute at this bar's open
        if ts > start_ms:
            for sym in pending_close:
                if sym in positions:
                    r = bar(sym, i)
                    close_pos(sym, r[1], ts, "rebalance")
            pending_close = []
            for sym, side in pending_open.items():
                if sym not in positions:
                    open_pos(sym, side, i)
            pending_open = {}

        # 2. funding + stops
        if ts > start_ms:
            for sym in list(positions):
                p = positions[sym]
                r = bar(sym, i)
                fi = _row_index(data, sym, ts)
                rate = data[sym]["funding"][fi] if fi >= 0 else 0.0
                if rate:
                    p["funding"] += rate * p["qty"] * r[4] * (
                        1.0 if p["side"] == "short" else -1.0)
                liq_hit = p["liq"] is not None and (
                    (r[1] <= p["liq"]) if p["side"] == "long" else (r[1] >= p["liq"]))
                if liq_hit:
                    close_pos(sym, r[1], ts, "liquidation", 55.0)
                    continue
                stop_hit = (r[3] <= p["stop"]) if p["side"] == "long" \
                    else (r[2] >= p["stop"])
                if stop_hit:
                    if p["side"] == "long":
                        fill, gapped = min(r[1], p["stop"]), r[1] < p["stop"]
                    else:
                        fill, gapped = max(r[1], p["stop"]), r[1] > p["stop"]
                    close_pos(sym, fill, ts, "stop_gap" if gapped else "stop")

        # 3. mark the curve
        if ts >= start_ms:
            close_mark = equity + sum(
                _unrealized(p, bar(sym, i)[4]) + p["funding"]
                for sym, p in positions.items())
            worst = equity + sum(
                _unrealized(p, _adverse(p, bar(sym, i))) + p["funding"]
                for sym, p in positions.items())
            worst = max(worst, 0.0)
            peak = max(peak, close_mark)
            if peak > 0:
                max_dd = max(max_dd, (peak - worst) / peak * 100.0)
            curve.append([int(ts), round(close_mark, 2), round(worst, 2)])

        # 4. rebalance at the close, execute at the next open
        if i % rebal or ts < start_ms or i + 1 >= n or ts >= end_ms:
            continue
        ranked = _rank(data, index, i, params.lookback_bars)
        if not ranked:
            continue
        top = ranked[: params.k]
        bottom = ranked[-params.k:]
        target = {s: "long" for s in top}
        target.update({s: "short" for s in bottom})
        for sym in list(positions):
            if target.get(sym) != positions[sym]["side"]:
                pending_close.append(sym)
        for sym, side in target.items():
            current = positions.get(sym)
            if current is None or current["side"] != side:
                pending_open[sym] = side

    for sym in list(positions):
        close_pos(sym, bar(sym, end_i)[4], int(index[end_i]), "window_end")

    return _metrics(trades, equity0, equity, max_dd=max_dd, curve=curve)


def _row_index(data: dict[str, Any], sym: str, ts: int) -> int:
    """Index into a symbol's own rows for the funding array."""
    rows = data[sym]["rows"]
    lo, hi = 0, len(rows) - 1
    while lo <= hi:
        mid = (lo + hi) // 2
        if rows[mid][0] < ts:
            lo = mid + 1
        elif rows[mid][0] > ts:
            hi = mid - 1
        else:
            return mid
    return -1


def _unrealized(p: dict[str, Any], price: float) -> float:
    return (price - p["entry"]) * p["qty"] if p["side"] == "long" \
        else (p["entry"] - price) * p["qty"]


def _adverse(p: dict[str, Any], r: list[float]) -> float:
    if p["side"] == "long":
        return max(r[3], p["stop"])
    return min(r[2], p["stop"])


def _liq(side: str, entry: float, leverage: float) -> float | None:
    if leverage <= 1.0:
        return None
    if side == "long":
        return entry * (1 - 1 / leverage + MMR)
    return entry * (1 + 1 / leverage - MMR)


def _rank(data: dict[str, Any], index: list[int], i: int,
          lookback: int) -> list[str]:
    if i - lookback < 0:
        return []
    scores = {}
    for sym, d in data.items():
        past = d["by_ts"].get(index[i - lookback])
        now = d["by_ts"].get(index[i])
        if past is None or now is None or past[4] <= 0:
            continue
        scores[sym] = now[4] / past[4] - 1.0
    return [s for s, _ in sorted(scores.items(), key=lambda kv: kv[1],
                                 reverse=True)]


def score(res: dict[str, Any], min_trades: int) -> tuple[float, float]:
    if res["trades"] < min_trades:
        return (-1.0, -1.0)
    pf = res["profit_factor"] if res["profit_factor"] is not None else 0.0
    return (pf, res["avg_r"])


def walk_forward(uni: dict[str, Any], start_ms: int, end_ms: int,
                 train_days: int, test_days: int, step_days: int,
                 equity0: float = 10_000.0,
                 min_train_trades: int = 8) -> dict[str, Any]:
    windows = []
    t0 = start_ms
    while True:
        train_end = t0 + train_days * DAY
        test_end = train_end + test_days * DAY
        if test_end > end_ms:
            break
        best, best_score = None, (-2.0, -2.0)
        for candidate in GRID:
            r = simulate_xsec(uni, candidate, t0, train_end, equity0)
            s = score(r, min_train_trades)
            if s > best_score:
                best_score, best = s, (candidate, r)
        chosen = best[0] if best else GRID[0]
        oos = simulate_xsec(uni, chosen, train_end, test_end, equity0)
        base = simulate_xsec(uni, XsecParams(), train_end, test_end, equity0)
        keep = ("trades", "win_rate", "avg_r", "profit_factor",
                "max_drawdown_pct", "total_return_pct")
        windows.append({
            "train": f"{iso(t0)[:10]} -> {iso(train_end)[:10]}",
            "test": f"{iso(train_end)[:10]} -> {iso(test_end)[:10]}",
            "chosen": chosen.label(),
            "oos": {k: oos[k] for k in keep},
            "baseline": {k: base[k] for k in keep},
        })
        t0 += step_days * DAY
    return {"windows": windows}


def aggregate(windows: list[dict[str, Any]]) -> dict[str, Any]:
    out = {}
    for key in ("oos", "baseline"):
        rows = [w[key] for w in windows]
        rets = [float(r["total_return_pct"]) for r in rows]
        out[key] = {
            "windows": len(rows),
            "trades": sum(r["trades"] for r in rows),
            "avg_return_pct": round(sum(rets) / len(rets), 2) if rets else 0.0,
            "positive_windows": sum(1 for r in rets if r > 0),
            "worst_return_pct": round(min(rets), 2) if rets else 0.0,
            "worst_dd_pct": round(max(r["max_drawdown_pct"] for r in rows), 2)
            if rows else 0.0,
        }
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["single", "walkforward"], default="single")
    ap.add_argument("--start", required=True)
    ap.add_argument("--end", required=True)
    ap.add_argument("--warmup-days", type=int, default=30)
    ap.add_argument("--train-days", type=int, default=90)
    ap.add_argument("--test-days", type=int, default=30)
    ap.add_argument("--step-days", type=int, default=30)
    ap.add_argument("--k", type=int, default=1)
    ap.add_argument("--lookback-bars", type=int, default=None)
    ap.add_argument("--rebalance-bars", type=int, default=None)
    ap.add_argument("--json-out", default="")
    ap.add_argument("--fee-bps", type=float, default=None)
    ap.add_argument("--slip-bps", type=float, default=None)
    args = ap.parse_args()

    if args.fee_bps is not None or args.slip_bps is not None:
        import backtest_trend as bt
        if args.fee_bps is not None:
            globals()["FEE_BPS"] = args.fee_bps
            bt.FEE_BPS = args.fee_bps
        if args.slip_bps is not None:
            globals()["SLIP_BPS"] = args.slip_bps
            bt.SLIP_BPS = args.slip_bps

    start_ms, end_ms = parse_day(args.start), parse_day(args.end)
    uni = load_universe(start_ms - args.warmup_days * DAY, end_ms, UNIVERSE)
    params = XsecParams(
        k=args.k,
        lookback_bars=args.lookback_bars or 168,
        rebalance_bars=args.rebalance_bars or 72,
    )

    payload: dict[str, Any] = {
        "generated": datetime.now(timezone.utc).isoformat(),
        "strategy": "xsec",
        "mode": args.mode,
        "universe": UNIVERSE,
        "range": {"start": args.start, "end": args.end},
        "config": {"fee_bps": FEE_BPS, "slip_bps": SLIP_BPS,
                   "k": args.k, "default_params": params.label()},
        "results": [],
    }

    if args.mode == "single":
        t = time.time()
        res = simulate_xsec(uni, params, start_ms, end_ms)
        res.update({"symbol": "XSEC10", "timeframe": "1h",
                    "params": params.label()})
        payload["results"].append(res)
        print(f"[res ] XSEC10 trades={res['trades']} pf={res['profit_factor']} "
              f"ret={res['total_return_pct']}% dd={res['max_drawdown_pct']}% "
              f"({time.time()-t:.0f}s)", file=sys.stderr)
    else:
        t = time.time()
        wf = walk_forward(uni, start_ms, end_ms, args.train_days,
                          args.test_days, args.step_days)
        wf.update({"symbol": "XSEC10", "timeframe": "1h",
                   "aggregate": aggregate(wf["windows"])})
        payload["results"].append(wf)
        agg = wf["aggregate"]["oos"]
        print(f"[wf  ] XSEC10 windows={agg['windows']} trades={agg['trades']} "
              f"avg={agg['avg_return_pct']}% ({time.time()-t:.0f}s)",
              file=sys.stderr)

    if args.json_out:
        Path(args.json_out).write_text(json.dumps(payload, indent=2))
    print(json.dumps({k: v for k, v in payload.items() if k != "results"}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
