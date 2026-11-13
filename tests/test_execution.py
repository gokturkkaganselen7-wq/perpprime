import pytest

from cfos.exchange import BaseExchange, build_exchange
from cfos.execution import (
    build_plan,
    close_position,
    confirm_entry,
    execute,
    split_quantity,
    sync_paper,
)
from cfos.guards import KillSwitch
from cfos.journal import Journal
from cfos.models import (
    MarketSnapshot,
    OrderResult,
    PositionSnapshot,
    Side,
    Verdict,
)
from cfos.paper import PaperExchange

SYMBOL = "BTC/USDT:USDT"


class FakeVenue(BaseExchange):
    """A scripted venue for paths paper mode cannot produce (async fills)."""

    name = "fake"

    def __init__(
        self,
        entry_result: OrderResult | Exception | None = None,
        *,
        visible_after_entry: list[PositionSnapshot] | None = None,
    ):
        self.entry_result = entry_result
        self.visible_after_entry = list(visible_after_entry or [])
        self._positions: list[PositionSnapshot] = []
        self.requests = []
        self.closed: list[float | None] = []
        self.polls = 0
        self.snapshot = MarketSnapshot(
            symbol=SYMBOL,
            price=65_000.0,
            quote_volume_24h=5_000_000_000.0,
            spread_bps=1.0,
        )

    def load(self) -> None:
        pass

    def market(self, symbol: str) -> MarketSnapshot:
        return self.snapshot

    def positions(self) -> list[PositionSnapshot]:
        return list(self._positions)

    def balance(self) -> float:
        return 10_000.0

    def set_leverage(self, symbol: str, leverage: float) -> None:
        pass

    def ohlcv(self, symbol: str, timeframe: str = "4h", limit: int = 200):
        return []

    def create_order(self, request):
        self.requests.append(request)
        if request.client_id and request.client_id.endswith("-entry"):
            self._positions = list(self.visible_after_entry)
            if isinstance(self.entry_result, Exception):
                raise self.entry_result
            return self.entry_result
        return OrderResult(
            id=f"o{len(self.requests)}",
            client_id=request.client_id,
            status="open",
            filled=0.0,
            avg_price=request.price,
        )

    def order(self, order_id: str, symbol: str) -> OrderResult:
        self.polls += 1
        return OrderResult(
            id=order_id,
            client_id=None,
            status="open",
            filled=0.0,
            avg_price=None,
        )

    def cancel(self, order_id: str, symbol: str) -> None:
        pass

    def cancel_all(self, symbol: str) -> None:
        pass

    def close_position(self, symbol: str, qty: float | None = None) -> OrderResult:
        self.closed.append(qty)
        return OrderResult(
            id="close-1",
            client_id=None,
            status="closed",
            filled=1.0,
            avg_price=65_000.0,
        )


def venue_position(qty: float = 0.05) -> PositionSnapshot:
    return PositionSnapshot(
        symbol=SYMBOL,
        side=Side.LONG,
        qty=qty,
        entry=65_000.0,
        mark=65_000.0,
        leverage=1.0,
        unrealized=0.0,
    )


def make_plan(settings, **over):
    exchange = PaperExchange(settings)
    base = dict(
        symbol=SYMBOL,
        side=Side.LONG,
        entry=65_000.0,
        stop=64_000.0,
        take_profits=[67_000.0],
        intent="test",
        rationale="unit test",
        invalidation="close below 64,000",
    )
    base.update(over)
    return build_plan(settings, exchange, **base)


def test_build_plan_sizes_and_sets_geometry(settings):
    plan = make_plan(settings)
    assert plan.qty == pytest.approx(0.05)
    assert plan.risk_amount == pytest.approx(50.0)
    assert plan.leverage == pytest.approx(0.325)
    assert plan.rr == pytest.approx(2.0)
    assert plan.mode.value == "paper"
    assert plan.id


def test_build_plan_rejects_wrong_stop_side(settings):
    with pytest.raises(ValueError):
        make_plan(settings, stop=66_000.0)


def test_build_plan_rejects_tp_on_wrong_side_of_entry(settings):
    with pytest.raises(ValueError, match="above entry"):
        make_plan(settings, take_profits=[64_500.0])


def test_build_plan_rejects_non_monotonic_tp_ladder(settings):
    with pytest.raises(ValueError, match="increasing"):
        make_plan(settings, take_profits=[68_000.0, 67_000.0])


def test_short_plan_requires_descending_tps(settings):
    plan = make_plan(
        settings,
        side=Side.SHORT,
        entry=65_000.0,
        stop=66_000.0,
        take_profits=[63_000.0, 62_000.0],
    )
    assert plan.side is Side.SHORT
    assert plan.rr == pytest.approx(2.0)
    with pytest.raises(ValueError, match="decreasing"):
        make_plan(
            settings,
            side=Side.SHORT,
            entry=65_000.0,
            stop=66_000.0,
            take_profits=[62_000.0, 63_000.0],
        )


def test_split_quantity_keeps_total_and_respects_minimum():
    assert split_quantity(0.05, 1) == [0.05]
    assert split_quantity(0.05, 2) == pytest.approx([0.025, 0.025])
    # Venue minimum forces fewer slices instead of an unfillable one.
    assert split_quantity(0.05, 4, min_qty=0.02) == pytest.approx([0.025, 0.025])
    halves = split_quantity(0.05, 2)
    assert sum(halves) == pytest.approx(0.05)


def test_split_quantity_stays_on_the_venue_step():
    slices = split_quantity(0.05, 3, step=0.001)
    assert slices == pytest.approx([0.016, 0.016, 0.018])
    assert all(
        slice_ == pytest.approx(round(slice_ / 0.001) * 0.001) for slice_ in slices
    )
    assert sum(slices) == pytest.approx(0.05)

    # A step larger than the slice collapses the ladder to one level.
    assert split_quantity(0.05, 8, step=0.02) == [0.05]


def test_dry_run_places_nothing(settings):
    plan = make_plan(settings)
    report = execute(settings, plan, dry_run=True)
    assert report.verdict is Verdict.DRY_RUN
    assert report.ok is False
    assert PaperExchange(settings).positions() == []
    journal = Journal(settings.db_path)
    assert journal.open_positions() == []


def test_paper_execute_opens_bracket_and_journals(settings):
    plan = make_plan(settings)
    report = execute(settings, plan)
    assert report.verdict is Verdict.EXECUTED, report.messages
    assert report.position["stop"] == pytest.approx(64_000.0)
    assert report.position["take_profit"] == [pytest.approx(67_000.0)]

    paper = PaperExchange(settings)
    positions = paper.positions()
    assert len(positions) == 1
    assert positions[0].stop == pytest.approx(64_000.0)
    assert positions[0].take_profit == pytest.approx(67_000.0)

    journal = Journal(settings.db_path)
    open_rows = journal.open_positions()
    assert len(open_rows) == 1
    assert open_rows[0]["symbol"] == SYMBOL
    assert journal.recent_decisions(limit=1)[0]["verdict"] == "EXECUTED"


def test_kill_switch_blocks_execution(settings):
    KillSwitch(settings.kill_file).engage("unit test")
    plan = make_plan(settings)
    report = execute(settings, plan)
    assert report.verdict is Verdict.BLOCKED
    assert any("kill_switch" in message for message in report.messages)
    assert PaperExchange(settings).positions() == []


def test_reward_risk_gate_blocks(settings):
    plan = make_plan(settings, take_profits=[65_500.0])  # 1.5R
    report = execute(settings, plan)
    assert report.verdict is Verdict.BLOCKED
    assert any("reward_risk" in message for message in report.messages)


def test_leverage_gate_blocks(settings):
    plan = make_plan(settings)
    plan.leverage = 10.0
    report = execute(settings, plan)
    assert report.verdict is Verdict.BLOCKED
    assert any("leverage" in message for message in report.messages)


def test_second_position_in_same_symbol_is_blocked(settings):
    first = execute(settings, make_plan(settings))
    assert first.verdict is Verdict.EXECUTED

    second = execute(settings, make_plan(settings, entry=66_000.0, stop=65_000.0, take_profits=[68_000.0]))
    assert second.verdict is Verdict.BLOCKED
    assert any("max_per_symbol" in message for message in second.messages)


def test_universe_gate_blocks(settings):
    plan = make_plan(settings, symbol="PEPE/USDT:USDT")
    report = execute(settings, plan)
    assert report.verdict is Verdict.BLOCKED
    assert any("universe" in message for message in report.messages)


def test_paper_stop_sync_closes_journal(settings):
    plan = make_plan(settings)
    assert execute(settings, plan).verdict is Verdict.EXECUTED

    paper = PaperExchange(settings)
    paper.set_price(SYMBOL, plan.stop)
    synced = sync_paper(settings)
    assert len(synced) == 1
    assert synced[0]["reason"] == "stop"

    journal = Journal(settings.db_path)
    assert journal.open_positions() == []
    summary = journal.summary()
    assert summary["trades"] == 1
    assert summary["total_pnl"] < 0


def test_paper_take_profit_sync(settings):
    plan = make_plan(settings)
    assert execute(settings, plan).verdict is Verdict.EXECUTED

    paper = PaperExchange(settings)
    paper.set_price(SYMBOL, plan.take_profits[0])
    synced = sync_paper(settings)
    assert len(synced) == 1
    assert synced[0]["reason"] == "take_profit"
    assert Journal(settings.db_path).summary()["total_pnl"] > 0


def test_tp_ladder_closes_in_slices_and_accumulates_pnl(settings):
    plan = make_plan(settings, take_profits=[67_000.0, 68_500.0])
    report = execute(settings, plan)
    assert report.verdict is Verdict.EXECUTED, report.messages

    position = PaperExchange(settings).positions()[0]
    assert position.qty == pytest.approx(0.05)
    assert position.take_profit == pytest.approx(67_000.0)

    # TP1 closes half; the position and the next ladder level stay open.
    PaperExchange(settings).set_price(SYMBOL, 67_000.0)
    first = sync_paper(settings)
    assert len(first) == 1
    assert first[0]["reason"] == "take_profit"
    assert first[0]["qty"] == pytest.approx(0.025)
    assert first[0]["remaining"] == pytest.approx(0.025)

    position = PaperExchange(settings).positions()[0]
    assert position.qty == pytest.approx(0.025)
    assert position.take_profit == pytest.approx(68_500.0)

    journal = Journal(settings.db_path)
    open_rows = journal.open_positions()
    assert len(open_rows) == 1
    assert open_rows[0]["qty"] == pytest.approx(0.025)
    assert open_rows[0]["pnl"] == pytest.approx(first[0]["pnl"])

    # TP2 closes the rest; the journal keeps ONE trade with accumulated PnL.
    PaperExchange(settings).set_price(SYMBOL, 68_500.0)
    second = sync_paper(settings)
    assert len(second) == 1
    assert second[0]["remaining"] == pytest.approx(0.0)

    assert PaperExchange(settings).positions() == []
    journal = Journal(settings.db_path)
    assert journal.open_positions() == []
    summary = journal.summary()
    assert summary["trades"] == 1
    assert summary["total_pnl"] == pytest.approx(first[0]["pnl"] + second[0]["pnl"])
    assert summary["total_pnl"] > 0


def test_single_tick_can_fill_the_whole_tp_ladder(settings):
    plan = make_plan(settings, take_profits=[67_000.0, 68_500.0])
    assert execute(settings, plan).verdict is Verdict.EXECUTED

    PaperExchange(settings).set_price(SYMBOL, 69_000.0)
    synced = sync_paper(settings)
    assert [trade["reason"] for trade in synced] == ["take_profit", "take_profit"]
    assert sum(trade["qty"] for trade in synced) == pytest.approx(0.05)
    assert PaperExchange(settings).positions() == []

    summary = Journal(settings.db_path).summary()
    assert summary["trades"] == 1
    assert summary["total_pnl"] == pytest.approx(sum(t["pnl"] for t in synced))


def test_partial_manual_close_keeps_journal_open(settings):
    plan = make_plan(settings)
    assert execute(settings, plan).verdict is Verdict.EXECUTED

    PaperExchange(settings).set_price(SYMBOL, 65_500.0)
    close_position(settings, SYMBOL, qty=0.02)

    position = PaperExchange(settings).positions()[0]
    assert position.qty == pytest.approx(0.03)

    journal = Journal(settings.db_path)
    open_rows = journal.open_positions()
    assert len(open_rows) == 1
    assert open_rows[0]["qty"] == pytest.approx(0.03)
    # A partial exit must not restart the trade count.
    assert journal.summary()["trades"] == 0


def test_build_exchange_respects_mode(settings):
    assert isinstance(build_exchange(settings), PaperExchange)


def test_stop_failure_triggers_emergency_close(settings, monkeypatch):
    original = PaperExchange.create_order
    seen = []

    def flaky(self, request):
        seen.append(request.type)
        if request.type == "stop_market":
            raise RuntimeError("venue rejected stop")
        return original(self, request)

    monkeypatch.setattr(PaperExchange, "create_order", flaky)

    report = execute(settings, make_plan(settings))
    assert report.verdict is Verdict.ERROR
    assert "stop_market" in seen
    assert any("emergency close" in message.lower() for message in report.messages)

    # The position must not be left open and unprotected.
    assert PaperExchange(settings).positions() == []
    summary = Journal(settings.db_path).summary()
    assert summary["trades"] == 1



def test_confirm_entry_trusts_a_creation_fill_without_polling():
    creation = OrderResult(
        id="e1", client_id=None, status="closed", filled=0.05, avg_price=65_000.0
    )

    class NoPoll:
        def order(self, order_id, symbol):
            raise AssertionError("a confirmed fill must not be polled")

    assert confirm_entry(NoPoll(), SYMBOL, creation) is creation


def test_confirm_entry_polls_until_the_fill_is_visible():
    creation = OrderResult(
        id="e1", client_id=None, status="open", filled=0.0, avg_price=None
    )

    class FillsAfterOne:
        def __init__(self):
            self.polls = 0

        def order(self, order_id, symbol):
            self.polls += 1
            return OrderResult(
                id=order_id,
                client_id=None,
                status="closed",
                filled=0.05,
                avg_price=65_010.0,
            )

    venue = FillsAfterOne()
    confirmed = confirm_entry(venue, SYMBOL, creation, attempts=3, delay=0)
    assert venue.polls == 1
    assert confirmed.status == "closed"
    assert confirmed.filled == pytest.approx(0.05)


def test_confirm_entry_gives_up_on_an_unresolved_order():
    creation = OrderResult(
        id="e1", client_id=None, status="open", filled=0.0, avg_price=None
    )

    class NeverFills:
        def __init__(self):
            self.polls = 0

        def order(self, order_id, symbol):
            self.polls += 1
            return OrderResult(
                id=order_id,
                client_id=None,
                status="open",
                filled=0.0,
                avg_price=None,
            )

    venue = NeverFills()
    result = confirm_entry(venue, SYMBOL, creation, attempts=2, delay=0)
    assert venue.polls == 2
    assert result.filled == 0.0
    assert result.status == "open"


def test_partial_entry_sizes_the_bracket_to_the_filled_qty(settings, monkeypatch):
    fake = FakeVenue(
        OrderResult(
            id="e1",
            client_id=None,
            status="open",
            filled=0.03,
            avg_price=65_000.0,
        )
    )
    monkeypatch.setattr("cfos.execution.build_exchange", lambda _settings: fake)

    report = execute(settings, make_plan(settings, take_profits=[67_000.0, 68_500.0]))

    assert report.verdict is Verdict.EXECUTED, report.messages
    assert report.position is not None
    assert report.position["qty"] == pytest.approx(0.03)
    assert any("partial entry" in message for message in report.messages)
    assert any("only the visible filled quantity" in m for m in report.messages)

    entry, stop, *tps = fake.requests
    assert entry.type == "market"
    assert stop.type == "stop_market"
    assert stop.amount == pytest.approx(0.03)
    assert sum(tp.amount for tp in tps) == pytest.approx(0.03)
    assert all(tp.type == "take_profit_market" for tp in tps)

    rows = Journal(settings.db_path).open_positions()
    assert len(rows) == 1
    assert rows[0]["qty"] == pytest.approx(0.03)


def test_unconfirmed_entry_closes_a_visible_venue_position(settings, monkeypatch):
    fake = FakeVenue(
        OrderResult(
            id="e1", client_id=None, status="open", filled=0.0, avg_price=None
        ),
        visible_after_entry=[venue_position()],
    )
    monkeypatch.setattr("cfos.execution.build_exchange", lambda _settings: fake)
    monkeypatch.setattr("cfos.execution.time.sleep", lambda _seconds: None)

    report = execute(settings, make_plan(settings))

    assert report.verdict is Verdict.ERROR
    assert fake.polls == 5
    assert fake.closed == [None]
    assert any("defensive close submitted" in m for m in report.messages)
    assert Journal(settings.db_path).open_positions() == []


def test_create_order_exception_checks_the_venue_before_closing(settings, monkeypatch):
    fake = FakeVenue(RuntimeError("request lost"), visible_after_entry=[venue_position()])
    monkeypatch.setattr("cfos.execution.build_exchange", lambda _settings: fake)

    report = execute(settings, make_plan(settings))

    assert report.verdict is Verdict.ERROR
    assert fake.closed == [None]
    assert any("defensive close submitted" in m for m in report.messages)


def test_create_order_exception_reports_a_clean_failure(settings, monkeypatch):
    fake = FakeVenue(RuntimeError("request lost"))
    monkeypatch.setattr("cfos.execution.build_exchange", lambda _settings: fake)

    report = execute(settings, make_plan(settings))

    assert report.verdict is Verdict.ERROR
    assert fake.closed == []
    assert any("no venue position found" in m for m in report.messages)
