#!/usr/bin/env python3
"""Spot-perp basis carry: long spot, short perp, collect funding.

This is the delta-neutral version of carry that ``cfos-funding-carry``
describes. It is a different risk profile from the single-leg funding test in
``backtest_edges.py``: the directional move of the two legs cancels, so the
P&L is funding minus costs plus any basis convergence.

Rules
  * signal on a CLOSED 1h bar: annualised 8h funding (rate * 3 * 365 * 100)
  * enter at the NEXT bar open on both legs when the annualised funding is
    at or above ``entry_ann``
  * exit at the next open when funding falls to ``exit_ann`` or after
    ``max_hold_days``
  * costs: 5.5 bps taker + 2 bps slippage per fill, on both legs, four fills
    per round trip
  * funding is credited every 8h stamp while the perp short is open
  * no leverage on either leg: spot notional and perp margin each use
    ``exposure_pct`` of equity

Usage:
  PYTHONPATH=src .venv/bin/python scripts/backtest_basis.py --mode single \
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
    SLIP_BPS,
    _metrics,
    fetch_funding,
    fetch_ohlcv,
    iso,
    parse_day,
)

HOUR = 3_600_000
# Bybit's market metadata: swap taker 6 bps (the repo uses 5.5), spot taker
# 10 bps. Modelling one fee for both legs understated the real cost.
SPOT_FEE_BPS = 10.0


@dataclass(frozen=True)
class BasisParams:
    entry_ann: float = 10.0      # percent per year
    exit_ann: float = 2.0
    max_hold_days: int = 30
    exposure_pct: float = 50.0   # per leg, percent of equity

    def label(self) -> str:
        return (f"entry{self.entry_ann:g}/exit{self.exit_ann:g}/"
                f"hold{self.max_hold_days}")


GRID = [
    BasisParams(entry_ann=e, exit_ann=x, max_hold_days=h)
    for e, x, h in itertools.product([4.0, 6.0, 8.0, 10.0], [0.0, 2.0], [14, 30])
]


def spot_symbol(perp: str) -> str:
    return perp.split(":")[0]


def funding_by_bar(rows: list[list[float]], funding: list[list[float]]) -> list[float]:
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


def load_pair(perp: str, start_ms: int, end_ms: int) -> dict[str, Any]:
    spot = spot_symbol(perp)
    p_rows = fetch_ohlcv(perp, "1h", start_ms, end_ms)
    s_rows = fetch_ohlcv(spot, "1h", start_ms, end_ms)
    funding = fetch_funding(perp, start_ms, end_ms)
    s_map = {int(r[0]): r for r in s_rows}
    rows = [r for r in p_rows if int(r[0]) in s_map]
    spot_rows = [s_map[int(r[0])] for r in rows]
    return {
        "symbol": perp,
        "rows": rows,
        "spot": spot_rows,
        "funding_by_bar": funding_by_bar(rows, funding),
    }


def simulate_basis(data: dict[str, Any], params: BasisParams,
                   start_ms: int, end_ms: int,
                   equity0: float = 10_000.0) -> dict[str, Any]:
    rows, spot_rows = data["rows"], data["spot"]
    fbb = data["funding_by_bar"]

    equity = equity0
    curve: list[list[float]] = []
    trades: list[dict[str, Any]] = []
    pos: dict[str, Any] | None = None
    pending: str | None = None
    peak = equity0
    max_dd = 0.0

    def leg_fill(price: float, buying: bool) -> float:
        f = 1.0 + SLIP_BPS / 10_000 if buying else 1.0 - SLIP_BPS / 10_000
        return price * f

    def mark_with(spot_px: float, perp_px: float) -> float:
        assert pos is not None
        spot_pnl = (spot_px - pos["spot_entry"]) * pos["spot_qty"]
        perp_pnl = (pos["perp_entry"] - perp_px) * pos["perp_qty"]
        return equity + spot_pnl + perp_pnl - pos["entry_fee"] + pos["funding"]

    def close_at(ts: int, spot_open: float, perp_open: float, reason: str) -> None:
        nonlocal equity, pos, pending
        assert pos is not None
        spot_fill = leg_fill(spot_open, buying=False)
        perp_fill = leg_fill(perp_open, buying=True)   # buy back the short
        spot_pnl = (spot_fill - pos["spot_entry"]) * pos["spot_qty"]
        perp_pnl = (pos["perp_entry"] - perp_fill) * pos["perp_qty"]
        exit_fee = (spot_fill * pos["spot_qty"] * SPOT_FEE_BPS
                    + perp_fill * pos["perp_qty"] * FEE_BPS) / 10_000
        pnl = spot_pnl + perp_pnl + pos["funding"] - pos["entry_fee"] - exit_fee
        equity += pnl
        fees = pos["entry_fee"] + exit_fee
        trades.append({
            "side": "basis",
            "entry_ts": iso(pos["entry_ts"]), "exit_ts": iso(ts),
            "entry": round(pos["perp_entry"], 4),
            "spot_entry": round(pos["spot_entry"], 4),
            "exit": round(perp_fill, 4),
            "pnl": round(pnl, 4),
            "fees": round(fees, 4),
            "funding": round(pos["funding"], 4),
            "r": round(pnl / (2 * pos["notional"]), 4),
            "exit_reason": reason,
            "partials": 1,
            "bars_held": int((ts - pos["entry_ts"]) / HOUR),
        })
        pos = None
        pending = None

    for i in range(1, len(rows)):
        ts, po, ph, pl, pc, _v = rows[i]
        _ts, so, sh, sl, sc, _sv = spot_rows[i]

        if pos is not None and ts > start_ms:
            # funding accrual for the short perp leg
            rate = fbb[i]
            if rate:
                pos["funding"] += rate * pos["perp_qty"] * pc

            if pending:
                close_at(ts, so, po, pending)

            if pos is not None:
                ann = _ann(fbb, i)
                if ann <= params.exit_ann:
                    pending = "funding_done"
                elif i - pos["entry_index"] >= params.max_hold_days * 24:
                    pending = "max_hold"

            if ts >= start_ms:
                if pos is not None:
                    worst = mark_with(sl, ph)      # spot down while perp up
                    close_mark = mark_with(sc, pc)
                else:
                    # The position closed at this bar's open; the curve is
                    # already at the realised equity.
                    worst = close_mark = equity
                peak = max(peak, close_mark)
                if peak > 0:
                    max_dd = max(max_dd, (peak - worst) / peak * 100.0)
                curve.append([int(ts), round(close_mark, 2), round(worst, 2)])
        elif ts >= start_ms:
            peak = max(peak, equity)
            curve.append([int(ts), round(equity, 2), round(equity, 2)])

        if pos is not None or pending or ts < start_ms or ts >= end_ms:
            continue
        if i + 1 >= len(rows):
            continue

        ann = _ann(fbb, i)
        if ann < params.entry_ann:
            continue

        # enter at the next open on both legs
        next_ts, npo, _nph, _npl, _npc, _nv = rows[i + 1]
        _t2, nso, _nsh, _nsl, _nsc, _nsv = spot_rows[i + 1]
        notional = equity * params.exposure_pct / 100.0
        spot_entry = leg_fill(nso, buying=True)
        perp_entry = leg_fill(npo, buying=False)
        if spot_entry <= 0 or perp_entry <= 0:
            continue
        spot_qty = notional / spot_entry
        perp_qty = notional / perp_entry
        entry_fee = (spot_entry * spot_qty * SPOT_FEE_BPS
                     + perp_entry * perp_qty * FEE_BPS) / 10_000
        pos = {
            "entry_ts": int(next_ts), "entry_index": i + 1,
            "spot_entry": spot_entry, "perp_entry": perp_entry,
            "spot_qty": spot_qty, "perp_qty": perp_qty,
            "notional": notional, "entry_fee": entry_fee, "funding": 0.0,
        }
        pending = None

    if pos is not None:
        close_at(int(rows[-1][0]), spot_rows[-1][1], rows[-1][1], "window_end")

    return _metrics(trades, equity0, equity, max_dd=max_dd, curve=curve)


def _ann(fbb: list[float], i: int) -> float:
    """Last non-zero 8h funding rate, annualised in percent."""
    for j in range(i, max(-1, i - 24), -1):
        if fbb[j]:
            return fbb[j] * 3 * 365 * 100.0
    return 0.0


def score(res: dict[str, Any], min_trades: int) -> tuple[float, float]:
    if res["trades"] < min_trades:
        return (-1.0, -1.0)
    pf = res["profit_factor"] if res["profit_factor"] is not None else 0.0
    return (pf, res["avg_r"])


def walk_forward(data: dict[str, Any], start_ms: int, end_ms: int,
                 train_days: int, test_days: int, step_days: int,
                 equity0: float = 10_000.0,
                 min_train_trades: int = 5) -> dict[str, Any]:
    windows: list[dict[str, Any]] = []
    t0 = start_ms
    while True:
        train_end = t0 + train_days * DAY
        test_end = train_end + test_days * DAY
        if test_end > end_ms:
            break
        best = None
        best_score = (-2.0, -2.0)
        for candidate in GRID:
            r = simulate_basis(data, candidate, t0, train_end, equity0)
            s = score(r, min_train_trades)
            if s > best_score:
                best_score, best = s, (candidate, r)
        chosen = best[0] if best else GRID[0]
        oos = simulate_basis(data, chosen, train_end, test_end, equity0)
        base = simulate_basis(data, BasisParams(), train_end, test_end, equity0)
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
            "best_return_pct": round(max(rets), 2) if rets else 0.0,
            "worst_dd_pct": round(max(r["max_drawdown_pct"] for r in rows), 2)
            if rows else 0.0,
        }
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["single", "walkforward"], default="single")
    ap.add_argument("--symbols", nargs="+",
                    default=["BTC/USDT:USDT", "ETH/USDT:USDT", "SOL/USDT:USDT"])
    ap.add_argument("--start", required=True)
    ap.add_argument("--end", required=True)
    ap.add_argument("--warmup-days", type=int, default=10)
    ap.add_argument("--train-days", type=int, default=90)
    ap.add_argument("--test-days", type=int, default=30)
    ap.add_argument("--step-days", type=int, default=30)
    ap.add_argument("--json-out", default="")
    ap.add_argument("--fee-bps", type=float, default=None,
                    help="override perp taker fee for sensitivity runs")
    ap.add_argument("--spot-fee-bps", type=float, default=None,
                    help="override spot taker fee for sensitivity runs")
    ap.add_argument("--slip-bps", type=float, default=None)
    args = ap.parse_args()

    if args.fee_bps is not None or args.slip_bps is not None \
            or args.spot_fee_bps is not None:
        import backtest_trend as bt
        if args.fee_bps is not None:
            globals()["FEE_BPS"] = args.fee_bps
            bt.FEE_BPS = args.fee_bps
        if args.spot_fee_bps is not None:
            globals()["SPOT_FEE_BPS"] = args.spot_fee_bps
        if args.slip_bps is not None:
            globals()["SLIP_BPS"] = args.slip_bps
            bt.SLIP_BPS = args.slip_bps

    start_ms = parse_day(args.start)
    end_ms = parse_day(args.end)
    fetch_start = start_ms - args.warmup_days * DAY

    payload: dict[str, Any] = {
        "generated": datetime.now(timezone.utc).isoformat(),
        "strategy": "basis",
        "mode": args.mode,
        "range": {"start": args.start, "end": args.end},
        "config": {"fee_bps": FEE_BPS, "slip_bps": SLIP_BPS,
                   "warmup_days": args.warmup_days,
                   "default_params": BasisParams().label()},
        "results": [],
    }

    for symbol in args.symbols:
        t = time.time()
        print(f"[data] {symbol} + spot {args.start}..{args.end}", file=sys.stderr)
        data = load_pair(symbol, fetch_start, end_ms)
        print(f"[prep] aligned bars={len(data['rows'])}", file=sys.stderr)
        if args.mode == "single":
            res = simulate_basis(data, BasisParams(), start_ms, end_ms)
            res.update({"symbol": symbol, "timeframe": "1h",
                        "params": BasisParams().label()})
            payload["results"].append(res)
            print(f"[res ] {symbol} trades={res['trades']} "
                  f"pf={res['profit_factor']} ret={res['total_return_pct']}% "
                  f"dd={res['max_drawdown_pct']}% ({time.time()-t:.0f}s)",
                  file=sys.stderr)
        else:
            wf = walk_forward(data, start_ms, end_ms, args.train_days,
                              args.test_days, args.step_days)
            wf.update({"symbol": symbol, "timeframe": "1h",
                       "aggregate": aggregate(wf["windows"])})
            payload["results"].append(wf)
            agg = wf["aggregate"]["oos"]
            print(f"[wf  ] {symbol} windows={agg['windows']} "
                  f"trades={agg['trades']} avg={agg['avg_return_pct']}% "
                  f"({time.time()-t:.0f}s)", file=sys.stderr)

    if args.json_out:
        Path(args.json_out).write_text(json.dumps(payload, indent=2))
    print(json.dumps({k: v for k, v in payload.items() if k != "results"}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
