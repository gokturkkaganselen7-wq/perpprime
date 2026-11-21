"""Regression tests for the spot-perp basis engine (scripts/backtest_basis.py)."""

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from backtest_basis import BasisParams, simulate_basis  # noqa: E402

HOUR = 3_600_000


def _data(perp, spot, funding):
    rows = [[i * HOUR, p, p, p, p, 1.0] for i, p in enumerate(perp)]
    srows = [[i * HOUR, p, p, p, p, 1.0] for i, p in enumerate(spot)]
    return {"symbol": "TEST/USDT:USDT", "rows": rows, "spot": srows,
            "funding_by_bar": funding}


def _funding_stamps(n, every=8, rate=0.0005):
    out = [0.0] * n
    for i in range(0, n, every):
        out[i] = rate
    return out


def test_basis_is_delta_neutral_when_both_legs_move_together():
    """A 10% move in both legs must not create a directional P&L."""
    n = 40
    perp = [100.0] * 10 + [110.0] * (n - 10)
    spot = list(perp)
    data = _data(perp, spot, _funding_stamps(n, rate=0.0002))
    params = BasisParams(entry_ann=10.0, exit_ann=0.0, max_hold_days=1)

    res = simulate_basis(data, params, 0, n * HOUR)

    assert res["trades"] >= 1
    trade = res["trades_detail"][0]
    assert trade["exit_reason"] == "max_hold"
    # Spot +10% and short perp -10% cancel; only fees, funding and the small
    # slippage mismatch remain. A directional trade would show ~ +/-500.
    assert -25.0 < trade["pnl"] < 0.0
    assert abs(trade["pnl"]) < 0.02 * 10_000


def test_basis_collects_funding_when_it_is_rich():
    n = 100
    flat = [100.0] * n
    data = _data(flat, flat, _funding_stamps(n, rate=0.0005))
    params = BasisParams(entry_ann=10.0, exit_ann=0.0, max_hold_days=3)

    res = simulate_basis(data, params, 0, n * HOUR)

    assert res["trades"] >= 1
    trade = res["trades_detail"][0]
    assert trade["funding"] > 0
    assert trade["pnl"] > 0
    # PnL = funding - fees - slippage; on 10k of notional the four 2 bps
    # slippage fills cost about 4 USDT, so allow that band.
    assert trade["pnl"] == pytest.approx(
        trade["funding"] - trade["fees"], abs=5.0
    )


def test_basis_enters_at_the_next_open():
    n = 40
    flat = [100.0] * n
    data = _data(flat, flat, _funding_stamps(n, rate=0.0005))
    params = BasisParams(entry_ann=10.0, exit_ann=0.0, max_hold_days=1)

    res = simulate_basis(data, params, 0, n * HOUR)

    trade = res["trades_detail"][0]
    assert trade["entry_ts"] == "1970-01-01 02:00"


def test_basis_stays_flat_without_funding():
    n = 40
    flat = [100.0] * n
    data = _data(flat, flat, [0.0] * n)
    params = BasisParams(entry_ann=10.0)

    res = simulate_basis(data, params, 0, n * HOUR)

    assert res["trades"] == 0
    assert res["total_return_pct"] == 0.0
