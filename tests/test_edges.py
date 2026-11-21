"""Regression tests for the alternative-edge engine (scripts/backtest_edges.py)."""

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from backtest_edges import EdgeParams, simulate_edge  # noqa: E402
from backtest_trend import SLIP_BPS  # noqa: E402

HOUR = 3_600_000
DAY = 24 * HOUR


def _prep(candles, *, volumes=None, atr=2.0, regime="range", funding=None):
    rows = [
        [i * HOUR, o, h, l, c, (volumes[i] if volumes else 1.0)]
        for i, (o, h, l, c) in enumerate(candles)
    ]
    n = len(rows)
    return {
        "symbol": "TEST/USDT:USDT",
        "tf": "1h",
        "rows": rows,
        "h4_start": 0,
        "regime_at": [{"regime": regime, "ema50": None}] * n,
        "closes": [r[4] for r in rows],
        "e20": [100.0] * n,
        "e50": [99.0] * n,
        "a14": [atr] * n,
        "funding_by_bar": funding or [0.0] * n,
    }


def _flat(n, price=100.0):
    return [(price, price, price, price)] * n


def _run(prep, params, equity0=10_000.0):
    return simulate_edge(prep, params, 0, len(prep["rows"]) * HOUR, equity0)


def test_carry_enters_at_the_next_open_and_receives_funding():
    candles = _flat(30)
    funding = [0.0] * 30
    funding[19] = 0.0005          # 5 bps per 8h, rich enough to short
    funding[24] = 0.0005          # a stamp while the position is open
    funding[27] = 0.00005         # carry gone; no re-entry signal
    params = EdgeParams("carry", entry_bp=1.0, exit_bp=0.0, max_bars=6,
                        stop_atr=1.5, tp_r=10.0)

    res = _run(_prep(candles, funding=funding), params)

    assert res["trades"] == 1
    trade = res["trades_detail"][0]
    assert trade["side"] == "short"
    # Unit: the short is paid, so funding is positive on the trade record.
    assert trade["funding"] > 0
    next_open = 100.0 * (1 - SLIP_BPS / 10_000)
    assert trade["entry"] == pytest.approx(next_open, rel=1e-9)
    assert trade["entry_ts"] == "1970-01-01 20:00"   # bar 20, not bar 19
    assert trade["exit_reason"] == "time_stop"


def test_carry_stays_flat_when_funding_is_neutral():
    params = EdgeParams("carry", entry_bp=1.0)
    res = _run(_prep(_flat(30)), params)
    assert res["trades"] == 0


def test_opening_range_breakout_buys_the_break_and_closes_at_day_end():
    # Day 0 supplies the 20-bar volume history. Day 1 opens 99..101 and breaks.
    candles = _flat(24, 100.0)                   # bars 0..23
    candles += [(100.0, 100.5, 99.0, 100.0),     # bar 24: OR
                (100.0, 101.0, 99.5, 100.5)]     # bar 25: OR
    candles += _flat(3, 100.5)                   # bars 26..28 inside the range
    candles += [(100.5, 102.5, 100.5, 102.0)]    # bar 29: closes above OR high
    candles += _flat(18, 102.0)                  # rest of day 1
    candles += _flat(10, 102.0)                  # day 2, no breakout
    volumes = [1.0] * len(candles)
    volumes[29] = 5.0
    params = EdgeParams("orb", or_hours=2, vol_mult=1.2, stop_atr=1.0,
                        tp_r=10.0, max_bars=100)

    res = _run(_prep(candles, volumes=volumes, regime="trend_up"), params)

    assert res["trades"] == 1
    trade = res["trades_detail"][0]
    assert trade["side"] == "long"
    assert trade["entry_ts"] == "1970-01-02 06:00"   # bar 30, one after the signal
    assert trade["exit_reason"] == "day_end"
    assert trade["entry"] == pytest.approx(102.0 * (1 + SLIP_BPS / 10_000))


def test_range_fade_requires_a_stretch_and_a_reclaim():
    candles = _flat(19) + [(100.0, 100.0, 94.5, 95.0)]     # bar 19: -2.5 ATR
    candles += [(95.0, 96.2, 94.8, 96.0)]                  # bar 20: green reclaim
    candles += [(96.2, 97.0, 96.0, 96.8)]                  # bar 21: entry bar
    candles += [(96.8, 100.5, 96.5, 100.0)]                # bar 22: reaches the mean
    candles += _flat(10, 100.0)
    params = EdgeParams("range", dev_atr=2.0, stop_atr=1.5, tp_r=2.0,
                        max_bars=72)

    res = _run(_prep(candles, regime="range"), params)

    assert res["trades"] == 1
    trade = res["trades_detail"][0]
    assert trade["side"] == "long"
    assert trade["exit_reason"] == "tp"
    assert trade["entry_ts"] == "1970-01-01 21:00"   # bar 21
    assert trade["pnl"] > 0


def test_range_fade_refuses_a_trend_regime():
    candles = _flat(19) + [(100.0, 100.0, 94.5, 95.0)]
    candles += [(95.0, 96.2, 94.8, 96.0)] + _flat(20, 100.0)
    params = EdgeParams("range", dev_atr=2.0)

    res = _run(_prep(candles, regime="trend_down"), params)

    assert res["trades"] == 0
