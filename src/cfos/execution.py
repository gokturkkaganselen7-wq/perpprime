"""Plan building, account state assembly and the only order-placing path."""

from __future__ import annotations

import time
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from typing import Any

from .config import Settings
from .exchange import BaseExchange, build_exchange, with_retries
from .guards import KillSwitch, check_live_permission, preflight_market
from .journal import Journal
from .models import (
    AccountState,
    ExecutionReport,
    GateStatus,
    Mode,
    OrderRequest,
    Side,
    TradePlan,
    Verdict,
    dumps,
    utcnow,
)
from .paper import PaperExchange
from .risk import (
    blocked,
    evaluate_gates,
    floor_step,
    position_size,
    realized_pnl,
    reward_risk,
    symbol_in_universe,
)


def _midnight_utc() -> datetime:
    now = datetime.now(timezone.utc)
    return datetime(now.year, now.month, now.day, tzinfo=timezone.utc)


def _week_start_utc() -> datetime:
    now = datetime.now(timezone.utc)
    midnight = datetime(now.year, now.month, now.day, tzinfo=timezone.utc)
    return midnight - timedelta(days=midnight.weekday())


def confirm_entry(
    exchange: BaseExchange,
    symbol: str,
    order: Any,
    *,
    attempts: int = 5,
    delay: float = 0.4,
) -> Any:
    """Return the entry order after the venue has had a moment to fill it.

    Market-order creation responses often carry only an order id (Bybit does
    exactly this), so ``status is None`` at creation is not evidence that the
    order did not fill. Polling avoids attaching a bracket to an order we only
    *think* is open — or refusing to protect one that already filled.
    """
    if isinstance(exchange, PaperExchange):
        return order
    if order.status in ("closed", "filled") or float(order.filled or 0) > 0:
        return order
    last = order
    for _ in range(max(1, attempts)):
        if not last.id:
            break
        time.sleep(delay)
        try:
            last = exchange.order(last.id, symbol)
        except Exception:  # noqa: BLE001 - a failed poll is not a failed fill
            break
        if last.status in ("closed", "filled") or float(last.filled or 0) > 0:
            break
    return last


def defensive_close(
    settings: Settings, exchange: BaseExchange, symbol: str
) -> str:
    """Close a venue position only if one actually exists.

    Used when a fill is uncertain: a market entry can succeed while its
    response is lost. Closing blindly turns a clean failure into a confusing
    "no position" error, so inspect the venue first.
    """
    try:
        positions = [
            p for p in exchange.positions() if p.symbol == symbol and abs(p.qty) > 0
        ]
    except Exception as exc:  # noqa: BLE001 - uncertainty must be reported
        return (
            f"could not read venue positions ({type(exc).__name__}: {exc}); "
            "verify the venue manually"
        )
    if not positions:
        return "no venue position found; nothing to close"
    try:
        closed = close_position(settings, symbol)
        return f"defensive close submitted: {closed.get('status', 'unknown')}"
    except Exception as exc:  # noqa: BLE001 - a failed close is critical
        return (
            f"DEFENSIVE CLOSE FAILED: {type(exc).__name__}: {exc}; "
            "the position may be unprotected — close it manually"
        )


def split_quantity(
    total: float, parts: int, min_qty: float = 0.0, step: float = 0.0
) -> list[float]:
    """Split a position into take-profit slices without dropping below min.

    Slices stay on the venue's quantity step; the last slice absorbs the
    remainder so the sum is exactly ``total``. Fewer slices are returned when
    the venue minimum or step makes the ladder impossible.
    """
    if parts <= 1:
        return [total]
    if min_qty > 0:
        parts = max(1, min(parts, int(total / min_qty + 1e-12)))
    if parts <= 1:
        return [total]
    base = total / parts
    if step > 0:
        base = floor_step(base, step)
    if base <= 0 or base * (parts - 1) >= total:
        return [total]
    slices = [base] * (parts - 1)
    slices.append(total - base * (parts - 1))
    if min_qty > 0 and any(s < min_qty for s in slices):
        return split_quantity(total, parts - 1, min_qty, step)
    return slices


def account_state(
    settings: Settings,
    journal: Journal,
    exchange: BaseExchange,
) -> AccountState:
    equity = exchange.balance()
    positions = exchange.positions()
    open_rows = journal.open_positions()
    open_risk = sum(float(row.get("risk_amount") or 0.0) for row in open_rows)

    peak = journal.peak_equity() or equity
    peak = max(peak, equity)

    day_start = journal.equity_at_or_before(_midnight_utc().isoformat(timespec="seconds")) or equity
    week_start = journal.equity_at_or_before(_week_start_utc().isoformat(timespec="seconds")) or equity

    losses, last_loss_ts = journal.consecutive_losses()

    clusters: dict[str, int] = {}
    for position in positions:
        cluster = settings.universe.cluster_of(position.symbol)
        if cluster:
            clusters[cluster] = clusters.get(cluster, 0) + 1

    return AccountState(
        equity=equity,
        peak_equity=peak,
        day_start_equity=day_start,
        week_start_equity=week_start,
        realized_pnl_today=journal.realized_today(),
        consecutive_losses=losses,
        last_loss_ts=last_loss_ts,
        open_positions=len(positions),
        open_risk=open_risk,
        open_symbols=[p.symbol for p in positions],
        cluster_counts=clusters,
        trades_today=journal.trades_today(),
    )


def build_plan(
    settings: Settings,
    exchange: BaseExchange,
    *,
    symbol: str,
    side: Side,
    entry: float,
    stop: float,
    take_profits: list[float],
    risk_pct: float | None = None,
    intent: str = "manual",
    rationale: str = "",
    invalidation: str = "",
    leverage: float | None = None,
) -> TradePlan:
    if not take_profits:
        raise ValueError("at least one take profit is required")
    if any(tp <= 0 for tp in take_profits):
        raise ValueError("take profits must be > 0")
    if entry <= 0 or stop <= 0:
        raise ValueError("entry and stop must be > 0")
    if side is Side.LONG:
        if stop >= entry:
            raise ValueError("long stop must be below entry")
        if any(tp <= entry for tp in take_profits):
            raise ValueError("long take profits must be above entry")
        if any(a >= b for a, b in zip(take_profits, take_profits[1:])):
            raise ValueError("long take profits must be strictly increasing")
    else:
        if stop <= entry:
            raise ValueError("short stop must be above entry")
        if any(tp >= entry for tp in take_profits):
            raise ValueError("short take profits must be below entry")
        if any(a <= b for a, b in zip(take_profits, take_profits[1:])):
            raise ValueError("short take profits must be strictly decreasing")

    resolved = exchange.resolve_symbol(symbol)
    contract_size = exchange.contract_size(resolved)
    qty_step = exchange.qty_step(resolved)
    min_qty = exchange.min_qty(resolved)

    pct = risk_pct if risk_pct is not None else settings.risk.risk_per_trade_pct
    equity = exchange.balance()
    size = position_size(
        equity=equity,
        risk_pct=pct,
        entry=entry,
        stop=stop,
        side=side,
        contract_size=contract_size,
        qty_step=qty_step,
        min_qty=min_qty,
        max_leverage=settings.risk.max_leverage,
        maintenance_margin_rate=settings.risk.maintenance_margin_rate,
    )
    if not size.ok:
        raise ValueError(f"cannot size plan: {size.reason}")

    rr = reward_risk(entry, stop, take_profits[0])
    plan = TradePlan(
        symbol=resolved,
        side=side,
        entry=entry,
        stop=stop,
        take_profits=take_profits,
        qty=size.qty,
        leverage=size.leverage if leverage is None else leverage,
        risk_amount=size.risk_amount,
        notional=size.notional,
        rr=rr,
        mode=settings.mode,
        venue=settings.venue.name,
        intent=intent,
        rationale=rationale,
        invalidation=invalidation,
        warnings=size.warnings,
    )
    return plan


def execute(
    settings: Settings,
    plan: TradePlan,
    *,
    confirm_live: bool = False,
    dry_run: bool = False,
) -> ExecutionReport:
    exchange = build_exchange(settings)
    journal = Journal(settings.db_path)
    kill = KillSwitch(settings.kill_file)
    engaged, kill_reason = kill.engaged()

    # Paper mode must be executable offline and deterministically: seed the
    # symbol at the plan's entry price when no price has been set yet.
    if isinstance(exchange, PaperExchange) and plan.symbol not in exchange.state["prices"]:
        exchange.set_price(plan.symbol, plan.entry)

    snapshot = exchange.market(plan.symbol)
    age = snapshot.age_seconds()
    allowed = symbol_in_universe(plan.symbol, settings.universe)
    ok, preflight_reason = preflight_market(snapshot, settings.universe)
    if not ok:
        allowed = False

    state = account_state(settings, journal, exchange)
    cluster = settings.universe.cluster_of(plan.symbol)
    gates = evaluate_gates(
        state=state,
        plan=plan,
        risk=settings.risk,
        kill_reason=kill_reason if engaged else None,
        symbol_allowed=allowed,
        market_age_sec=age,
        spread_bps=snapshot.spread_bps,
        intended_cluster=cluster,
    )
    gate_dicts = [asdict(g) for g in gates]
    hard = blocked(gates)

    def finish(
        verdict: Verdict,
        messages: list[str],
        orders: list[dict[str, Any]] | None = None,
        position: dict[str, Any] | None = None,
    ) -> ExecutionReport:
        journal.log_decision(
            mode=settings.mode.value,
            verdict=verdict.value,
            intent=plan.intent,
            symbol=plan.symbol,
            side=plan.side.value,
            reasons=messages,
            plan=plan.to_dict(),
        )
        try:
            journal.snapshot_equity(exchange.balance())
        except Exception as exc:  # noqa: BLE001 - a failed snapshot must not hide the verdict
            messages = messages + [
                f"equity snapshot failed: {type(exc).__name__}: {exc}"
            ]
        return ExecutionReport(
            ok=verdict is Verdict.EXECUTED,
            verdict=verdict,
            mode=settings.mode,
            symbol=plan.symbol,
            plan=plan.to_dict(),
            orders=orders or [],
            messages=messages,
            gates=gate_dicts,
            position=position,
        )

    if dry_run:
        return finish(Verdict.DRY_RUN, ["dry run: no orders sent"] + [preflight_reason])

    if hard:
        return finish(
            Verdict.BLOCKED,
            [f"{g.name}: {g.reason}" for g in hard],
        )

    permission = check_live_permission(settings.mode, settings.live_ack, confirm_live)
    if not permission.ok:
        return finish(Verdict.BLOCKED, [f"live permission: {permission.reason}"])

    entry_filled = False
    entry_attempted = False
    try:
        exchange.set_leverage(plan.symbol, plan.leverage)
        entry_request = OrderRequest(
            symbol=plan.symbol,
            side=plan.side.order_side,
            type="market",
            amount=plan.qty,
            client_id=f"{plan.id}-entry",
        )
        entry_attempted = True
        entry = with_retries(
            lambda: exchange.create_order(entry_request),
            retries=settings.risk.max_order_retries,
        )
        entry = confirm_entry(exchange, plan.symbol, entry)
        filled_qty = float(entry.filled or 0.0)
        if filled_qty <= 0 and entry.status in ("closed", "filled"):
            filled_qty = plan.qty
        entry_filled = filled_qty > 0
        journal.log_order(
            client_id=f"{plan.id}-entry",
            venue=settings.venue.name,
            symbol=plan.symbol,
            type="market",
            side=plan.side.order_side,
            qty=filled_qty or plan.qty,
            price=entry.avg_price,
            status=entry.status,
            raw=entry.raw,
        )
        orders = [asdict(entry)]

        if not entry_filled:
            messages = [
                f"entry order is {entry.status}; fill unconfirmed, bracket not attached",
                "checking the venue for a residual position before giving up",
            ]
            messages.append(defensive_close(settings, exchange, plan.symbol))
            return finish(Verdict.ERROR, messages, orders)

        fill_price = entry.avg_price or snapshot.price
        warnings = list(plan.warnings)
        if filled_qty + 1e-12 < plan.qty:
            warnings.append(
                f"partial entry: {filled_qty:g} of {plan.qty:g} filled; "
                "bracket covers the filled size only"
            )
        if entry.status not in ("closed", "filled"):
            warnings.append(
                f"entry reported status {entry.status!r}; "
                "only the visible filled quantity is bracketed"
            )
        if isinstance(exchange, PaperExchange):
            exchange.tag_position(
                plan.symbol, position_id=plan.id, risk_amount=plan.risk_amount
            )

        # Record the position BEFORE attaching the bracket. If a stop fails,
        # the emergency close has a journal row to reconcile against.
        journal.open_position(
            position_id=plan.id,
            symbol=plan.symbol,
            side=plan.side.value,
            qty=filled_qty,
            entry=fill_price,
            stop=plan.stop,
            tp=plan.take_profits[0],
            leverage=plan.leverage,
            risk_amount=plan.risk_amount,
            plan=plan.to_dict(),
        )

        stop_request = OrderRequest(
            symbol=plan.symbol,
            side=plan.side.close_side,
            type="stop_market",
            amount=filled_qty,
            price=plan.stop,
            reduce_only=True,
            client_id=f"{plan.id}-stop",
        )
        stop = exchange.create_order(stop_request)
        journal.log_order(
            client_id=f"{plan.id}-stop",
            venue=settings.venue.name,
            symbol=plan.symbol,
            type="stop_market",
            side=plan.side.close_side,
            qty=filled_qty,
            price=plan.stop,
            status=stop.status,
            raw=stop.raw,
        )
        orders.append(asdict(stop))

        slices = split_quantity(
            filled_qty,
            len(plan.take_profits),
            exchange.min_qty(plan.symbol),
            exchange.qty_step(plan.symbol),
        )
        active_tps = plan.take_profits[: len(slices)]
        for index, (tp, tp_qty) in enumerate(zip(active_tps, slices), start=1):
            tp_request = OrderRequest(
                symbol=plan.symbol,
                side=plan.side.close_side,
                type="take_profit_market",
                amount=tp_qty,
                price=tp,
                reduce_only=True,
                client_id=f"{plan.id}-tp{index}",
            )
            take_profit = exchange.create_order(tp_request)
            journal.log_order(
                client_id=f"{plan.id}-tp{index}",
                venue=settings.venue.name,
                symbol=plan.symbol,
                type="take_profit_market",
                side=plan.side.close_side,
                qty=tp_qty,
                price=tp,
                status=take_profit.status,
                raw=take_profit.raw,
            )
            orders.append(asdict(take_profit))

        position = {
            "id": plan.id,
            "symbol": plan.symbol,
            "side": plan.side.value,
            "qty": filled_qty,
            "entry": fill_price,
            "stop": plan.stop,
            "take_profit": active_tps,
            "leverage": plan.leverage,
            "risk_amount": plan.risk_amount,
        }
        messages = [
            f"entry filled at {fill_price:.6f}",
            f"stop attached at {plan.stop:.6f}",
            f"{min(len(plan.take_profits), len(slices))} take-profit order(s) attached",
        ]
        if len(slices) < len(plan.take_profits):
            warnings.append(
                f"TP ladder reduced to {len(slices)} level(s) by venue minimum size"
            )
        messages.extend(warnings)
        return finish(Verdict.EXECUTED, messages, orders, position)

    except Exception as exc:  # noqa: BLE001 - surface the venue error verbatim
        messages = [f"execution failed: {type(exc).__name__}: {exc}"]
        if entry_filled:
            messages.append(
                "entry filled but bracket is incomplete; attempting emergency close"
            )
            messages.append(defensive_close(settings, exchange, plan.symbol))
        elif entry_attempted:
            # The request may have reached the venue even though the response
            # was lost (timeout, dropped connection, 5xx). Never assume flat.
            messages.append(
                "entry state unknown; checking the venue for an unprotected position"
            )
            messages.append(defensive_close(settings, exchange, plan.symbol))
        return finish(Verdict.ERROR, messages)


def close_position(settings: Settings, symbol: str, qty: float | None = None) -> dict[str, Any]:
    exchange = build_exchange(settings)
    journal = Journal(settings.db_path)
    open_rows = [r for r in journal.open_positions() if r["symbol"] == symbol]
    live_positions = [p for p in exchange.positions() if p.symbol == symbol]

    # A full close must not leave bracket orders resting behind it. A partial
    # close keeps the stop/TP protection for the remaining size (reduce-only
    # orders can only ever shrink the position).
    if qty is None:
        try:
            exchange.cancel_all(symbol)
        except Exception:  # noqa: BLE001 - closing risk wins over cleanup errors
            pass

    result = exchange.close_position(symbol, qty)

    if isinstance(exchange, PaperExchange):
        # Paper records the authoritative closed trade, including fees.
        for trade in exchange.drain_trades():
            if trade.get("position_id"):
                journal.close_position(
                    trade["position_id"],
                    exit_price=float(trade["exit"]),
                    pnl=float(trade["pnl"]),
                    fees=float(trade.get("fees") or 0.0),
                    qty=float(trade["qty"]) if trade.get("qty") is not None else None,
                )
    elif open_rows:
        row = open_rows[0]
        position = live_positions[0] if live_positions else None
        exit_price = result.avg_price or (position.mark if position else 0.0)
        pnl = 0.0
        if position is not None:
            closed_qty = qty if qty is not None else abs(position.qty)
            pnl = realized_pnl(
                position.entry,
                exit_price,
                closed_qty,
                position.side,
                exchange.contract_size(symbol),
            )
        journal.close_position(
            row["id"], exit_price=exit_price, pnl=pnl, qty=qty
        )

    journal.snapshot_equity(exchange.balance())
    return asdict(result)


def sync_paper(settings: Settings) -> list[dict[str, Any]]:
    """Reconcile paper fills (stop/TP/liquidation) into the journal."""
    exchange = build_exchange(settings)
    if not isinstance(exchange, PaperExchange):
        return []
    journal = Journal(settings.db_path)
    synced: list[dict[str, Any]] = []
    for trade in exchange.drain_trades():
        position_id = trade.get("position_id")
        if not position_id:
            continue
        journal.close_position(
            position_id,
            exit_price=float(trade["exit"]),
            pnl=float(trade["pnl"]),
            fees=float(trade.get("fees") or 0.0),
            qty=float(trade["qty"]) if trade.get("qty") is not None else None,
        )
        synced.append(trade)
    journal.snapshot_equity(exchange.equity(), realized=exchange.state["realized"])
    return synced


def status(settings: Settings) -> dict[str, Any]:
    exchange = build_exchange(settings)
    journal = Journal(settings.db_path)
    state = account_state(settings, journal, exchange)
    engaged, reason = KillSwitch(settings.kill_file).engaged()
    return {
        "mode": settings.mode.value,
        "venue": settings.venue.name,
        "kill_switch": {"engaged": engaged, "reason": reason},
        "account": asdict(state),
        "positions": [asdict(p) for p in exchange.positions()],
        "summary": journal.summary(),
        "equity_curve": journal.equity_curve(limit=30),
        "last_decisions": journal.recent_decisions(limit=5),
    }


def dump_plan(plan: TradePlan) -> str:
    return dumps(plan.to_dict())
