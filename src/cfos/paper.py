"""Deterministic paper exchange.

Fills are simulated, but the accounting is real: fees, slippage, partial
reduces, stop/take-profit triggers and liquidation. State persists to JSON so
an agent restart does not erase the book.

Public prices are fetched read-only through CCXT when no explicit price is set.
Tests always set prices explicitly and never touch the network.
"""

from __future__ import annotations

import json
import os
import time
import uuid
from pathlib import Path
from typing import Any

from .config import Settings
from .exchange import BaseExchange, ExchangeError
from .models import (
    MarketSnapshot,
    OrderRequest,
    OrderResult,
    PositionSnapshot,
    Side,
    utcnow,
)

EPS = 1e-12


class PaperExchange(BaseExchange):
    name = "paper"

    def __init__(self, settings: Settings, *, autoload: bool = True):
        self.settings = settings
        self.fee_pct = settings.risk.taker_fee_pct
        self.slip_bps = settings.risk.paper_slippage_bps
        self.path: Path = settings.data_dir / "paper_state.json"
        self._price_cache: dict[str, tuple[float, float]] = {}
        self.state: dict[str, Any] = {
            "cash": float(settings.equity),
            "realized": 0.0,
            "fees": 0.0,
            "positions": {},
            "orders": {},
            "prices": {},
            "leverage": {},
            "trades": [],
            "created_at": utcnow(),
        }
        if autoload:
            self._load()

    # -- persistence ----------------------------------------------------
    def _load(self) -> None:
        if not self.path.is_file():
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                self.state.update(data)
        except (OSError, json.JSONDecodeError) as exc:
            raise ExchangeError(f"paper state is unreadable: {exc}") from exc
        # Migrate v1.0 pre-ladder positions: a scalar tp becomes one level.
        for pos in self.state.get("positions", {}).values():
            if pos.get("tp") is not None and not pos.get("tps"):
                pos["tps"] = [{"price": float(pos["tp"]), "qty": float(pos["qty"])}]

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.state, indent=2), encoding="utf-8")
        os.replace(tmp, self.path)

    def reset(self, equity: float | None = None) -> None:
        self.state = {
            "cash": float(equity if equity is not None else self.settings.equity),
            "realized": 0.0,
            "fees": 0.0,
            "positions": {},
            "orders": {},
            "prices": {},
            "leverage": {},
            "trades": [],
            "created_at": utcnow(),
        }
        self._save()

    # -- market ---------------------------------------------------------
    def load(self) -> None:
        return None

    def set_price(self, symbol: str, price: float) -> None:
        if price <= 0:
            raise ValueError("price must be > 0")
        self.state["prices"][symbol] = float(price)
        self._price_cache[symbol] = (float(price), time.time())
        self._process(symbol)
        self._save()

    def _public_price(self, symbol: str) -> float:
        import ccxt

        klass = getattr(ccxt, self.settings.venue.name)
        client = klass(
            {
                "enableRateLimit": True,
                "options": {"defaultType": self.settings.venue.default_type},
            }
        )
        ticker = client.fetch_ticker(symbol)
        price = float(ticker.get("last") or ticker.get("close") or 0.0)
        if price <= 0:
            raise ExchangeError(f"no public price for {symbol}")
        self.state["prices"][symbol] = price
        self._price_cache[symbol] = (price, time.time())
        return price

    def price(self, symbol: str) -> float:
        cached = self._price_cache.get(symbol)
        if cached and time.time() - cached[1] < 30:
            return cached[0]
        if symbol in self.state["prices"]:
            return float(self.state["prices"][symbol])
        return self._public_price(symbol)

    def market(self, symbol: str) -> MarketSnapshot:
        return MarketSnapshot(symbol=symbol, price=self.price(symbol), ts=utcnow())

    def equity(self) -> float:
        unrealized = 0.0
        for symbol, pos in self.state["positions"].items():
            mark = self.price(symbol)
            unrealized += self._unrealized(pos, mark)
        return float(self.state["cash"]) + unrealized

    @staticmethod
    def _unrealized(pos: dict[str, Any], mark: float) -> float:
        qty = float(pos["qty"])
        entry = float(pos["entry"])
        if pos["side"] == Side.LONG.value:
            return (mark - entry) * qty
        return (entry - mark) * qty

    # -- account --------------------------------------------------------
    def balance(self) -> float:
        return self.equity()

    def positions(self) -> list[PositionSnapshot]:
        out: list[PositionSnapshot] = []
        for symbol, pos in self.state["positions"].items():
            mark = self.price(symbol)
            out.append(
                PositionSnapshot(
                    symbol=symbol,
                    side=Side(pos["side"]),
                    qty=float(pos["qty"]),
                    entry=float(pos["entry"]),
                    mark=mark,
                    leverage=float(pos.get("leverage") or 1.0),
                    unrealized=self._unrealized(pos, mark),
                    liq_price=pos.get("liq"),
                    stop=pos.get("stop"),
                    take_profit=pos.get("tp"),
                )
            )
        return out

    def set_leverage(self, symbol: str, leverage: float) -> None:
        self.state["leverage"][symbol] = float(leverage)
        self._save()

    def ohlcv(
        self, symbol: str, timeframe: str = "4h", limit: int = 200
    ) -> list[list[float]]:
        """Read-only public candles. Paper needs a real market to classify."""
        import ccxt

        klass = getattr(ccxt, self.settings.venue.name)
        client = klass(
            {
                "enableRateLimit": True,
                "options": {"defaultType": self.settings.venue.default_type},
            }
        )
        return client.fetch_ohlcv(symbol, timeframe, limit=limit)

    def contract_size(self, symbol: str) -> float:
        return 1.0

    def qty_step(self, symbol: str) -> float:
        return 0.001

    def min_qty(self, symbol: str) -> float:
        return 0.0

    # -- orders ---------------------------------------------------------
    def _slip(self, price: float, buying: bool) -> float:
        drift = price * self.slip_bps / 10_000
        return price + drift if buying else price - drift

    def create_order(self, request: OrderRequest) -> OrderResult:
        symbol = request.symbol
        price = self.price(symbol)
        client_id = request.client_id or f"paper-{uuid.uuid4().hex[:10]}"

        if request.type in ("stop_market", "take_profit_market"):
            pos = self.state["positions"].get(symbol)
            if pos is None:
                raise ExchangeError(f"cannot attach {request.type} without a position")
            if request.type == "stop_market":
                pos["stop"] = request.price
            else:
                # Multiple take-profits are a ladder: each level carries its
                # own slice quantity and fires independently.
                tps = pos.setdefault("tps", [])
                tps.append(
                    {
                        "price": float(request.price),
                        "qty": float(request.amount),
                        "client_id": client_id,
                    }
                )
                pos["tp"] = tps[0]["price"]
            self._save()
            return OrderResult(
                id=f"paper-{uuid.uuid4().hex[:10]}",
                client_id=client_id,
                status="open",
                filled=0.0,
                avg_price=request.price,
            )

        if request.type == "market":
            fill = self._slip(price, request.side == "buy")
            if request.reduce_only:
                return self._reduce(symbol, request.amount, fill, client_id)
            return self._open(request, fill, client_id)

        if request.type == "limit":
            if request.price is None:
                raise ExchangeError("limit order requires a price")
            crosses = (request.side == "buy" and price <= request.price) or (
                request.side == "sell" and price >= request.price
            )
            if crosses:
                if request.reduce_only:
                    return self._reduce(symbol, request.amount, request.price, client_id)
                return self._open(request, request.price, client_id)
            order_id = f"paper-{uuid.uuid4().hex[:10]}"
            self.state["orders"][order_id] = {
                "id": order_id,
                "client_id": client_id,
                "symbol": symbol,
                "side": request.side,
                "qty": request.amount,
                "price": request.price,
                "reduce_only": request.reduce_only,
                "status": "open",
                "ts": utcnow(),
            }
            self._save()
            return OrderResult(
                id=order_id,
                client_id=client_id,
                status="open",
                filled=0.0,
                avg_price=None,
            )

        raise ExchangeError(f"unsupported paper order type {request.type!r}")

    def _open(self, request: OrderRequest, fill: float, client_id: str) -> OrderResult:
        symbol = request.symbol
        if symbol in self.state["positions"]:
            raise ExchangeError(f"paper account already has a position in {symbol}")
        side = Side.LONG if request.side == "buy" else Side.SHORT
        qty = float(request.amount)
        if qty <= 0:
            raise ExchangeError("qty must be > 0")
        leverage = float(self.state["leverage"].get(symbol, 1.0))
        fee = fill * qty * self.fee_pct / 100.0
        self.state["cash"] -= fee
        self.state["fees"] += fee
        self.state["positions"][symbol] = {
            "id": None,
            "symbol": symbol,
            "side": side.value,
            "qty": qty,
            "entry": fill,
            "stop": None,
            "tp": None,
            "tps": [],
            "leverage": leverage,
            "liq": self._liq(side, fill, leverage),
            "opened_at": utcnow(),
            "fees": fee,
            "risk_amount": 0.0,
        }
        self._save()
        return OrderResult(
            id=f"paper-{uuid.uuid4().hex[:10]}",
            client_id=client_id,
            status="closed",
            filled=qty,
            avg_price=fill,
        )

    def _liq(self, side: Side, entry: float, leverage: float) -> float:
        mmr = self.settings.risk.maintenance_margin_rate
        if leverage <= 1:
            return 0.0
        if side is Side.LONG:
            return max(0.0, entry * (1 - 1 / leverage + mmr))
        return entry * (1 + 1 / leverage - mmr)

    def tag_position(
        self, symbol: str, *, position_id: str, risk_amount: float
    ) -> None:
        pos = self.state["positions"].get(symbol)
        if pos:
            pos["id"] = position_id
            pos["risk_amount"] = risk_amount
            self._save()

    def _reduce(self, symbol: str, qty: float, fill: float, client_id: str) -> OrderResult:
        pos = self.state["positions"].get(symbol)
        if pos is None:
            raise ExchangeError(f"no paper position in {symbol}")
        return self._close(symbol, min(qty, float(pos["qty"])), fill, client_id, "manual")

    def _close(
        self, symbol: str, qty: float, fill: float, client_id: str, reason: str
    ) -> OrderResult:
        pos = self.state["positions"][symbol]
        qty = min(qty, float(pos["qty"]))
        side = Side(pos["side"])
        entry = float(pos["entry"])
        gross = (fill - entry) * qty if side is Side.LONG else (entry - fill) * qty
        fee = fill * qty * self.fee_pct / 100.0
        net = gross - fee
        self.state["cash"] += net
        self.state["realized"] += net
        self.state["fees"] += fee
        pos["qty"] = float(pos["qty"]) - qty
        remaining = float(pos["qty"])
        if remaining <= EPS:
            del self.state["positions"][symbol]
        self.state["trades"].append(
            {
                "position_id": pos.get("id"),
                "symbol": symbol,
                "side": side.value,
                "qty": qty,
                "entry": entry,
                "exit": fill,
                "pnl": net,
                "fees": fee,
                "reason": reason,
                "remaining": remaining,
                "ts": utcnow(),
            }
        )
        self._save()
        return OrderResult(
            id=f"paper-{uuid.uuid4().hex[:10]}",
            client_id=client_id,
            status="closed",
            filled=qty,
            avg_price=fill,
        )

    def _process(self, symbol: str) -> None:
        pos = self.state["positions"].get(symbol)
        if not pos:
            return
        price = float(self.state["prices"][symbol])
        side = Side(pos["side"])
        liq = float(pos.get("liq") or 0.0)

        # Liquidation first: it is the outcome a stop was meant to prevent.
        if liq > 0 and ((side is Side.LONG and price <= liq) or (side is Side.SHORT and price >= liq)):
            self._close(symbol, float(pos["qty"]), liq, f"paper-liq-{uuid.uuid4().hex[:6]}", "liquidation")
            return

        stop = pos.get("stop")
        if stop is not None:
            hit = (side is Side.LONG and price <= stop) or (side is Side.SHORT and price >= stop)
            if hit:
                fill = self._slip(float(stop), side is Side.SHORT)
                self._close(symbol, float(pos["qty"]), fill, f"paper-stop-{uuid.uuid4().hex[:6]}", "stop")
                return

        # Walk the take-profit ladder. A single tick can fill several levels;
        # each one closes only its own slice.
        while symbol in self.state["positions"]:
            pos = self.state["positions"][symbol]
            tps = pos.get("tps") or []
            if not tps:
                break
            tp = float(tps[0]["price"])
            hit = (side is Side.LONG and price >= tp) or (side is Side.SHORT and price <= tp)
            if not hit:
                break
            fill = self._slip(tp, side is Side.SHORT)
            qty = min(float(tps[0].get("qty") or pos["qty"]), float(pos["qty"]))
            tps.pop(0)
            if symbol in self.state["positions"]:
                self.state["positions"][symbol]["tp"] = (
                    tps[0]["price"] if tps else None
                )
            self._close(
                symbol,
                qty,
                fill,
                f"paper-tp-{uuid.uuid4().hex[:6]}",
                "take_profit",
            )

    def cancel(self, order_id: str, symbol: str) -> None:
        order = self.state["orders"].get(order_id)
        if not order:
            raise ExchangeError(f"unknown paper order {order_id}")
        order["status"] = "canceled"
        self._save()

    def order(self, order_id: str, symbol: str) -> OrderResult:
        """Paper fills are synchronous, so a created order is already final.

        Execution only polls this on real venues; this implementation keeps
        the interface honest without inventing a fake open-state machine.
        """
        order = self.state["orders"].get(order_id)
        if order:
            return OrderResult(
                id=order_id,
                client_id=order.get("client_id"),
                status=str(order.get("status") or "closed"),
                filled=float(order.get("qty") or 0.0),
                avg_price=float(order["price"]) if order.get("price") else None,
                raw=dict(order),
            )
        return OrderResult(
            id=order_id,
            client_id=None,
            status="closed",
            filled=0.0,
            avg_price=None,
        )

    def cancel_all(self, symbol: str) -> None:
        for order in self.state["orders"].values():
            if order["symbol"] == symbol and order["status"] == "open":
                order["status"] = "canceled"
        self._save()

    def close_position(self, symbol: str, qty: float | None = None) -> OrderResult:
        pos = self.state["positions"].get(symbol)
        if pos is None:
            raise ExchangeError(f"no open paper position in {symbol}")
        price = self.price(symbol)
        fill = self._slip(price, Side(pos["side"]).close_side == "buy")
        amount = abs(qty) if qty is not None else float(pos["qty"])
        return self._close(symbol, amount, fill, f"paper-close-{uuid.uuid4().hex[:6]}", "manual")

    def drain_trades(self) -> list[dict[str, Any]]:
        trades = list(self.state.get("trades", []))
        self.state["trades"] = []
        self._save()
        return trades
