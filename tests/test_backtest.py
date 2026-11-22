"""Regression tests for the backtest engine's honesty rules.

The engine lives in scripts/backtest_trend.py (not a package module), so the
test imports it by path.
"""

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from backtest_trend import (  # noqa: E402
    MMR,
    SLIP_BPS,
    Params,
    liquidation_price,
    portfolio_metrics,
    simulate,
)

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


# --------------------------------------------------------------------------- #
# P2 modelling: gaps, liquidation, intrabar drawdown, ambiguous TP1 bars
# --------------------------------------------------------------------------- #
def _prep_with(candles, *, atr=2.0, regime="trend_up"):
    """Build a deterministic prep from explicit (o, h, l, c) candles.

    EMA20 sits at 100 and EMA50 at 99, so the only signal is the bar whose
    close crosses above 100 after a close at or below it.
    """
    rows = [[i * HOUR, o, h, l, c, 1.0] for i, (o, h, l, c) in enumerate(candles)]
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
        "funding_by_bar": [0.0] * n,
    }


def _flat(n, price=100.0):
    return [(price, price, price, price)] * n


def _long_base():
    """Warm-up, a pullback close at 99, then a signal bar closing at 101."""
    candles = _flat(19)                       # bars 0..18
    candles.append((100.0, 100.0, 98.5, 99.0))   # bar 19: pullback low 98.5
    candles.append((100.0, 102.0, 99.0, 101.0))  # bar 20: signal
    return candles


def _long_params(**over):
    base = dict(require_regime=False, require_pullback=True, time_stop_days=2.0)
    base.update(over)
    return Params(**base)


def test_liquidation_price_only_applies_above_one_x():
    assert liquidation_price("long", 100.0, 1.0) is None
    assert liquidation_price("long", 100.0, 0.5) is None
    # 4x long: 100 * (1 - 1/4 + 0.005)
    assert liquidation_price("long", 100.0, 4.0) == pytest.approx(75.5)
    # 4x short: 100 * (1 + 1/4 - 0.005)
    assert liquidation_price("short", 100.0, 4.0) == pytest.approx(124.5)
    assert liquidation_price("long", 100.0, 4.0, mmr=0.0) == pytest.approx(75.0)
    assert MMR == pytest.approx(0.005)


def test_gap_through_stop_fills_at_the_open_not_the_stop():
    """A bar that opens below the stop cannot fill at the stop price."""
    candles = _long_base() + [(100.0, 100.5, 99.5, 100.0)]   # bar 21: entry bar
    gapped = candles + [(95.0, 96.0, 94.0, 95.0)] + _flat(8, 95.0)
    clean = candles + [(97.5, 98.0, 97.0, 97.5)] + _flat(8, 97.5)

    gap_res = simulate(_prep_with(gapped), _long_params(), 0, len(gapped) * HOUR)
    clean_res = simulate(_prep_with(clean), _long_params(), 0, len(clean) * HOUR)

    gap_trade = gap_res["trades_detail"][0]
    clean_trade = clean_res["trades_detail"][0]

    assert gap_trade["exit_reason"] == "stop_gap"
    assert clean_trade["exit_reason"] == "stop"
    # Same trade, same stop: the gap must cost strictly more than a stop fill.
    assert gap_trade["pnl"] < clean_trade["pnl"]
    assert gap_trade["r"] < clean_trade["r"]


def test_liquidation_takes_precedence_over_the_stop():
    """With 4x leverage a gap through the liquidation level kills the position."""
    candles = _flat(19, 100.0)
    candles.append((100.0, 100.0, 99.88, 99.95))   # bar 19: tight swing low
    candles.append((100.0, 100.1, 99.95, 100.05))  # bar 20: signal
    candles.append((100.0, 100.05, 99.90, 100.0))  # bar 21: entry, no stop
    candles.append((60.0, 61.0, 59.0, 60.0))       # bar 22: gap through 4x liq
    candles += _flat(6, 60.0)

    res = simulate(_prep_with(candles, atr=0.1), _long_params(), 0, len(candles) * HOUR)

    assert res["trades"] == 1
    trade = res["trades_detail"][0]
    assert trade["exit_reason"] == "liquidation"
    # Liquidated at the gap open with the extra liquidation fee, so the loss
    # is far larger than the 0.5% equity risk the plan asked for.
    assert trade["r"] < -1.0
    assert trade["pnl"] < -50.0
    # The 2.6x position loses more than the account holds: the engine must
    # stop at zero instead of compounding a negative balance.
    assert res["ruined"] is True
    assert res["final_equity"] == 0.0
    assert res["total_return_pct"] == -100.0
    assert res["max_drawdown_pct"] == 100.0


def test_intrabar_drawdown_sees_the_adverse_excursion():
    """Max DD must include open-position pain, not just closed-trade equity."""
    candles = _long_base() + [
        (100.0, 100.5, 98.5, 100.4),   # bar 21: deep wick, no stop/target
        (100.4, 100.6, 99.0, 100.5),
    ]
    candles += [(100.5, 101.0, 100.0, 100.8)] + _flat(9, 100.8)

    res = simulate(_prep_with(candles), _long_params(), 0, len(candles) * HOUR)

    assert res["trades"] == 1
    trade = res["trades_detail"][0]
    assert trade["pnl"] > 0            # the trade itself is a small winner
    assert res["max_drawdown_closed_pct"] == 0.0
    assert res["max_drawdown_pct"] > 0.3
    assert res["max_drawdown_pct"] > res["max_drawdown_closed_pct"]


def test_same_bar_tp1_and_breakeven_is_counted():
    """One OHLC bar cannot prove TP1 came before the pullback to breakeven."""
    candles = _long_base() + [(100.0, 106.0, 99.9, 105.0)]
    candles += _flat(10, 105.0)

    res = simulate(_prep_with(candles), _long_params(), 0, len(candles) * HOUR)

    assert res["trades"] == 1
    trade = res["trades_detail"][0]
    assert trade["exit_reason"] == "breakeven"
    assert trade["partials"] == 2
    assert res["same_bar_tp1_be"] == 1


def test_portfolio_metrics_combines_legs_at_the_bar_level():
    """Portfolio DD must sum concurrent marks, not add up per-symbol DDs."""
    legs = [
        {"symbol": "A", "trades": 2, "curve": [
            [0, 10_000, 10_000],
            [1, 9_800, 9_700],
            [2, 10_100, 10_100],
        ]},
        {"symbol": "B", "trades": 1, "curve": [
            [0, 10_000, 10_000],
            [1, 10_100, 10_100],
            [2, 9_900, 9_900],
        ]},
    ]

    port = portfolio_metrics(legs, equity0=10_000.0)

    assert port["legs"] == ["A", "B"]
    assert port["start_equity"] == 20_000.0
    assert port["end_equity"] == 20_000.0
    assert port["return_pct"] == 0.0
    # Close marks: 20,000 -> 19,900 -> 20,000 = 0.5% drawdown.
    assert port["max_dd_close_pct"] == pytest.approx(0.5)
    # Worst marks assume both legs hit their low in the same bar: 1.0%.
    assert port["max_dd_worst_pct"] == pytest.approx(1.0)
    assert port["trades"] == 3


def test_portfolio_metrics_ignores_unshared_timestamps():
    legs = [
        {"symbol": "A", "trades": 1, "curve": [[0, 10_000, 10_000], [1, 9_000, 9_000]]},
        {"symbol": "B", "trades": 1, "curve": [[1, 10_000, 10_000], [2, 11_000, 11_000]]},
    ]

    port = portfolio_metrics(legs, equity0=10_000.0)

    assert port["bars"] == 1
    assert port["start_equity"] == 20_000.0
    assert port["end_equity"] == 19_000.0


def test_position_is_closed_at_the_window_edge_not_the_file_end():
    """A walk-forward test window must not hold a position into later data."""
    candles = [(100.0, 100.5, 99.5, 100.0)] * 19
    candles.append((100.0, 100.5, 99.5, 99.0))    # bar 19: pullback
    candles.append((100.0, 102.0, 99.8, 101.0))   # bar 20: signal
    candles.append((100.0, 100.5, 99.9, 100.0))   # bar 21: entry bar
    candles += [(100.0, 100.5, 99.9, 100.0)] * 4  # bars 22..25
    candles += [(200.0, 200.0, 200.0, 200.0)] * 14  # after the window

    prep = _prep_with(candles)
    params = _long_params()
    res = simulate(prep, params, 0, 25 * HOUR)

    trade = res["trades_detail"][0]
    assert trade["exit_reason"] == "window_end"
    assert trade["exit_ts"] == "1970-01-02 01:00"   # bar 25 (25h), not the file end
    assert trade["pnl"] < 10.0                       # no 200.0 windfall
