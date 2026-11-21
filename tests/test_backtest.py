"""Regression tests for the backtest engine's honesty rules.

The engine lives in scripts/backtest_trend.py (not a package module), so the
test imports it by path.
"""

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from backtest_trend import SLIP_BPS, Params, simulate  # noqa: E402

HOUR = 3_600_000
N = 40


def _prep():
    """One long signal on bar 20, next bar opens at a clearly different price."""
    rows: list[list[float]] = []
    for i in range(N):
        o, h, l, c = 100.0, 102.0, 98.0, 100.0
        if i == 19:
            c = 99.0  # close below EMA20 -> pullback condition for the next bar
        elif i == 20:
            o, h, l, c = 100.0, 102.0, 99.0, 101.5  # signal bar: closes up
        elif i == 21:
            o, h, l, c = 101.0, 101.5, 96.0, 97.0  # entry bar: hits the stop
        rows.append([i * HOUR, o, h, l, c, 1.0])

    closes = [r[4] for r in rows]
    e20 = [100.5] * N
    e20[19] = 100.0
    e50 = [100.0] * N
    a14 = [2.0] * N
    regime_at = [{"regime": "trend_up", "ema50": None}] * 20

    return {
        "symbol": "TEST/USDT:USDT",
        "tf": "1h",
        "rows": rows,
        "h4_start": 0,
        "regime_at": regime_at,
        "closes": closes,
        "e20": e20,
        "e50": e50,
        "a14": a14,
        "funding_by_bar": [0.0] * N,
    }


def test_entry_fills_at_next_bar_open_not_signal_bar_open():
    """A signal that only exists at bar i's close must fill at bar i+1's open.

    Regression for the v1.0.0 look-ahead bug: the engine used the signal bar's
    open as the fill price, which handed every trade a free bar body.
    """
    prep = _prep()
    params = Params(require_regime=False, require_pullback=True)

    res = simulate(prep, params, start_ms=0, end_ms=N * HOUR)

    assert res["trades"] == 1
    trade = res["trades_detail"][0]
    assert trade["side"] == "long"
    assert trade["exit_reason"] == "stop"

    next_open = prep["rows"][21][1]
    expected = next_open * (1 + SLIP_BPS / 10_000)
    signal_open = prep["rows"][20][1] * (1 + SLIP_BPS / 10_000)

    assert trade["entry"] == pytest.approx(expected, rel=1e-9)
    assert trade["entry"] != pytest.approx(signal_open, rel=1e-9)
    assert trade["entry_ts"] == "1970-01-01 21:00"  # bar 21, not bar 20
