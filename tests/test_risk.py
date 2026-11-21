from datetime import datetime, timedelta, timezone

import pytest

from cfos.config import RiskConfig
from cfos.models import (
    AccountState,
    GateStatus,
    Mode,
    Side,
    TradePlan,
)
from cfos.risk import (
    evaluate_gates,
    liquidation_price,
    portfolio_heat,
    position_size,
    realized_pnl,
    reward_risk,
    symbol_in_universe,
)
from cfos.config import UniverseConfig


def plan(**over) -> TradePlan:
    base = dict(
        symbol="BTC/USDT:USDT",
        side=Side.LONG,
        entry=100.0,
        stop=95.0,
        take_profits=[110.0],
        qty=1.0,
        leverage=2.0,
        risk_amount=5.0,
        notional=100.0,
        rr=2.0,
        mode=Mode.PAPER,
        venue="bybit",
    )
    base.update(over)
    return TradePlan(**base)


def state(**over) -> AccountState:
    base = dict(equity=10_000.0, peak_equity=10_000.0, day_start_equity=10_000.0,
                week_start_equity=10_000.0)
    base.update(over)
    return AccountState(**base)


# -- sizing -----------------------------------------------------------------
def test_position_size_basic():
    result = position_size(equity=10_000, risk_pct=0.5, entry=65_000, stop=64_000)
    assert result.ok
    assert result.qty == pytest.approx(0.05)
    assert result.notional == pytest.approx(3_250)
    assert result.leverage == pytest.approx(0.325)
    assert result.risk_amount == pytest.approx(50)
    assert result.risk_pct == pytest.approx(0.5)


def test_position_size_short_is_mirrored():
    result = position_size(
        equity=10_000, risk_pct=1, entry=100, stop=105, side=Side.SHORT
    )
    assert result.ok
    assert result.qty == pytest.approx(20)
    assert result.liq_price is not None and result.liq_price > 100


def test_leverage_cap_reduces_size_and_warns():
    result = position_size(
        equity=1_000, risk_pct=5, entry=100, stop=99, max_leverage=3
    )
    assert result.ok
    assert result.qty == pytest.approx(30)
    assert result.leverage == pytest.approx(3.0)
    assert result.risk_pct == pytest.approx(3.0)
    assert result.warnings


def test_below_minimum_is_rejected():
    result = position_size(
        equity=10_000, risk_pct=0.5, entry=65_000, stop=64_000, min_qty=1.0
    )
    assert not result.ok
    assert "minimum" in result.reason


def test_wrong_stop_side_is_rejected():
    assert not position_size(equity=10_000, risk_pct=1, entry=100, stop=101).ok
    assert not position_size(
        equity=10_000, risk_pct=1, entry=100, stop=99, side=Side.SHORT
    ).ok


def test_stop_beyond_liquidation_is_rejected():
    result = position_size(
        equity=10_000,
        risk_pct=100,
        entry=100,
        stop=99,
        max_leverage=100,
    )
    assert not result.ok
    assert "liquidation" in result.reason


def test_liquidation_price_math():
    assert liquidation_price(Side.LONG, 100, 10, 0.005) == pytest.approx(90.5)
    assert liquidation_price(Side.SHORT, 100, 10, 0.005) == pytest.approx(109.5)


def test_reward_risk_and_heat():
    assert reward_risk(100, 95, 110) == pytest.approx(2.0)
    assert reward_risk(100, 95, 100) == 0.0
    assert portfolio_heat([50, 25], 10_000) == pytest.approx(0.75)


# -- gates ------------------------------------------------------------------
def _gate_names(gates):
    return {g.name for g in gates if g.status is GateStatus.BLOCK}


def test_clean_plan_has_no_blocks():
    gates = evaluate_gates(
        state=state(),
        plan=plan(),
        risk=RiskConfig(),
        market_age_sec=1,
        spread_bps=1.0,
    )
    assert _gate_names(gates) == set()


def test_missing_spread_blocks():
    """No order book means no measured spread: refuse to trade blind."""
    gates = evaluate_gates(
        state=state(), plan=plan(), risk=RiskConfig(), market_age_sec=1
    )
    assert "spread" in _gate_names(gates)


def test_kill_switch_blocks():
    gates = evaluate_gates(
        state=state(), plan=plan(), risk=RiskConfig(), kill_reason="manual"
    )
    assert "kill_switch" in _gate_names(gates)


def test_daily_loss_blocks():
    gates = evaluate_gates(
        state=state(equity=9_700), plan=plan(), risk=RiskConfig()
    )
    assert "daily_loss" in _gate_names(gates)


def test_weekly_loss_blocks():
    gates = evaluate_gates(
        state=state(equity=9_400, week_start_equity=10_000),
        plan=plan(),
        risk=RiskConfig(),
    )
    assert "weekly_loss" in _gate_names(gates)


def test_drawdown_blocks():
    gates = evaluate_gates(
        state=state(equity=8_900, peak_equity=10_000), plan=plan(), risk=RiskConfig()
    )
    assert "drawdown" in _gate_names(gates)


def test_heat_blocks():
    gates = evaluate_gates(
        state=state(open_risk=400), plan=plan(risk_amount=200), risk=RiskConfig()
    )
    assert "portfolio_heat" in _gate_names(gates)


def test_cluster_cap_blocks():
    gates = evaluate_gates(
        state=state(cluster_counts={"majors": 2}),
        plan=plan(),
        risk=RiskConfig(),
        intended_cluster="majors",
    )
    assert "correlation" in _gate_names(gates)


def test_rr_below_min_blocks():
    gates = evaluate_gates(state=state(), plan=plan(rr=1.0), risk=RiskConfig())
    assert "reward_risk" in _gate_names(gates)


def test_stale_data_blocks():
    gates = evaluate_gates(
        state=state(), plan=plan(), risk=RiskConfig(), market_age_sec=600
    )
    assert "data_freshness" in _gate_names(gates)


def test_missing_snapshot_blocks():
    gates = evaluate_gates(state=state(), plan=plan(), risk=RiskConfig())
    assert "data_freshness" in _gate_names(gates)


def test_leverage_gate_blocks():
    gates = evaluate_gates(state=state(), plan=plan(leverage=10), risk=RiskConfig())
    assert "leverage" in _gate_names(gates)


def test_symbol_not_allowed_blocks():
    gates = evaluate_gates(
        state=state(), plan=plan(), risk=RiskConfig(), symbol_allowed=False
    )
    assert "universe" in _gate_names(gates)


def test_cooldown_blocks_after_losses():
    now = datetime.now(timezone.utc)
    gates = evaluate_gates(
        state=state(
            consecutive_losses=2,
            last_loss_ts=(now - timedelta(minutes=10)).isoformat(),
        ),
        plan=plan(),
        risk=RiskConfig(),
        now=now,
    )
    assert "cooldown" in _gate_names(gates)


def test_cooldown_expires():
    now = datetime.now(timezone.utc)
    gates = evaluate_gates(
        state=state(
            consecutive_losses=2,
            last_loss_ts=(now - timedelta(hours=3)).isoformat(),
        ),
        plan=plan(),
        risk=RiskConfig(),
        now=now,
    )
    assert "cooldown" not in _gate_names(gates)


def test_gate_order_is_stable():
    gates = evaluate_gates(state=state(), plan=plan(), risk=RiskConfig())
    assert [g.name for g in gates][:3] == ["kill_switch", "universe", "daily_loss"]


def test_symbol_in_universe():
    universe = UniverseConfig(allow=("BTC/USDT:USDT",))
    assert symbol_in_universe("BTC/USDT:USDT", universe)
    assert not symbol_in_universe("DOGE/USDT:USDT", universe)


def test_realized_pnl_applies_contract_size():
    # One contract worth 0.01 BTC: a 1,000 USD move is 10 USD per contract.
    assert realized_pnl(65_000, 66_000, 1, Side.LONG, 0.01) == pytest.approx(10.0)
    assert realized_pnl(65_000, 64_000, 2, Side.LONG, 0.01) == pytest.approx(-20.0)
    assert realized_pnl(65_000, 64_000, 1, Side.SHORT, 0.01) == pytest.approx(10.0)
