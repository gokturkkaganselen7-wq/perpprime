"""Risk kernel: pure functions, no I/O, fully unit-testable.

Every number the executor uses to size or veto a trade comes from here.
If a rule cannot be evaluated (missing state, stale data), the kernel
fails closed: BLOCK, never "assume it's fine".
"""

from __future__ import annotations

import math
from datetime import datetime, timezone

from .config import RiskConfig, UniverseConfig
from .models import AccountState, GateResult, GateStatus, Side, SizeResult, TradePlan

EPS = 1e-12


def floor_step(value: float, step: float) -> float:
    if step <= 0:
        return value
    return round(math.floor(value / step + EPS) * step, 12)


def stop_distance(entry: float, stop: float) -> float:
    distance = abs(entry - stop)
    if distance <= 0:
        raise ValueError("entry and stop must differ")
    return distance


def realized_pnl(
    entry: float,
    exit_price: float,
    qty: float,
    side: Side,
    contract_size: float = 1.0,
) -> float:
    """Gross PnL. ``qty`` is in contracts, so contract size must be applied."""
    return (exit_price - entry) * qty * contract_size * side.sign


def liquidation_price(
    side: Side,
    entry: float,
    leverage: float,
    maintenance_margin_rate: float = 0.005,
) -> float | None:
    """Approximate isolated-margin liquidation price.

    This is an estimate, not venue truth. It exists to catch the case where
    the stop sits beyond the liquidation price — the one error a stop cannot
    save you from.
    """
    if entry <= 0 or leverage <= 0:
        return None
    if side is Side.LONG:
        price = entry * (1.0 - 1.0 / leverage + maintenance_margin_rate)
    else:
        price = entry * (1.0 + 1.0 / leverage - maintenance_margin_rate)
    return max(price, 0.0)


def position_size(
    *,
    equity: float,
    risk_pct: float,
    entry: float,
    stop: float,
    side: Side = Side.LONG,
    contract_size: float = 1.0,
    qty_step: float = 0.001,
    min_qty: float = 0.0,
    max_notional: float | None = None,
    max_leverage: float = 3.0,
    maintenance_margin_rate: float = 0.005,
) -> SizeResult:
    """Size a position so that entry-to-stop loss equals ``risk_pct`` of equity."""
    if equity <= 0:
        return SizeResult(False, 0, 0, 0, 0, 0, 0, None, reason="equity must be > 0")
    if risk_pct <= 0:
        return SizeResult(False, 0, 0, 0, 0, 0, 0, None, reason="risk_pct must be > 0")
    if entry <= 0 or stop <= 0:
        return SizeResult(False, 0, 0, 0, 0, 0, 0, None, reason="entry/stop must be > 0")
    if side is Side.LONG and stop >= entry:
        return SizeResult(False, 0, 0, 0, 0, 0, 0, None, reason="long stop must be below entry")
    if side is Side.SHORT and stop <= entry:
        return SizeResult(False, 0, 0, 0, 0, 0, 0, None, reason="short stop must be above entry")

    distance = stop_distance(entry, stop)
    risk_amount = equity * risk_pct / 100.0
    warnings: list[str] = []

    raw_qty = risk_amount / (distance * contract_size)
    qty = floor_step(raw_qty, qty_step)

    if qty <= 0 or qty < min_qty:
        return SizeResult(
            False,
            0,
            0,
            0,
            risk_amount,
            risk_pct,
            distance,
            None,
            reason=f"size {qty} below minimum {min_qty} (increase risk or tighten stop)",
        )

    notional = qty * entry * contract_size
    leverage = notional / equity

    if leverage > max_leverage:
        capped = floor_step(max_leverage * equity / (entry * contract_size), qty_step)
        if capped < min_qty:
            return SizeResult(
                False,
                0,
                0,
                0,
                risk_amount,
                risk_pct,
                distance,
                None,
                warnings,
                f"leverage cap {max_leverage}x leaves size below minimum",
            )
        qty = capped
        notional = qty * entry * contract_size
        leverage = notional / equity
        warnings.append(
            f"leverage capped at {max_leverage}x; risk reduced to "
            f"{qty * distance * contract_size:.2f}"
        )

    if max_notional is not None and notional > max_notional:
        capped = floor_step(max_notional / (entry * contract_size), qty_step)
        if capped < min_qty:
            return SizeResult(
                False,
                0,
                0,
                0,
                risk_amount,
                risk_pct,
                distance,
                None,
                warnings,
                "notional cap leaves size below minimum",
            )
        qty = capped
        notional = qty * entry * contract_size
        leverage = notional / equity
        warnings.append(f"notional capped at {max_notional:.2f}")

    actual_risk = qty * distance * contract_size
    liq = liquidation_price(side, entry, leverage, maintenance_margin_rate)

    if liq is not None:
        liq_breached = (side is Side.LONG and stop <= liq) or (
            side is Side.SHORT and stop >= liq
        )
        if liq_breached:
            return SizeResult(
                False,
                qty,
                notional,
                leverage,
                actual_risk,
                actual_risk / equity * 100.0,
                distance,
                liq,
                warnings,
                f"stop {stop:.4f} is beyond estimated liquidation {liq:.4f}",
            )

    return SizeResult(
        ok=True,
        qty=qty,
        notional=notional,
        leverage=leverage,
        risk_amount=actual_risk,
        risk_pct=actual_risk / equity * 100.0,
        stop_distance=distance,
        liq_price=liq,
        warnings=warnings,
    )


def reward_risk(entry: float, stop: float, take_profit: float) -> float:
    risk = abs(entry - stop)
    if risk <= 0:
        return 0.0
    return abs(take_profit - entry) / risk


def portfolio_heat(open_risks: list[float], equity: float) -> float:
    if equity <= 0:
        return 0.0
    return sum(max(0.0, r) for r in open_risks) / equity * 100.0


def _block(name: str, reason: str, value=None) -> GateResult:
    return GateResult(name, GateStatus.BLOCK, reason, value)


def _warn(name: str, reason: str, value=None) -> GateResult:
    return GateResult(name, GateStatus.WARN, reason, value)


def _allow(name: str, reason: str = "ok", value=None) -> GateResult:
    return GateResult(name, GateStatus.ALLOW, reason, value)


def evaluate_gates(
    *,
    state: AccountState,
    plan: TradePlan,
    risk: RiskConfig,
    kill_reason: str | None = None,
    symbol_allowed: bool | None = None,
    market_age_sec: float | None = None,
    spread_bps: float | None = None,
    intended_cluster: str | None = None,
    now: datetime | None = None,
) -> list[GateResult]:
    """Evaluate every hard rule. Order is stable so tests can assert on it."""
    now = now or datetime.now(timezone.utc)
    gates: list[GateResult] = []

    if kill_reason:
        gates.append(_block("kill_switch", f"engaged: {kill_reason}"))
    else:
        gates.append(_allow("kill_switch"))

    if symbol_allowed is False:
        gates.append(_block("universe", f"{plan.symbol} is not in the allowlist"))
    else:
        gates.append(_allow("universe"))

    if state.daily_pnl_pct <= -risk.daily_loss_limit_pct:
        gates.append(
            _block(
                "daily_loss",
                f"daily P&L {state.daily_pnl_pct:.2f}% breaches "
                f"-{risk.daily_loss_limit_pct:.2f}%",
                state.daily_pnl_pct,
            )
        )
    else:
        gates.append(_allow("daily_loss", f"{state.daily_pnl_pct:.2f}%"))

    weekly_pct = 0.0
    if state.week_start_equity > 0:
        weekly_pct = (state.equity - state.week_start_equity) / state.week_start_equity * 100
    if weekly_pct <= -risk.weekly_loss_limit_pct:
        gates.append(
            _block(
                "weekly_loss",
                f"weekly P&L {weekly_pct:.2f}% breaches -{risk.weekly_loss_limit_pct:.2f}%",
                weekly_pct,
            )
        )
    else:
        gates.append(_allow("weekly_loss", f"{weekly_pct:.2f}%"))

    if state.drawdown_pct >= risk.max_drawdown_pct:
        gates.append(
            _block(
                "drawdown",
                f"drawdown {state.drawdown_pct:.2f}% breaches {risk.max_drawdown_pct:.2f}%",
                state.drawdown_pct,
            )
        )
    else:
        gates.append(_allow("drawdown", f"{state.drawdown_pct:.2f}%"))

    if state.trades_today >= risk.max_trades_per_day:
        gates.append(
            _block(
                "trade_count",
                f"{state.trades_today}/{risk.max_trades_per_day} trades used today",
            )
        )
    else:
        gates.append(_allow("trade_count", f"{state.trades_today}/{risk.max_trades_per_day}"))

    if state.open_positions >= risk.max_open_positions:
        gates.append(
            _block(
                "max_positions",
                f"{state.open_positions}/{risk.max_open_positions} open positions",
            )
        )
    else:
        gates.append(_allow("max_positions", f"{state.open_positions}/{risk.max_open_positions}"))

    same_symbol = state.open_symbols.count(plan.symbol)
    if same_symbol >= risk.max_per_symbol:
        gates.append(_block("max_per_symbol", f"{same_symbol} position(s) already in {plan.symbol}"))
    else:
        gates.append(_allow("max_per_symbol"))

    if intended_cluster:
        in_cluster = state.cluster_counts.get(intended_cluster, 0)
        if in_cluster >= risk.max_cluster_positions:
            gates.append(
                _block(
                    "correlation",
                    f"cluster '{intended_cluster}' already holds {in_cluster} positions",
                )
            )
        else:
            gates.append(_allow("correlation", f"{in_cluster}/{risk.max_cluster_positions}"))
    else:
        gates.append(_allow("correlation", "no cluster"))

    heat = (state.open_risk + plan.risk_amount) / state.equity * 100.0 if state.equity > 0 else 100.0
    if heat > risk.max_portfolio_heat_pct:
        gates.append(
            _block(
                "portfolio_heat",
                f"heat {heat:.2f}% would breach {risk.max_portfolio_heat_pct:.2f}%",
                heat,
            )
        )
    else:
        gates.append(_allow("portfolio_heat", f"{heat:.2f}%"))

    if state.consecutive_losses >= risk.consecutive_loss_cooldown and state.last_loss_ts:
        try:
            last_loss = datetime.fromisoformat(state.last_loss_ts)
            if last_loss.tzinfo is None:
                last_loss = last_loss.replace(tzinfo=timezone.utc)
            minutes = (now - last_loss).total_seconds() / 60.0
            if minutes < risk.cooldown_minutes:
                gates.append(
                    _block(
                        "cooldown",
                        f"{state.consecutive_losses} consecutive losses; "
                        f"{risk.cooldown_minutes - minutes:.0f} min cooldown left",
                    )
                )
            else:
                gates.append(_allow("cooldown"))
        except ValueError:
            gates.append(_block("cooldown", f"unparseable last_loss_ts {state.last_loss_ts!r}"))
    else:
        gates.append(_allow("cooldown"))

    if plan.rr < risk.min_rr:
        gates.append(
            _block("reward_risk", f"R:R {plan.rr:.2f} below minimum {risk.min_rr:.2f}", plan.rr)
        )
    else:
        gates.append(_allow("reward_risk", f"{plan.rr:.2f}R"))

    if market_age_sec is None:
        gates.append(_block("data_freshness", "no market snapshot; cannot verify freshness"))
    elif market_age_sec > risk.max_data_age_sec:
        gates.append(
            _block(
                "data_freshness",
                f"snapshot is {market_age_sec:.0f}s old (max {risk.max_data_age_sec}s)",
            )
        )
    else:
        gates.append(_allow("data_freshness", f"{market_age_sec:.0f}s"))

    if spread_bps is not None:
        if spread_bps > risk.max_spread_bps * 2:
            gates.append(
                _block("spread", f"spread {spread_bps:.1f} bps is 2x the {risk.max_spread_bps} bps limit")
            )
        elif spread_bps > risk.max_spread_bps:
            gates.append(
                _warn("spread", f"spread {spread_bps:.1f} bps above {risk.max_spread_bps} bps")
            )
        else:
            gates.append(_allow("spread", f"{spread_bps:.1f} bps"))
    else:
        gates.append(_allow("spread", "unknown (no order book snapshot)"))

    if plan.leverage > risk.max_leverage + 1e-9:
        gates.append(
            _block("leverage", f"{plan.leverage:.2f}x above cap {risk.max_leverage:.2f}x")
        )
    else:
        gates.append(_allow("leverage", f"{plan.leverage:.2f}x"))

    return gates


def blocked(gates: list[GateResult]) -> list[GateResult]:
    return [g for g in gates if g.blocked]


def gate_summary(gates: list[GateResult]) -> str:
    hard = blocked(gates)
    if hard:
        return "BLOCK: " + "; ".join(f"{g.name} ({g.reason})" for g in hard)
    warnings = [g for g in gates if g.status is GateStatus.WARN]
    if warnings:
        return "ALLOW with warnings: " + "; ".join(f"{g.name} ({g.reason})" for g in warnings)
    return "ALLOW"


def symbol_in_universe(symbol: str, universe: UniverseConfig) -> bool:
    return not universe.allow or symbol in universe.allow
