import pytest

from cfos.exchange import step_from_market, venue_leverage


def test_ccxt_protective_orders_use_unified_sl_tp_params():
    from types import SimpleNamespace

    from cfos.exchange import CcxtExchange
    from cfos.models import OrderRequest

    captured: dict = {}

    class FakeClient:
        def amount_to_precision(self, symbol, amount):
            return str(amount)

        def price_to_precision(self, symbol, price):
            return str(price)

        def create_order(self, symbol, type_, side, amount, price, params):
            captured.update(
                symbol=symbol,
                type=type_,
                side=side,
                amount=amount,
                price=price,
                params=params,
            )
            return {"id": "1", "status": "open", "filled": 0}

    ex = CcxtExchange.__new__(CcxtExchange)
    ex.name = "bybit"
    ex.settings = SimpleNamespace(venue=SimpleNamespace(position_idx=0))
    ex.ex = FakeClient()
    ex.load = lambda: None
    ex.resolve_symbol = lambda symbol: symbol

    ex.create_order(
        OrderRequest(
            symbol="BTC/USDT:USDT",
            side="sell",
            type="stop_market",
            amount=0.05,
            price=64_000.0,
            reduce_only=True,
            client_id="p-stop",
        )
    )
    # A protective order is a conditional MARKET order with a trigger in
    # params; Bybit derives triggerDirection from stopLossPrice/takeProfitPrice.
    assert captured["type"] == "market"
    assert captured["price"] is None
    assert captured["params"]["stopLossPrice"] == 64_000.0
    assert "stopPrice" not in captured["params"]
    assert "triggerPrice" not in captured["params"]
    assert captured["params"]["reduceOnly"] is True

    ex.create_order(
        OrderRequest(
            symbol="BTC/USDT:USDT",
            side="sell",
            type="take_profit_market",
            amount=0.025,
            price=67_000.0,
            reduce_only=True,
            client_id="p-tp1",
        )
    )
    assert captured["params"]["takeProfitPrice"] == 67_000.0
    assert "stopLossPrice" not in captured["params"]
    assert "stopPrice" not in captured["params"]


def market(precision=None, min_amount=None):
    return {
        "precision": {"amount": precision},
        "limits": {"amount": {"min": min_amount}},
    }


def test_tick_size_precision_is_the_step_itself():
    # Bybit-style: precision.amount == 0.001, not a digit count.
    assert step_from_market(market(0.001)) == 0.001


def test_decimal_places_precision_is_a_digit_count():
    assert step_from_market(market(3)) == 0.001
    assert step_from_market(market(0)) == 1.0


def test_missing_precision_falls_back_to_min_amount():
    assert step_from_market(market(None, 0.01)) == 0.01
    assert step_from_market({"precision": {}, "limits": {}}) == 0.001


def test_venue_leverage_rounds_up_and_never_goes_below_one():
    # Effective leverage below 1x still needs 1x venue leverage.
    assert venue_leverage(0.325) == 1
    # Rounding down would make a 2.5x plan unaffordable at 2x margin.
    assert venue_leverage(2.5) == 3
    assert venue_leverage(2.0) == 2
    assert venue_leverage(3.0) == 3
    assert venue_leverage(1.0000001) == 2


def test_balance_includes_unrealised_pnl():
    """Risk limits must see open losses, not just the wallet balance."""
    from cfos.exchange import CcxtExchange

    class FakeClient:
        def fetch_balance(self):
            return {
                "info": {
                    "result": {
                        "list": [
                            {
                                "accountType": "UNIFIED",
                                "totalEquity": "9750.0",
                                "totalWalletBalance": "10000.0",
                                "totalPerpUPL": "-250.0",
                            }
                        ]
                    }
                },
                # ccxt maps Bybit's walletBalance to `total`; using it would
                # hide the 250 USDT open loss.
                "USDT": {"total": 10000.0, "free": 9000.0, "used": 1000.0},
            }

    ex = CcxtExchange.__new__(CcxtExchange)
    ex.ex = FakeClient()
    ex.load = lambda: None

    assert ex.balance() == pytest.approx(9750.0)
