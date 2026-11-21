#!/usr/bin/env python3
"""CFOS trend backtest engine — funding-aware, walk-forward capable.

Rules implemented (skills/cfos-trend + skills/cfos-exit-management):
  regime   4h CFOS regime, only trend_up / trend_down
  entry    pullback: close crosses back over EMA20 after dipping below it
  stop     last 10-bar swing +/- stop_atr * ATR
  TP1      tp1_r * R, 50% off, stop -> breakeven
  TP2      measured move (20-bar range), clamped to [tp2_min_r, tp2_max_r]
  exits    stop, TP2, regime flip, 4h EMA50 break, time stop, window end

Costs: taker 5.5 bps + 2 bps slippage per fill, plus real Bybit funding
charged/credited on every 8h stamp while a position is open.

Honesty rules:
  * signal on a CLOSED bar, fill at the NEXT bar open
  * if stop and target are both inside one bar, the stop is assumed first
  * funding is signed correctly: longs pay positive funding, shorts receive it
  * walk-forward: parameters are picked on train, scored on the next window only

Usage:
  PYTHONPATH=src .venv/bin/python scripts/backtest_trend.py --mode single \
      --symbols BTC/USDT:USDT --timeframes 1h --start 2026-10-15 --end 2026-11-14
  PYTHONPATH=src .venv/bin/python scripts/backtest_trend.py --mode walkforward \
      --symbols BTC/USDT:USDT ETH/USDT:USDT SOL/USDT:USDT --timeframes 1h \
      --start 2025-11-01 --end 2026-11-14 --train-days 90 --test-days 30 --step-days 30
"""

from __future__ import annotations

import argparse
import itertools
import json
import sys
import time
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from cfos.analysis import atr, compute_regime  # noqa: E402

FEE_BPS = 5.5
SLIP_BPS = 2.0
# Maintenance margin used for the liquidation model. Bybit's tier-1 MMR is
# around 0.5% for BTC/ETH perps; using one value for all three symbols is a
# documented approximation, not a venue-exact schedule.
MMR = 0.005
LIQ_FEE_BPS = 55.0
FOUR_H = 4 * 60 * 60 * 1000
DAY = 86_400_000
CACHE = Path("/tmp/cfos-backtest-cache")
TF_MS = {"15m": 900_000, "1h": 3_600_000, "4h": FOUR_H}


# --------------------------------------------------------------------------- #
# data
# --------------------------------------------------------------------------- #
def _cache_path(kind: str, symbol: str, tf: str, start: int, end: int) -> Path:
    tag = symbol.replace("/", "_").replace(":", "_")
    return CACHE / f"{kind}_{tag}_{tf}_{start}_{end}.json"


def _paginate(fetch, since: int, end: int, step: int, limit: int = 1000) -> list:
    rows: list = []
    guard = 0
    while since < end and guard < 200:
        guard += 1
        batch = fetch(since)
        if not batch:
            break
        rows.extend(batch)
        nxt = int(batch[-1][0]) + step
        if nxt <= since:
            break
        since = nxt
    return rows


def _exchange():
    import ccxt

    ex = ccxt.bybit({"enableRateLimit": True, "options": {"defaultType": "swap"}})
    ex.load_markets()
    return ex


def fetch_ohlcv(symbol: str, tf: str, start_ms: int, end_ms: int) -> list[list[float]]:
    CACHE.mkdir(parents=True, exist_ok=True)
    path = _cache_path("ohlcv", symbol, tf, start_ms, end_ms)
    if path.exists():
        return json.loads(path.read_text())

    ex = _exchange()
    step = TF_MS[tf]
    raw = _paginate(lambda s: ex.fetch_ohlcv(symbol, tf, since=s, limit=1000),
                    start_ms, end_ms, step)

    seen: set[int] = set()
    clean: list[list[float]] = []
    for row in raw:
        ts = int(row[0])
        if ts in seen or ts > end_ms:
            continue
        seen.add(ts)
        clean.append([ts, float(row[1]), float(row[2]), float(row[3]),
                      float(row[4]), float(row[5])])
    clean.sort(key=lambda r: r[0])
    path.write_text(json.dumps(clean))
    return clean


def fetch_funding(symbol: str, start_ms: int, end_ms: int) -> list[list[float]]:
    CACHE.mkdir(parents=True, exist_ok=True)
    path = _cache_path("funding", symbol, "8h", start_ms, end_ms)
    if path.exists():
        return json.loads(path.read_text())

    ex = _exchange()
    # Bybit caps this endpoint at 200 records per call and returns the NEWEST
    # page inside [since, until]. Walk backwards with an explicit cursor so a
    # long range is never silently truncated.
    raw: list = []
    cursor = end_ms
    guard = 0
    while cursor > start_ms and guard < 100:
        guard += 1
        batch = ex.fetch_funding_rate_history(
            symbol, since=start_ms, limit=200, params={"until": cursor})
        if not batch:
            break
        raw.extend(batch)
        oldest = min(int(r["timestamp"]) for r in batch)
        if oldest <= start_ms:
            break
        cursor = oldest - 1
    clean = sorted(
        [[int(r["timestamp"]), float(r["fundingRate"])] for r in raw
         if r.get("timestamp") and r.get("fundingRate") is not None],
        key=lambda r: r[0],
    )
    path.write_text(json.dumps(clean))
    return clean


def resample(rows: list[list[float]], target_ms: int) -> list[list[float]]:
    buckets: dict[int, list[float]] = {}
    for ts, o, h, l, c, v in rows:
        key = (int(ts) // target_ms) * target_ms
        cur = buckets.get(key)
        if cur is None:
            buckets[key] = [key, o, h, l, c, v]
        else:
            cur[2] = max(cur[2], h)
            cur[3] = min(cur[3], l)
            cur[4] = c
            cur[5] += v
    return [buckets[k] for k in sorted(buckets)]


# --------------------------------------------------------------------------- #
# indicators
# --------------------------------------------------------------------------- #
def ema_series(values: list[float], period: int) -> list[float | None]:
    out: list[float | None] = [None] * len(values)
    if len(values) < period:
        return out
    seed = sum(values[:period]) / period
    out[period - 1] = seed
    k = 2.0 / (period + 1)
    for i in range(period, len(values)):
        seed = (values[i] - seed) * k + seed
        out[i] = seed
    return out


def atr_series(rows: list[list[float]], period: int = 14) -> list[float | None]:
    out: list[float | None] = [None] * len(rows)
    for i in range(period, len(rows)):
        out[i] = atr(rows[i - period:i + 1], period)
    return out


def prepare(symbol: str, tf: str, rows: list[list[float]],
            funding: list[list[float]]) -> dict[str, Any]:
    """Everything expensive, computed once and reused by every simulation."""
    h4 = resample(rows, FOUR_H)
    h4_start = h4[0][0]
    closes4 = [r[4] for r in h4]
    ema50_4 = ema_series(closes4, 50)

    regime_at: list[dict[str, Any]] = []
    for i in range(len(h4)):
        if i + 1 < 200:
            regime_at.append({"regime": "warmup", "ema50": None})
            continue
        rep = compute_regime(h4[: i + 1], symbol=symbol, timeframe="4h")
        regime_at.append({"regime": rep.regime, "ema50": ema50_4[i]})

    closes = [r[4] for r in rows]
    e20 = ema_series(closes, 20)
    e50 = ema_series(closes, 50)
    a14 = atr_series(rows, 14)

    # funding event -> the bar whose interval (prev_ts, ts] contains it
    step = TF_MS[tf]
    funding_by_bar: list[float] = [0.0] * len(rows)
    if funding:
        fi = 0
        for i, r in enumerate(rows):
            lo = int(r[0])
            hi = lo + step
            total = 0.0
            while fi < len(funding) and funding[fi][0] <= lo:
                fi += 1
            j = fi
            while j < len(funding) and funding[j][0] <= hi:
                total += funding[j][1]
                j += 1
            funding_by_bar[i] = total
            fi = j

    return {"symbol": symbol, "tf": tf, "rows": rows, "h4_start": h4_start,
            "regime_at": regime_at, "closes": closes, "e20": e20, "e50": e50,
            "a14": a14, "funding_by_bar": funding_by_bar}


# --------------------------------------------------------------------------- #
# simulation
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Params:
    stop_atr: float = 0.5
    tp1_r: float = 2.0
    tp2_min_r: float = 3.0
    tp2_max_r: float = 8.0
    require_pullback: bool = True
    require_regime: bool = True
    time_stop_days: float = 2.0
    risk_pct: float = 0.5
    max_leverage: float = 3.0

    def label(self) -> str:
        return (f"stop{self.stop_atr}/tp1_{self.tp1_r}/"
                f"pb{int(self.require_pullback)}/rg{int(self.require_regime)}/"
                f"ts{self.time_stop_days}")


@dataclass
class Position:
    side: str
    entry_ts: int
    entry: float
    stop: float
    tp1: float
    tp2: float
    qty: float
    risk_amount: float
    risk_dist: float
    initial_qty: float
    entry_index: int
    tp1_done: bool = False
    fees: float = 0.0
    funding: float = 0.0
    realized: float = 0.0
    exits: list[dict[str, Any]] = field(default_factory=list)
    leverage: float = 0.0
    liq_price: float | None = None


def slip(price: float, side: str, entering: bool) -> float:
    buying = (side == "long") == entering
    f = 1.0 + SLIP_BPS / 10_000 if buying else 1.0 - SLIP_BPS / 10_000
    return price * f


def liquidation_price(side: str, entry: float, leverage: float,
                      mmr: float = MMR) -> float | None:
    """Approximate isolated-margin liquidation price.

    With effective leverage at or below 1x the position cannot be liquidated
    by price alone, so the function returns ``None``. Above 1x the standard
    maintenance-margin formula is used.
    """
    if leverage <= 1.0:
        return None
    if side == "long":
        return entry * (1 - 1 / leverage + mmr)
    return entry * (1 + 1 / leverage - mmr)


def simulate(prep: dict[str, Any], params: Params,
             start_ms: int, end_ms: int, equity0: float = 10_000.0) -> dict[str, Any]:
    rows = prep["rows"]
    e20, e50, a14 = prep["e20"], prep["e50"], prep["a14"]
    closes = prep["closes"]
    regime_at, h4_start = prep["regime_at"], prep["h4_start"]
    fbb = prep["funding_by_bar"]
    step = TF_MS[prep["tf"]]
    time_stop_bars = int(params.time_stop_days * DAY / step)

    equity = equity0
    trades: list[dict[str, Any]] = []
    pos: Position | None = None
    pending_exit: str | None = None
    peak_equity = equity0
    max_dd = 0.0
    same_bar_tp1_be = 0
    ruined = False
    curve: list[list[float]] = []

    def unrealized(side: str, entry: float, price: float, qty: float) -> float:
        return (price - entry) * qty if side == "long" else (entry - price) * qty

    def close_position(px: float, ts: int, qty: float, reason: str,
                       extra_fee_bps: float = 0.0) -> None:
        nonlocal equity, pos, pending_exit, ruined
        assert pos is not None
        fill = slip(px, pos.side, entering=False)
        gross = (fill - pos.entry) * qty if pos.side == "long" else (pos.entry - fill) * qty
        fee = fill * qty * (FEE_BPS + extra_fee_bps) / 10_000
        pos.realized += gross - fee
        pos.fees += fee
        pos.exits.append({"ts": ts, "price": round(fill, 4), "qty": round(qty, 6),
                          "reason": reason})
        if qty >= pos.qty - 1e-12:
            equity += pos.realized
            if equity <= 0:
                # A leveraged gap can lose more than the account holds. Stop
                # at zero: no venue lets you keep trading a wiped account.
                equity = 0.0
                ruined = True
            trades.append({
                "side": pos.side,
                "entry_ts": iso(pos.entry_ts), "exit_ts": iso(ts),
                "entry": round(pos.entry, 4),
                "pnl": round(pos.realized, 4),
                "fees": round(pos.fees, 4),
                "funding": round(pos.funding, 4),
                "r": round(pos.realized / pos.risk_amount, 3) if pos.risk_amount else 0.0,
                "exit_reason": pos.exits[-1]["reason"],
                "partials": len(pos.exits),
                "bars_held": int((ts - pos.entry_ts) / step),
            })
            pos = None
            pending_exit = None

    for i in range(1, len(rows)):
        if ruined:
            break
        ts, o, h, l, c, _v = rows[i]
        equity_at_start = equity
        had_pos = pos is not None and ts > start_ms
        side_start = ""
        entry_start = stop_start = qty_start = realized_start = 0.0

        if had_pos:
            assert pos is not None
            side_start = pos.side
            entry_start = pos.entry
            stop_start = pos.stop

            # funding on every 8h stamp inside this bar
            rate = fbb[i]
            if rate:
                sign = 1.0 if pos.side == "long" else -1.0
                pay = sign * pos.qty * c * rate
                pos.realized -= pay
                pos.funding -= pay
            qty_start = pos.qty
            realized_start = pos.realized

            # A gap through the liquidation level is the one case where a
            # working stop cannot save the position: the venue liquidates at
            # the open before any stop can fill at its own price.
            liq_hit = False
            if pos.liq_price is not None:
                liq_hit = (o <= pos.liq_price) if pos.side == "long" \
                    else (o >= pos.liq_price)
            if liq_hit:
                close_position(o, ts, pos.qty, "liquidation", LIQ_FEE_BPS)
            else:
                if pending_exit:
                    close_position(o, ts, pos.qty, pending_exit)
                    pending_exit = None

                if pos is not None:
                    stop_hit = (l <= pos.stop) if pos.side == "long" else (h >= pos.stop)
                    if stop_hit:
                        # A bar that opens through the stop fills at the open,
                        # not at the stop: gap risk is real and always adverse.
                        if pos.side == "long":
                            fill_px, gapped = min(o, pos.stop), o < pos.stop
                        else:
                            fill_px, gapped = max(o, pos.stop), o > pos.stop
                        close_position(fill_px, ts, pos.qty,
                                       "stop_gap" if gapped else "stop")
                    elif not pos.tp1_done:
                        tp1_hit = (h >= pos.tp1) if pos.side == "long" \
                            else (l <= pos.tp1)
                        if tp1_hit:
                            half = pos.qty / 2.0
                            close_position(pos.tp1, ts, half, "tp1")
                            if pos is not None:
                                pos.qty -= half
                                pos.tp1_done = True
                                pos.stop = pos.entry
                                be_hit = (l <= pos.stop) if pos.side == "long" \
                                    else (h >= pos.stop)
                                if be_hit:
                                    # OHLC cannot prove whether the low came
                                    # before or after TP1. Keep the stop-first
                                    # convention (close the rest at breakeven)
                                    # and count the bar so the assumption is
                                    # visible in the report.
                                    same_bar_tp1_be += 1
                                    close_position(pos.stop, ts, pos.qty,
                                                   "breakeven")
                    else:
                        tp2_hit = (h >= pos.tp2) if pos.side == "long" \
                            else (l <= pos.tp2)
                        if tp2_hit:
                            close_position(pos.tp2, ts, pos.qty, "tp2")

                    if pos is not None:
                        idx = (((int(ts) // FOUR_H) * FOUR_H - FOUR_H)
                               - h4_start) // FOUR_H
                        reg = regime_at[idx] if 0 <= idx < len(regime_at) else {
                            "regime": "warmup", "ema50": None}
                        want = "trend_up" if pos.side == "long" else "trend_down"
                        if params.require_regime and reg["regime"] not in (want, "warmup"):
                            pending_exit = "regime_flip"
                        elif reg["ema50"] is not None and (
                            (pos.side == "long" and c < reg["ema50"])
                            or (pos.side == "short" and c > reg["ema50"])
                        ):
                            pending_exit = "ema50_break"
                        elif i - pos.entry_index >= time_stop_bars:
                            pending_exit = "time_stop"

        # Intrabar drawdown: mark the worst plausible point of the bar, not
        # just the closed-trade equity. The pre-exit mark uses the position as
        # it stood at the bar's open (bounded by its stop); the post-exit mark
        # uses the remaining size and the stop that is live after any TP1.
        if ts >= start_ms:
            if had_pos:
                adverse_pre = l if side_start == "long" else h
                adverse_pre = (max(adverse_pre, stop_start) if side_start == "long"
                               else min(adverse_pre, stop_start))
                pre_mark = (equity_at_start + realized_start
                            + unrealized(side_start, entry_start, adverse_pre, qty_start))
                if pos is not None:
                    adverse_post = l if pos.side == "long" else h
                    adverse_post = (max(adverse_post, pos.stop) if pos.side == "long"
                                    else min(adverse_post, pos.stop))
                    post_mark = (equity_at_start + pos.realized
                                 + unrealized(pos.side, pos.entry, adverse_post, pos.qty))
                    worst = min(pre_mark, post_mark)
                    close_mark = (equity_at_start + pos.realized
                                  + unrealized(pos.side, pos.entry, c, pos.qty))
                else:
                    worst = min(pre_mark, equity)
                    close_mark = equity
                worst = max(worst, 0.0)
                peak_equity = max(peak_equity, close_mark)
                if peak_equity > 0:
                    max_dd = max(max_dd, (peak_equity - worst) / peak_equity * 100.0)
                curve.append([int(ts), round(close_mark, 2), round(worst, 2)])
            else:
                peak_equity = max(peak_equity, equity)
                curve.append([int(ts), round(equity, 2), round(equity, 2)])

        # ---- new signal ---------------------------------------------------- #
        if ruined or pos is not None or ts < start_ms or ts >= end_ms:
            continue
        if None in (e20[i], e50[i], a14[i], e20[i - 1]):
            continue

        idx = (((int(ts) // FOUR_H) * FOUR_H - FOUR_H) - h4_start) // FOUR_H
        if not (0 <= idx < len(regime_at)):
            continue
        reg = regime_at[idx]
        if params.require_regime and reg["regime"] not in ("trend_up", "trend_down"):
            continue

        atr_i = a14[i]
        swing_low = min(r[3] for r in rows[max(0, i - 9): i + 1])
        swing_high = max(r[2] for r in rows[max(0, i - 9): i + 1])

        up = reg["regime"] == "trend_up" if params.require_regime else e20[i] > e50[i]
        down = reg["regime"] == "trend_down" if params.require_regime else e20[i] < e50[i]
        pb_long = (closes[i - 1] <= e20[i - 1]) if params.require_pullback else True
        pb_short = (closes[i - 1] >= e20[i - 1]) if params.require_pullback else True

        side = None
        stop = None
        if up and c > e20[i] and c > o and pb_long and (c - e20[i]) <= 1.5 * atr_i:
            side, stop = "long", swing_low - params.stop_atr * atr_i
        elif down and c < e20[i] and c < o and pb_short and (e20[i] - c) <= 1.5 * atr_i:
            side, stop = "short", swing_high + params.stop_atr * atr_i

        if side is None or stop is None or i + 1 >= len(rows):
            continue

        # Fill at the NEXT bar's open: the signal only exists once bar i has
        # closed, so using this bar's open would be look-ahead bias.
        entry = slip(rows[i + 1][1], side, entering=True)
        risk_dist = (entry - stop) if side == "long" else (stop - entry)
        if risk_dist <= 0 or risk_dist > 2.5 * atr_i:
            continue

        risk_amount = equity * params.risk_pct / 100.0
        qty = risk_amount / risk_dist
        if qty * entry > equity * params.max_leverage:
            qty = equity * params.max_leverage / entry
        if qty * entry < 10:
            continue

        rng = max(r[2] for r in rows[max(0, i - 19): i + 1]) - \
              min(r[3] for r in rows[max(0, i - 19): i + 1])
        tp2_r = max(params.tp2_min_r, min(rng / risk_dist, params.tp2_max_r))
        tp1 = entry + params.tp1_r * risk_dist if side == "long" \
            else entry - params.tp1_r * risk_dist
        tp2 = entry + tp2_r * risk_dist if side == "long" else entry - tp2_r * risk_dist

        lev = (qty * entry) / equity if equity > 0 else 0.0
        liq_price = liquidation_price(side, entry, lev)
        pos = Position(side=side, entry_ts=int(rows[i + 1][0]), entry=entry, stop=stop,
                       tp1=tp1, tp2=tp2, qty=qty, risk_amount=risk_amount,
                       risk_dist=risk_dist, initial_qty=qty, entry_index=i + 1,
                       leverage=lev, liq_price=liq_price)
        fee = entry * qty * FEE_BPS / 10_000
        pos.fees += fee
        pos.realized -= fee

    # force-close whatever is open at the window edge: no leaked positions
    if pos is not None:
        close_position(rows[-1][4], rows[-1][0], pos.qty, "window_end")
    peak_equity = max(peak_equity, equity)

    return _metrics(trades, equity0, equity, max_dd=max_dd,
                    same_bar_tp1_be=same_bar_tp1_be, ruined=ruined,
                    curve=curve)


def portfolio_metrics(results: list[dict[str, Any]],
                      equity0: float = 10_000.0) -> dict[str, Any]:
    """Combine per-symbol bar curves into one account-level curve.

    Each leg starts at ``equity0``, so summing the legs is the portfolio
    equity. ``max_dd_worst_pct`` sums every leg's worst intrabar mark, which
    assumes all symbols hit their adverse extreme in the same bar: a stress
    figure, not a forecast.
    """
    legs = {
        r["symbol"]: {int(ts): (c, w) for ts, c, w in r.get("curve") or []}
        for r in results
    }
    legs = {s: c for s, c in legs.items() if c}
    if len(legs) < 2:
        return {}
    common = set.intersection(*(set(c) for c in legs.values()))
    if not common:
        return {}

    close: list[float] = []
    worst: list[float] = []
    for ts in sorted(common):
        close.append(sum(curves[ts][0] for curves in legs.values()))
        worst.append(sum(curves[ts][1] for curves in legs.values()))

    def drawdown(series: list[float]) -> float:
        peak = series[0]
        out = 0.0
        for value in series:
            peak = max(peak, value)
            if peak > 0:
                out = max(out, (peak - value) / peak * 100.0)
        return out

    start = equity0 * len(legs)
    end = close[-1]
    return {
        "legs": sorted(legs),
        "bars": len(common),
        "start_equity": round(start, 2),
        "end_equity": round(end, 2),
        "return_pct": round((end - start) / start * 100.0, 3),
        "max_dd_close_pct": round(drawdown(close), 2),
        "max_dd_worst_pct": round(drawdown(worst), 2),
        "trades": sum(r["trades"] for r in results if r["symbol"] in legs),
    }


def _metrics(trades: list[dict[str, Any]], equity0: float,
             equity: float, *, max_dd: float | None = None,
             same_bar_tp1_be: int = 0, ruined: bool = False,
             curve: list[list[float]] | None = None) -> dict[str, Any]:
    n = len(trades)
    wins = [t for t in trades if t["pnl"] > 0]
    losses = [t for t in trades if t["pnl"] <= 0]
    gw = sum(t["pnl"] for t in wins)
    gl = abs(sum(t["pnl"] for t in losses))
    eq = peak = equity0
    closed_dd = 0.0
    for t in trades:
        # A wiped account cannot go below zero, so cap the closed-trade curve
        # the same way the equity curve is capped.
        eq = max(0.0, eq + t["pnl"])
        peak = max(peak, eq)
        closed_dd = max(closed_dd, (peak - eq) / peak * 100.0)

    mix: dict[str, int] = {}
    for t in trades:
        mix[t["exit_reason"]] = mix.get(t["exit_reason"], 0) + 1

    return {
        "trades": n,
        "win_rate": round(len(wins) / n * 100, 2) if n else 0.0,
        "avg_r": round(sum(t["r"] for t in trades) / n, 3) if n else 0.0,
        "profit_factor": round(gw / gl, 3) if gl else None,
        "max_drawdown_pct": round(max_dd if max_dd is not None else closed_dd, 2),
        "max_drawdown_closed_pct": round(closed_dd, 2),
        "same_bar_tp1_be": same_bar_tp1_be,
        "ruined": ruined,
        "curve": curve or [],
        "total_return_pct": round((equity - equity0) / equity0 * 100, 3),
        "final_equity": round(equity, 2),
        "fees_paid": round(sum(t["fees"] for t in trades), 2),
        "funding_paid": round(sum(t["funding"] for t in trades), 2),
        "avg_bars_held": round(sum(t["bars_held"] for t in trades) / n, 1) if n else 0.0,
        "longs": sum(1 for t in trades if t["side"] == "long"),
        "shorts": sum(1 for t in trades if t["side"] == "short"),
        "exit_mix": mix,
        "trades_detail": trades,
    }


def iso(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M")


def parse_day(s: str) -> int:
    dt = datetime.strptime(s, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)


# --------------------------------------------------------------------------- #
# walk-forward
# --------------------------------------------------------------------------- #
GRID = [
    Params(stop_atr=s, tp1_r=tp, require_pullback=pb, time_stop_days=ts)
    for s, tp, pb, ts in itertools.product([0.5, 1.0], [1.5, 2.0, 3.0],
                                           [True, False], [2.0, 4.0])
]


def score(res: dict[str, Any], min_trades: int) -> tuple[float, float]:
    """Train-side ranking: profit factor first, average R as the tie-break."""
    if res["trades"] < min_trades:
        return (-1.0, -1.0)
    pf = res["profit_factor"] if res["profit_factor"] is not None else 0.0
    return (pf, res["avg_r"])


def walk_forward(prep: dict[str, Any], start_ms: int, end_ms: int,
                 train_days: int, test_days: int, step_days: int,
                 equity0: float = 10_000.0,
                 min_train_trades: int = 15,
                 base_params: Params | None = None) -> dict[str, Any]:
    base = base_params or Params()
    grid = [
        replace(
            base,
            stop_atr=p.stop_atr,
            tp1_r=p.tp1_r,
            require_pullback=(base.require_pullback and p.require_pullback),
            require_regime=base.require_regime,
            time_stop_days=p.time_stop_days,
        )
        for p in GRID
    ]
    windows: list[dict[str, Any]] = []
    t0 = start_ms
    while True:
        train_end = t0 + train_days * DAY
        test_end = train_end + test_days * DAY
        if test_end > end_ms:
            break

        best = None
        best_score = (-2.0, -2.0)
        for p in grid:
            r = simulate(prep, p, t0, train_end, equity0)
            s = score(r, min_train_trades)
            if s > best_score:
                best_score, best = s, (p, r)

        chosen = best[0] if best else grid[0]
        oos = simulate(prep, chosen, train_end, test_end, equity0)
        fixed = simulate(prep, base, train_end, test_end, equity0)

        windows.append({
            "train": f"{iso(t0)[:10]} -> {iso(train_end)[:10]}",
            "test": f"{iso(train_end)[:10]} -> {iso(test_end)[:10]}",
            "chosen": chosen.label(),
            "train_pf": best[1]["profit_factor"] if best else None,
            "train_trades": best[1]["trades"] if best else 0,
            "oos": {k: oos[k] for k in
                    ("trades", "win_rate", "avg_r", "profit_factor",
                     "max_drawdown_pct", "total_return_pct", "fees_paid",
                     "funding_paid", "longs", "shorts")},
            "baseline": {k: fixed[k] for k in
                         ("trades", "win_rate", "avg_r", "profit_factor",
                          "max_drawdown_pct", "total_return_pct")},
        })
        t0 += step_days * DAY

    return {"windows": windows}


def aggregate_windows(windows: list[dict[str, Any]]) -> dict[str, Any]:
    out = {}
    for key in ("oos", "baseline"):
        rows = [w[key] for w in windows]
        n = sum(r["trades"] for r in rows)
        rets = [r["total_return_pct"] for r in rows]
        out[key] = {
            "windows": len(rows),
            "trades": n,
            "avg_win_rate": round(sum(r["win_rate"] for r in rows) / len(rows), 2),
            "avg_r": round(sum(r["avg_r"] * r["trades"] for r in rows) / n, 3) if n else 0,
            "positive_windows": sum(1 for r in rets if r > 0),
            "avg_return_pct": round(sum(rets) / len(rets), 2),
            "worst_return_pct": round(min(rets), 2),
            "best_return_pct": round(max(rets), 2),
            "worst_dd_pct": round(max(r["max_drawdown_pct"] for r in rows), 2),
        }
    return out


# --------------------------------------------------------------------------- #
# cli
# --------------------------------------------------------------------------- #
def main() -> int:
    ap = argparse.ArgumentParser()
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
    ap.add_argument("--no-pullback", action="store_true",
                    help="ablation: drop the pullback filter")
    ap.add_argument("--no-regime", action="store_true",
                    help="ablation: drop the 4h regime filter")
    args = ap.parse_args()

    base_params = Params(
        require_pullback=not args.no_pullback,
        require_regime=not args.no_regime,
    )

    start_ms = parse_day(args.start)
    end_ms = parse_day(args.end)
    fetch_start = start_ms - args.warmup_days * DAY

    payload: dict[str, Any] = {
        "generated": datetime.now(timezone.utc).isoformat(),
        "mode": args.mode,
        "range": {"start": args.start, "end": args.end},
        "config": {"fee_bps": FEE_BPS, "slip_bps": SLIP_BPS,
                   "mmr": MMR, "liq_fee_bps": LIQ_FEE_BPS,
                   "warmup_days": args.warmup_days},
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
                res = simulate(prep, base_params, start_ms, end_ms)
                res.update({"symbol": symbol, "timeframe": tf})
                payload["results"].append(res)
                print(f"[res ] {symbol} {tf} trades={res['trades']} "
                      f"pf={res['profit_factor']} ret={res['total_return_pct']}% "
                      f"funding={res['funding_paid']} ({time.time()-t:.0f}s)",
                      file=sys.stderr)
            else:
                wf = walk_forward(prep, start_ms, end_ms, args.train_days,
                                  args.test_days, args.step_days,
                                  base_params=base_params)
                wf.update({"symbol": symbol, "timeframe": tf,
                           "aggregate": aggregate_windows(wf["windows"])})
                payload["results"].append(wf)
                agg = wf["aggregate"]
                print(f"[wf  ] {symbol} {tf} windows={agg['oos']['windows']} "
                      f"oos_trades={agg['oos']['trades']} "
                      f"oos_avg={agg['oos']['avg_return_pct']}% "
                      f"base_avg={agg['baseline']['avg_return_pct']}% "
                      f"({time.time()-t:.0f}s)", file=sys.stderr)

    if args.mode == "single" and len(payload["results"]) > 1:
        if len({r["timeframe"] for r in payload["results"]}) == 1:
            port = portfolio_metrics(payload["results"])
            if port:
                payload["portfolio"] = port

    if args.json_out:
        Path(args.json_out).write_text(json.dumps(payload, indent=2))
    print(json.dumps({k: v for k, v in payload.items() if k != "results"}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
