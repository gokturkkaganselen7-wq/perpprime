#!/usr/bin/env python3
"""Funding dispersion: long the cheapest perp, short the richest, collect carry.

The book is market-neutral by notional: at each rebalance the universe is
ranked by its recent 8h funding, the top ``k`` symbols are shorted (they pay
the most) and the bottom ``k`` are bought (they pay the least or receive).
Net funding = short rates - long rates; the trade profits if that spread is
larger than the round-trip fees.

Why this differs from the single-leg carry test in ``backtest_edges.py``:
  * no spot leg, so only perp taker fees (5.5 bps) apply
  * two or four legs, so price direction largely cancels
  * low turnover (3-14 day rebalance) keeps the fee bill small

Honesty rules match the other engines: signal on a closed bar, fill at the
next open, real funding per 8h stamp, gap-aware stops, intrabar drawdown.

Usage:
  PYTHONPATH=src .venv/bin/python scripts/backtest_funding_dispersion.py \
      --mode single --start 2025-11-14 --end 2026-11-14
  ... --placebo 20        # same engine with shuffled ranks, as a control
"""

from __future__ import annotations

import argparse
import itertools
import json
import random
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
    SLIP_BPS,
    _metrics,
    iso,
    parse_day,
)
from backtest_xsec import (  # noqa: E402
    BASE_SLIP_BPS,
    MID_SLIP_BPS,
    UNIVERSE,
    UNIVERSE_ALL,
    UNIVERSE_MID,
    _adv_at,
    dynamic_slip_bps,
    load_universe,
)

HOUR = 3_600_000


@dataclass(frozen=True)
class DispParams:
    lookback_stamps: int = 3      # average of the last N 8h funding stamps
    rebalance_days: int = 7
    k: int = 1                    # k shorts + k longs
    min_spread_bp: float = 1.0    # per 8h, between the two sides
    stop_atr: float = 5.0
    max_hold_days: int = 14
    exposure_pct: float = 100.0   # gross notional as % of equity
    max_leg_pct: float = 50.0

    def label(self) -> str:
        return (f"lb{self.lookback_stamps}/rb{self.rebalance_days}d/k{self.k}/"
                f"sp{self.min_spread_bp:g}/s{self.stop_atr:g}")


GRID = [
    DispParams(lookback_stamps=lb, rebalance_days=rb, k=k, min_spread_bp=sp)
    for lb, rb, k, sp in itertools.product(
        [1, 3, 9], [3, 7, 14], [1, 2], [1.0, 2.0]
    )
]


def funding_scores(uni: dict[str, Any],
                   lookback: int) -> dict[str, list[float | None]]:
    """Mean of the last ``lookback`` funding stamps at every bar."""
    out: dict[str, list[float | None]] = {}
    for sym, d in uni["data"].items():
        window: list[float] = []
        series: list[float | None] = []
        for rate in d["funding"]:
            if rate:
                window.append(rate)
                if len(window) > lookback:
                    window.pop(0)
            series.append(sum(window) / len(window) if window else None)
        out[sym] = series
    return out


def simulate_disp(uni: dict[str, Any], params: DispParams,
                  start_ms: int, end_ms: int,
                  equity0: float = 10_000.0,
                  placebo_seed: int | None = None) -> dict[str, Any]:
    data, index = uni["data"], uni["index"]
    n = len(index)
    scores = funding_scores(uni, params.lookback_stamps)
    pos_of = {
        sym: {int(r[0]): i for i, r in enumerate(d["rows"])}
        for sym, d in data.items()
    }
    rebal = params.rebalance_days * 24

    equity = equity0
    positions: dict[str, dict[str, Any]] = {}
    trades: list[dict[str, Any]] = []
    curve: list[list[float]] = []
    pending_close: list[str] = []
    pending_open: dict[str, str] = {}
    peak = equity0
    max_dd = 0.0

    def bar(sym: str, i: int):
        return data[sym]["by_ts"][index[i]]

    def score_at(sym: str, ts: int) -> float | None:
        p = pos_of[sym].get(ts)
        return scores[sym][p] if p is not None else None

    def unrealized(p: dict[str, Any], price: float) -> float:
        return (price - p["entry"]) * p["qty"] if p["side"] == "long" \
            else (p["entry"] - price) * p["qty"]

    def close_pos(sym: str, px: float, ts: int, reason: str) -> None:
        nonlocal equity
        p = positions.pop(sym)
        slip = p.get("slip_bps", SLIP_BPS)
        fill = px * (1 - slip / 10_000) if p["side"] == "long" \
            else px * (1 + slip / 10_000)
        gross = (fill - p["entry"]) * p["qty"] if p["side"] == "long" \
            else (p["entry"] - fill) * p["qty"]
        fee = fill * p["qty"] * FEE_BPS / 10_000
        equity += gross - fee + p["funding"]
        pnl = gross - fee - p["entry_fee"] + p["funding"]
        trades.append({
            "side": p["side"],
            "entry_ts": iso(p["entry_ts"]), "exit_ts": iso(ts),
            "entry": round(p["entry"], 6),
            "pnl": round(pnl, 4),
            "fees": round(p["entry_fee"] + fee, 4),
            "funding": round(p["funding"], 4),
            "r": round(pnl / p["notional"], 4) if p["notional"] else 0.0,
            "exit_reason": reason,
            "partials": 1,
            "bars_held": int((ts - p["entry_ts"]) / HOUR),
            "symbol": sym,
        })

    def open_pos(sym: str, side: str, i: int, notional: float) -> None:
        nonlocal equity
        r = bar(sym, i)
        slip = dynamic_slip_bps(
            _adv_at(data, sym, index[i - 1]), notional,
            base_bps=BASE_SLIP_BPS if sym in UNIVERSE else MID_SLIP_BPS,
        )
        entry = r[1] * (1 + slip / 10_000) if side == "long" \
            else r[1] * (1 - slip / 10_000)
        if entry <= 0 or notional < 10:
            return
        atr = _atr_at(data, sym, index[i - 1])
        stop = None
        if atr and atr > 0:
            stop = entry - params.stop_atr * atr if side == "long" \
                else entry + params.stop_atr * atr
        fee = notional * FEE_BPS / 10_000
        equity -= fee
        positions[sym] = {
            "symbol": sym, "side": side, "entry": entry,
            "qty": notional / entry, "notional": notional, "stop": stop,
            "entry_ts": int(index[i]), "entry_index": i,
            "entry_fee": fee, "funding": 0.0, "slip_bps": slip,
        }

    end_i = n - 1
    while end_i > 0 and index[end_i] > end_ms:
        end_i -= 1

    for i in range(1, end_i + 1):
        ts = index[i]

        if ts > start_ms:
            for sym in pending_close:
                if sym in positions:
                    close_pos(sym, bar(sym, i)[1], ts, "rebalance")
            pending_close = []

            for sym, side in pending_open.items():
                if sym not in positions:
                    per_leg = min(
                        equity * params.exposure_pct / 100.0
                        / max(1, 2 * params.k),
                        equity * params.max_leg_pct / 100.0,
                    )
                    open_pos(sym, side, i, per_leg)
            pending_open = {}

            for sym in list(positions):
                p = positions[sym]
                r = bar(sym, i)
                fi = pos_of[sym].get(ts)
                rate = data[sym]["funding"][fi] if fi is not None else 0.0
                if rate:
                    p["funding"] += rate * p["qty"] * r[4] * (
                        1.0 if p["side"] == "short" else -1.0)
                if p["stop"] is not None:
                    hit = (r[3] <= p["stop"]) if p["side"] == "long" \
                        else (r[2] >= p["stop"])
                    if hit:
                        if p["side"] == "long":
                            px, gapped = min(r[1], p["stop"]), r[1] < p["stop"]
                        else:
                            px, gapped = max(r[1], p["stop"]), r[1] > p["stop"]
                        close_pos(sym, px, ts, "stop_gap" if gapped else "stop")

        if ts >= start_ms:
            close_mark = equity + sum(
                unrealized(p, bar(sym, i)[4]) + p["funding"]
                for sym, p in positions.items())
            worst = equity + sum(
                unrealized(p, _adverse(p, bar(sym, i))) + p["funding"]
                for sym, p in positions.items())
            worst = max(worst, 0.0)
            peak = max(peak, close_mark)
            if peak > 0:
                max_dd = max(max_dd, (peak - worst) / peak * 100.0)
            curve.append([int(ts), round(close_mark, 2), round(worst, 2)])

        if i % rebal or ts < start_ms or i + 1 >= n or ts >= end_ms:
            continue

        ranked = _rank(data, score_at, index, i, placebo_seed)
        if len(ranked) < 2 * params.k:
            continue
        top = ranked[: params.k]
        bottom = ranked[-params.k:]
        high = score_at(top[0], index[i])
        low = score_at(bottom[-1], index[i])
        if high is None or low is None or \
                (high - low) < params.min_spread_bp / 10_000:
            for sym in list(positions):
                pending_close.append(sym)
            continue

        target = {s: "short" for s in top}
        target.update({s: "long" for s in bottom})
        for sym in list(positions):
            stale = i - positions[sym]["entry_index"] >= params.max_hold_days * 24
            if target.get(sym) != positions[sym]["side"] or stale:
                pending_close.append(sym)
        for sym, side in target.items():
            current = positions.get(sym)
            if current is None or current["side"] != side:
                pending_open[sym] = side

    for sym in list(positions):
        close_pos(sym, bar(sym, end_i)[4], int(index[end_i]), "window_end")

    return _metrics(trades, equity0, equity, max_dd=max_dd, curve=curve)


def _rank(data: dict[str, Any], score_at, index: list[int], i: int,
          placebo_seed: int | None) -> list[str]:
    values = []
    for sym in data:
        s = score_at(sym, index[i])
        if s is not None:
            values.append((sym, s))
    values.sort(key=lambda kv: kv[1], reverse=True)
    ordered = [s for s, _ in values]
    if placebo_seed is not None:
        random.Random(placebo_seed * 100_003 + i).shuffle(ordered)
    return ordered


def _atr_at(data: dict[str, Any], sym: str, ts: int) -> float | None:
    rows = data[sym]["rows"]
    lo, hi = 0, len(rows) - 1
    while lo <= hi:
        mid = (lo + hi) // 2
        if rows[mid][0] < ts:
            lo = mid + 1
        elif rows[mid][0] > ts:
            hi = mid - 1
        else:
            return data[sym]["atr"][mid]
    return None


def _adverse(p: dict[str, Any], r: list[float]) -> float:
    if p["stop"] is not None:
        return max(r[3], p["stop"]) if p["side"] == "long" \
            else min(r[2], p["stop"])
    return r[3] if p["side"] == "long" else r[2]


# --------------------------------------------------------------------------- #
# walk-forward / placebo / cli
# --------------------------------------------------------------------------- #
def score(res: dict[str, Any], min_trades: int) -> tuple[float, float]:
    if res["trades"] < min_trades:
        return (-1.0, -1.0)
    pf = res["profit_factor"] if res["profit_factor"] is not None else 0.0
    return (pf, res["avg_r"])


def walk_forward(uni: dict[str, Any], start_ms: int, end_ms: int,
                 train_days: int, test_days: int, step_days: int,
                 equity0: float = 10_000.0,
                 min_train_trades: int = 4) -> dict[str, Any]:
    windows = []
    t0 = start_ms
    while True:
        train_end = t0 + train_days * DAY
        test_end = train_end + test_days * DAY
        if test_end > end_ms:
            break
        best, best_score = None, (-2.0, -2.0)
        for candidate in GRID:
            r = simulate_disp(uni, candidate, t0, train_end, equity0)
            s = score(r, min_train_trades)
            if s > best_score:
                best_score, best = s, (candidate, r)
        chosen = best[0] if best else GRID[0]
        oos = simulate_disp(uni, chosen, train_end, test_end, equity0)
        base = simulate_disp(uni, DispParams(), train_end, test_end, equity0)
        keep = ("trades", "win_rate", "avg_r", "profit_factor",
                "max_drawdown_pct", "total_return_pct", "funding_paid")
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
            "best_return_pct": round(max(rets), 2) if rets else 0.0,
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
    ap.add_argument("--lookback-stamps", type=int, default=3)
    ap.add_argument("--rebalance-days", type=int, default=7)
    ap.add_argument("--k", type=int, default=1)
    ap.add_argument("--universe", choices=["majors", "mid", "all"],
                    default="all")
    ap.add_argument("--min-spread-bp", type=float, default=1.0)
    ap.add_argument("--exposure-pct", type=float, default=100.0)
    ap.add_argument("--stop-atr", type=float, default=5.0)
    ap.add_argument("--max-hold-days", type=int, default=14)
    ap.add_argument("--placebo", type=int, default=0)
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
    symbols = {"majors": UNIVERSE, "mid": UNIVERSE_MID,
               "all": UNIVERSE_ALL}[args.universe]
    uni = load_universe(start_ms - args.warmup_days * DAY, end_ms, symbols)
    params = DispParams(lookback_stamps=args.lookback_stamps,
                        rebalance_days=args.rebalance_days, k=args.k,
                        min_spread_bp=args.min_spread_bp,
                        exposure_pct=args.exposure_pct,
                        stop_atr=args.stop_atr,
                        max_hold_days=args.max_hold_days)

    payload: dict[str, Any] = {
        "generated": datetime.now(timezone.utc).isoformat(),
        "strategy": "funding_dispersion",
        "mode": args.mode,
        "universe": symbols,
        "range": {"start": args.start, "end": args.end},
        "config": {"fee_bps": FEE_BPS, "slip_bps": SLIP_BPS,
                   "k": args.k, "default_params": params.label()},
        "results": [],
    }

    if args.mode == "single":
        t = time.time()
        res = simulate_disp(uni, params, start_ms, end_ms)
        res.update({"symbol": "FUND10", "timeframe": "1h", "params": params.label()})
        payload["results"].append(res)
        print(f"[res ] FUND10 trades={res['trades']} pf={res['profit_factor']} "
              f"ret={res['total_return_pct']}% dd={res['max_drawdown_pct']}% "
              f"fund={res['funding_paid']:.1f} ({time.time()-t:.0f}s)",
              file=sys.stderr)
        if args.placebo:
            rets = []
            for seed in range(1, args.placebo + 1):
                r = simulate_disp(uni, params, start_ms, end_ms, placebo_seed=seed)
                rets.append(float(r["total_return_pct"]))
            payload["placebo"] = {
                "seeds": args.placebo,
                "avg_return_pct": round(sum(rets) / len(rets), 2),
                "best_return_pct": round(max(rets), 2),
                "worst_return_pct": round(min(rets), 2),
                "positive": sum(1 for r in rets if r > 0),
            }
            print(f"[plac] n={args.placebo} avg={payload['placebo']['avg_return_pct']}% "
                  f"best={payload['placebo']['best_return_pct']}%", file=sys.stderr)
    else:
        t = time.time()
        wf = walk_forward(uni, start_ms, end_ms, args.train_days,
                          args.test_days, args.step_days)
        wf.update({"symbol": "FUND10", "timeframe": "1h",
                   "aggregate": aggregate(wf["windows"])})
        payload["results"].append(wf)
        agg = wf["aggregate"]["oos"]
        print(f"[wf  ] FUND10 windows={agg['windows']} trades={agg['trades']} "
              f"avg={agg['avg_return_pct']}% pos={agg['positive_windows']} "
              f"({time.time()-t:.0f}s)", file=sys.stderr)

    if args.json_out:
        Path(args.json_out).write_text(json.dumps(payload, indent=2))
    print(json.dumps({k: v for k, v in payload.items() if k != "results"}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
