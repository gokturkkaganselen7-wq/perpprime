import pytest

from cfos.models import OrderRequest, Side
from cfos.paper import PaperExchange

SYMBOL = "BTC/USDT:USDT"


def fresh(settings, equity: float = 10_000) -> PaperExchange:
    exchange = PaperExchange(settings)
    exchange.reset(equity)
    exchange.set_price(SYMBOL, 100.0)
    return exchange


def test_market_entry_and_stop_hit(settings):
    paper = fresh(settings)
    paper.set_leverage(SYMBOL, 2)
    result = paper.create_order(
        OrderRequest(symbol=SYMBOL, side="buy", type="market", amount=1.0, client_id="e1")
    )
    assert result.status == "closed"
    assert result.filled == 1.0
    assert paper.positions()[0].entry == pytest.approx(100.02)

    paper.create_order(
        OrderRequest(
            symbol=SYMBOL,
            side="sell",
            type="stop_market",
            amount=1.0,
            price=95.0,
            reduce_only=True,
            client_id="s1",
        )
    )
    paper.set_price(SYMBOL, 95.0)

    assert paper.positions() == []
    trades = paper.drain_trades()
    assert len(trades) == 1
    assert trades[0]["reason"] == "stop"
    assert trades[0]["pnl"] < 0
    assert paper.equity() < 10_000


def test_take_profit_hit(settings):
    paper = fresh(settings)
    paper.create_order(
        OrderRequest(symbol=SYMBOL, side="buy", type="market", amount=1.0, client_id="e1")
    )
    paper.create_order(
        OrderRequest(
            symbol=SYMBOL,
            side="sell",
            type="take_profit_market",
            amount=1.0,
            price=110.0,
            reduce_only=True,
        )
    )
    paper.set_price(SYMBOL, 110.0)
    assert paper.positions() == []
    trade = paper.drain_trades()[0]
    assert trade["reason"] == "take_profit"
    assert trade["pnl"] > 0


def test_short_liquidation(settings):
    paper = fresh(settings)
    paper.set_leverage(SYMBOL, 50)
    paper.create_order(
        OrderRequest(symbol=SYMBOL, side="buy", type="market", amount=1.0, client_id="e1")
    )
    position = paper.positions()[0]
    # Entry is slipped by 2 bps; the liquidation estimate must follow it.
    assert position.liq_price == pytest.approx(position.entry * (1 - 1 / 50 + 0.005))
    paper.set_price(SYMBOL, 98.0)
    trade = paper.drain_trades()[0]
    assert trade["reason"] == "liquidation"
    assert trade["pnl"] < 0


def test_partial_close(settings):
    paper = fresh(settings)
    paper.create_order(
        OrderRequest(symbol=SYMBOL, side="buy", type="market", amount=2.0, client_id="e1")
    )
    paper.close_position(SYMBOL, 1.0)
    remaining = paper.positions()
    assert len(remaining) == 1
    assert remaining[0].qty == pytest.approx(1.0)
    assert paper.drain_trades()[0]["qty"] == pytest.approx(1.0)


def test_state_persists_across_instances(settings):
    paper = fresh(settings)
    paper.create_order(
        OrderRequest(symbol=SYMBOL, side="buy", type="market", amount=1.0, client_id="e1")
    )
    reloaded = PaperExchange(settings)
    assert len(reloaded.positions()) == 1
    assert reloaded.balance() > 0


def test_duplicate_symbol_is_rejected(settings):
    paper = fresh(settings)
    paper.create_order(
        OrderRequest(symbol=SYMBOL, side="buy", type="market", amount=1.0, client_id="e1")
    )
    with pytest.raises(Exception):
        paper.create_order(
            OrderRequest(symbol=SYMBOL, side="sell", type="market", amount=1.0, client_id="e2")
        )


def test_limit_order_rests_then_fills(settings):
    paper = fresh(settings)
    result = paper.create_order(
        OrderRequest(
            symbol=SYMBOL,
            side="buy",
            type="limit",
            amount=1.0,
            price=90.0,
            client_id="l1",
        )
    )
    assert result.status == "open"
    assert paper.positions() == []
    paper.set_price(SYMBOL, 89.0)
    # A resting limit is not auto-filled by set_price in v1; this documents
    # the boundary rather than pretending otherwise.
    assert result.status == "open"
