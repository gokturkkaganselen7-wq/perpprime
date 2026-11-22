#!/usr/bin/env python3
"""Daily time-series momentum across the 10-symbol perp universe.

Different hypothesis from the 1h trend engine: crypto trend persistence at
daily/weekly horizons is a documented risk premium, and the low turnover
(weekly or monthly) keeps the fee bill small. Each symbol is traded on its
own trend, so the book can be net long, net short or flat.

Rules
  * daily bars; EMA fast/slow computed on closes
  * long when EMA_fast > EMA_slow and close > EMA_slow
  * short when EMA_fast < EMA_slow and close < EMA_slow
  * flat otherwise
  * rebalance weekly (7d) or monthly (30d); fill at the next open
  * equal notional per active symbol, gross <= 100% of equity
  * catastrophic stop at 5 ATR; funding per 8h stamp; 7.5 bps all-in cost

Usage:
  PYTHONPATH=src .venv/bin/python scripts/backtest_daily_trend.py \
      --mode single --start 2022-01-01 --end 2026-11-14
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
    atr_series,
    ema_series,
    fetch_funding,
    fetch_ohlcv,
    iso,
    parse_day,
)
from backtest_xsec import (  # noqa: E402
    BASE_SLIP_BPS,
    MID_SLIP_BPS,
    UNIVERSE,
    UNIVERSE_ALL,
    UNIVERSE_MID,
    _adv,
    _adv_at,
    dynamic_slip_bps,
)

HOUR = 3_600_000


@dataclass(frozen=True)
class DailyParams:
    fast: int = 50
    slow: int = 200
    rebalance_days: int = 7
    gross_pct: float = 100.0
    stop_atr: float = 5.0
    max_leg_pct: float = 20.0

    def label(self) -> str:
        return f"ma{self.fast}/{self.slow}/rb{self.rebalance_days}d"


GRID = [
    DailyParams(fast=f, slow=s, rebalance_days=rb)
    for f, s, rb in itertools.product([20, 50, 100], [100, 200], [7, 30])
]


def load_daily(symbols: list[str], start_ms: int, end_ms: int) -> dict[str, Any]:
    data = {}
    for sym in symbols:
        print(f"[data] {sym} 1d", file=sys.stderr)
        rows = fetch_ohlcv(sym, "1d", start_ms, end_ms)
        funding = fetch_funding(sym, start_ms, end_ms)
        closes = [r[4] for r in rows]
        data[sym] = {
            "rows": rows,
            "by_ts": {int(r[0]): r for r in rows},
            "atr": atr_series(rows, 14),
            "adv": _adv(rows, bars=1),
            "funding": _funding_by_day(rows, funding),
        }
    common = sorted(set.intersection(*(set(d["by_ts"]) for d in data.values())))
    return {"data": data, "index": common}


def _funding_by_day(rows: list[list[float]], funding: list[list[float]]) -> list[float]:
    out = [0.0] * len(rows)
    fi = 0
    for i, r in enumerate(rows):
        lo = int(r[0])
        hi = lo + DAY
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


def simulate_daily(uni: dict[str, Any], params: DailyParams,
                   start_ms: int, end_ms: int,
                   equity0: float = 10_000.0) -> dict[str, Any]:
    data, index = uni["data"], uni["index"]
    n = len(index)
    pos_of = {
        sym: {int(r[0]): i for i, r in enumerate(d["rows"])}
        for sym, d in data.items()
    }
    mas = {}
    for sym, d in data.items():
        closes = [r[4] for r in d["rows"]]
        mas[sym] = {"fast": ema_series(closes, params.fast),
                    "slow": ema_series(closes, params.slow)}
    rebal = params.rebalance_days

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
            "bars_held": int((ts - p["entry_ts"]) / DAY),
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
        fi = pos_of[sym].get(index[i])
        atr = data[sym]["atr"][fi] if fi is not None else None
        stop = None
        if atr and atr > 0:
            stop = entry - params.stop_atr * atr if side == "long" \
                else entry + params.stop_atr * atr
        fee = notional * FEE_BPS / 10_000
        equity -= fee
        positions[sym] = {
            "symbol": sym, "side": side, "entry": entry,
            "qty": notional / entry, "notional": notional, "stop": stop,
            "entry_ts": int(index[i]), "entry_fee": fee, "funding": 0.0,
            "slip_bps": slip,
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

            target_n = max(1, len(pending_open) + len(positions))
            for sym, side in pending_open.items():
                if sym not in positions:
                    per_leg = min(equity * params.gross_pct / 100.0 / target_n,
                                  equity * params.max_leg_pct / 100.0)
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
                _unreal(p, bar(sym, i)[4]) + p["funding"]
                for sym, p in positions.items())
            worst = equity + sum(
                _unreal(p, _adverse(p, bar(sym, i))) + p["funding"]
                for sym, p in positions.items())
            worst = max(worst, 0.0)
            peak = max(peak, close_mark)
            if peak > 0:
                max_dd = max(max_dd, (peak - worst) / peak * 100.0)
            curve.append([int(ts), round(close_mark, 2), round(worst, 2)])

        if i % rebal or ts < start_ms or i + 1 > end_i or ts >= end_ms:
            continue

        target = _target(data, mas, pos_of, index, i, params)
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


def _target(data: dict[str, Any],
            mas: dict[str, dict[str, list[float | None]]],
            pos_of: dict[str, dict[int, int]],
            index: list[int], i: int, params: DailyParams) -> dict[str, str]:
    out = {}
    ts = index[i]
    for sym, m in mas.items():
        p = pos_of[sym].get(ts)
        if p is None:
            continue
        fast = m["fast"][p]
        slow = m["slow"][p]
        close = data[sym]["rows"][p][4]
        if fast is None or slow is None:
            continue
        if fast > slow and close > slow:
            out[sym] = "long"
        elif fast < slow and close < slow:
            out[sym] = "short"
    return out


def _unreal(p: dict[str, Any], price: float) -> float:
    return (price - p["entry"]) * p["qty"] if p["side"] == "long" \
        else (p["entry"] - price) * p["qty"]


def _adverse(p: dict[str, Any], r: list[float]) -> float:
    if p["stop"] is not None:
        return max(r[3], p["stop"]) if p["side"] == "long" \
            else min(r[2], p["stop"])
    return r[3] if p["side"] == "long" else r[2]


def score(res: dict[str, Any], min_trades: int) -> tuple[float, float]:
    if res["trades"] < min_trades:
        return (-1.0, -1.0)
    pf = res["profit_factor"] if res["profit_factor"] is not None else 0.0
    return (pf, res["avg_r"])


def walk_forward(uni: dict[str, Any], start_ms: int, end_ms: int,
                 train_days: int, test_days: int, step_days: int,
                 equity0: float = 10_000.0,
                 min_train_trades: int = 10) -> dict[str, Any]:
    windows = []
    t0 = start_ms
    while True:
        train_end = t0 + train_days * DAY
        test_end = train_end + test_days * DAY
        if test_end > end_ms:
            break
        best, best_score = None, (-2.0, -2.0)
        for candidate in GRID:
            r = simulate_daily(uni, candidate, t0, train_end, equity0)
            s = score(r, min_train_trades)
            if s > best_score:
                best_score, best = s, (candidate, r)
        chosen = best[0] if best else GRID[0]
        oos = simulate_daily(uni, chosen, train_end, test_end, equity0)
        base = simulate_daily(uni, DailyParams(), train_end, test_end, equity0)
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
    ap.add_argument("--warmup-days", type=int, default=300)
    ap.add_argument("--train-days", type=int, default=365)
    ap.add_argument("--test-days", type=int, default=90)
    ap.add_argument("--step-days", type=int, default=90)
    ap.add_argument("--fast", type=int, default=50)
    ap.add_argument("--slow", type=int, default=200)
    ap.add_argument("--rebalance-days", type=int, default=7)
    ap.add_argument("--universe", choices=["majors", "mid", "all"],
                    default="all")
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
    uni = load_daily(symbols, start_ms - args.warmup_days * DAY, end_ms)
    params = DailyParams(fast=args.fast, slow=args.slow,
                         rebalance_days=args.rebalance_days)

    payload: dict[str, Any] = {
        "generated": datetime.now(timezone.utc).isoformat(),
        "strategy": "daily_trend",
        "mode": args.mode,
        "universe": symbols,
        "range": {"start": args.start, "end": args.end},
        "config": {"fee_bps": FEE_BPS, "slip_bps": SLIP_BPS,
                   "default_params": params.label()},
        "results": [],
    }

    if args.mode == "single":
        t = time.time()
        res = simulate_daily(uni, params, start_ms, end_ms)
        res.update({"symbol": "TREND10", "timeframe": "1d",
                    "params": params.label()})
        payload["results"].append(res)
        print(f"[res ] TREND10 trades={res['trades']} pf={res['profit_factor']} "
              f"ret={res['total_return_pct']}% dd={res['max_drawdown_pct']}% "
              f"fund={res['funding_paid']:.1f} ({time.time()-t:.0f}s)",
              file=sys.stderr)
    else:
        t = time.time()
        wf = walk_forward(uni, start_ms, end_ms, args.train_days,
                          args.test_days, args.step_days)
        wf.update({"symbol": "TREND10", "timeframe": "1d",
                   "aggregate": aggregate(wf["windows"])})
        payload["results"].append(wf)
        agg = wf["aggregate"]["oos"]
        print(f"[wf  ] TREND10 windows={agg['windows']} trades={agg['trades']} "
              f"avg={agg['avg_return_pct']}% pos={agg['positive_windows']} "
              f"({time.time()-t:.0f}s)", file=sys.stderr)

    if args.json_out:
        Path(args.json_out).write_text(json.dumps(payload, indent=2))
    print(json.dumps({k: v for k, v in payload.items() if k != "results"}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
