"""Exchange adapters. CCXT for real venues, a deterministic simulator for paper.

The rest of the system only sees ``BaseExchange``. That is what makes paper,
testnet and live the same code path with different risk.
"""

from __future__ import annotations

import math
import time
from abc import ABC, abstractmethod
from typing import Any

from .config import Settings
from .models import (
    MarketSnapshot,
    OrderRequest,
    OrderResult,
    PositionSnapshot,
    Side,
)


class ExchangeError(RuntimeError):
    pass


def venue_leverage(leverage: float) -> int:
    """Integer leverage a venue accepts, never below the effective exposure."""
    return max(1, math.ceil(leverage - 1e-9))


def step_from_market(market: dict[str, Any]) -> float:
    """Derive the quantity step from a CCXT market.

    CCXT venues use one of two precision conventions:
      * TICK_SIZE:      precision.amount is the step itself (0.001)
      * DECIMAL_PLACES: precision.amount is a digit count (3)

    Treating 0.001 as a digit count yields a step of 1.0 and silently rejects
    every fractional BTC order, so the distinction matters.
    """
    precision = (market.get("precision") or {}).get("amount")
    if isinstance(precision, (int, float)) and precision >= 0:
        if precision >= 1:
            return float(f"{10 ** -int(precision):.10f}")
        if precision > 0:
            return float(precision)
        # DECIMAL_PLACES=0 means whole units. A zero tick size is invalid,
        # so this is the only sensible reading.
        return 1.0
    min_amount = ((market.get("limits") or {}).get("amount") or {}).get("min")
    return float(min_amount or 0.001)


class BaseExchange(ABC):
    name: str = "base"

    @abstractmethod
    def load(self) -> None: ...

    @abstractmethod
    def market(self, symbol: str) -> MarketSnapshot: ...

    @abstractmethod
    def positions(self) -> list[PositionSnapshot]: ...

    @abstractmethod
    def balance(self) -> float: ...

    @abstractmethod
    def set_leverage(self, symbol: str, leverage: float) -> None: ...

    @abstractmethod
    def ohlcv(
        self, symbol: str, timeframe: str = "4h", limit: int = 200
    ) -> list[list[float]]: ...

    @abstractmethod
    def create_order(self, request: OrderRequest) -> OrderResult: ...

    @abstractmethod
    def order(self, order_id: str, symbol: str) -> OrderResult: ...

    @abstractmethod
    def cancel(self, order_id: str, symbol: str) -> None: ...

    @abstractmethod
    def cancel_all(self, symbol: str) -> None: ...

    @abstractmethod
    def close_position(self, symbol: str, qty: float | None = None) -> OrderResult: ...

    def contract_size(self, symbol: str) -> float:
        return 1.0

    def qty_step(self, symbol: str) -> float:
        return 0.001

    def min_qty(self, symbol: str) -> float:
        return 0.0

    def resolve_symbol(self, symbol: str) -> str:
        return symbol


class CcxtExchange(BaseExchange):
    """Thin, explicit CCXT wrapper. No hidden state, no magic."""
    def __init__(self, settings: Settings):
        try:
            import ccxt  # noqa: F401
        except ImportError as exc:  # pragma: no cover - depends on environment
            raise ExchangeError(
                "ccxt is not installed. Run: uv pip install --python .venv/bin/python ccxt"
            ) from exc
        import ccxt

        self.settings = settings
        self.name = settings.venue.name
        self._ccxt = ccxt
        klass = getattr(ccxt, settings.venue.name, None)
        if klass is None:
            raise ExchangeError(f"ccxt has no exchange id {settings.venue.name!r}")
        self.ex = klass(
            {
                "apiKey": settings.api_key,
                "secret": settings.api_secret,
                "password": settings.api_password,
                "enableRateLimit": True,
                "adjustForTimeDifference": True,
                "options": {"defaultType": settings.venue.default_type},
            }
        )
        # Sandbox only for testnet. Live never silently points at a sandbox.
        if settings.mode.value == "testnet":
            self.ex.set_sandbox_mode(True)
        self._markets_loaded = False

    def load(self) -> None:
        if not self._markets_loaded:
            self.ex.load_markets()
            self._markets_loaded = True

    def resolve_symbol(self, symbol: str) -> str:
        self.load()
        if symbol in self.ex.markets:
            return symbol
        candidate = symbol if ":" in symbol else (
            f"{symbol}:USDT" if "/" in symbol else f"{symbol}/USDT:USDT"
        )
        if candidate in self.ex.markets:
            return candidate
        raise ExchangeError(f"unknown symbol {symbol!r} on {self.name}")

    def contract_size(self, symbol: str) -> float:
        self.load()
        return float(self.ex.market(symbol).get("contractSize") or 1.0)

    def qty_step(self, symbol: str) -> float:
        self.load()
        return step_from_market(self.ex.market(symbol))

    def min_qty(self, symbol: str) -> float:
        self.load()
        market = self.ex.market(symbol)
        return float(((market.get("limits", {}) or {}).get("amount", {}) or {}).get("min") or 0.0)

    def market(self, symbol: str) -> MarketSnapshot:
        self.load()
        ticker = self.ex.fetch_ticker(symbol)
        price = float(ticker.get("last") or ticker.get("close") or 0.0)
        if price <= 0:
            raise ExchangeError(f"no price for {symbol}")

        funding = None
        try:
            fr = self.ex.fetch_funding_rate(symbol)
            funding = float(fr.get("fundingRate")) if fr.get("fundingRate") is not None else None
        except Exception:
            funding = None

        oi = None
        try:
            interest = self.ex.fetch_open_interest(symbol)
            oi = float(interest.get("openInterestAmount") or 0.0) or None
        except Exception:
            oi = None

        spread_bps = None
        try:
            book = self.ex.fetch_order_book(symbol, limit=5)
            bid = float(book["bids"][0][0])
            ask = float(book["asks"][0][0])
            if bid > 0 and ask > 0:
                spread_bps = (ask - bid) / ((ask + bid) / 2) * 10_000
        except Exception:
            spread_bps = None

        return MarketSnapshot(
            symbol=symbol,
            price=price,
            funding_rate=funding,
            open_interest=oi,
            quote_volume_24h=float(ticker.get("quoteVolume") or 0.0) or None,
            spread_bps=spread_bps,
        )

    def positions(self) -> list[PositionSnapshot]:
        self.load()
        raw = self.ex.fetch_positions()
        out: list[PositionSnapshot] = []
        for item in raw:
            contracts = float(item.get("contracts") or 0.0)
            if contracts == 0:
                continue
            side = Side.LONG if str(item.get("side")).lower() in ("long", "buy") else Side.SHORT
            out.append(
                PositionSnapshot(
                    symbol=item.get("symbol", ""),
                    side=side,
                    qty=contracts,
                    entry=float(item.get("entryPrice") or 0.0),
                    mark=float(item.get("markPrice") or item.get("lastPrice") or 0.0),
                    leverage=float(item.get("leverage") or 1.0),
                    unrealized=float(item.get("unrealizedPnl") or 0.0),
                    liq_price=float(item["liquidationPrice"])
                    if item.get("liquidationPrice")
                    else None,
                )
            )
        return out

    def balance(self) -> float:
        self.load()
        balance = self.ex.fetch_balance()
        # Risk limits must react to open losses. ccxt's parsed `total` for
        # Bybit is the wallet balance (unrealised PnL excluded), so prefer the
        # venue's equity: totalEquity on UNIFIED, equity on CONTRACT/SPOT.
        info = balance.get("info") or {}
        result = info.get("result") or {}
        entries = result.get("list")
        if isinstance(entries, list):
            for entry in entries:
                if not isinstance(entry, dict):
                    continue
                equity = entry.get("totalEquity")
                if equity is None:
                    equity = entry.get("equity")
                if equity is not None:
                    return float(equity)
                for coin in entry.get("coin") or []:
                    if coin.get("coin") == "USDT" and coin.get("equity") is not None:
                        return float(coin["equity"])
        usdt = balance.get("USDT") or {}
        total = usdt.get("total")
        if total is None:
            total = (balance.get("total") or {}).get("USDT")
        return float(total or 0.0)

    def set_leverage(self, symbol: str, leverage: float) -> None:
        self.load()
        params: dict[str, Any] = {}
        if self.name == "bybit":
            params["positionIdx"] = self.settings.venue.position_idx
        try:
            # Venues take integer leverage. Round UP so the margin posted is
            # never less than the notional/equity the plan was sized for;
            # rounding down can make a valid plan unaffordable.
            lev = venue_leverage(leverage)
            self.ex.set_leverage(lev, symbol, params=params)
        except Exception as exc:
            # Setting the same leverage twice can raise; do not fail the trade.
            if "leverage" not in str(exc).lower():
                raise ExchangeError(f"set_leverage failed: {exc}") from exc

    def ohlcv(
        self, symbol: str, timeframe: str = "4h", limit: int = 200
    ) -> list[list[float]]:
        self.load()
        return self.ex.fetch_ohlcv(self.resolve_symbol(symbol), timeframe, limit=limit)

    def _params(self, request: OrderRequest) -> dict[str, Any]:
        params: dict[str, Any] = dict(request.params)
        if request.reduce_only:
            params["reduceOnly"] = True
        if request.post_only:
            params["postOnly"] = True
        if request.client_id:
            if self.name == "binanceusdm":
                params.setdefault("newClientOrderId", request.client_id)
            else:
                params.setdefault("clientOrderId", request.client_id)
        if self.name == "bybit":
            params.setdefault("positionIdx", self.settings.venue.position_idx)
        return params

    def create_order(self, request: OrderRequest) -> OrderResult:
        self.load()
        symbol = self.resolve_symbol(request.symbol)
        amount = float(self.ex.amount_to_precision(symbol, request.amount))
        price = None
        if request.price is not None:
            price = float(self.ex.price_to_precision(symbol, request.price))

        order_type = request.type
        params = self._params(request)

        if order_type in ("stop_market", "take_profit_market"):
            trigger = price if price is not None else params.pop("stopPrice", None)
            if trigger is None:
                trigger = params.pop("triggerPrice", None)
            if trigger is None:
                raise ExchangeError(f"{order_type} requires a trigger price")
            # CCXT unified protective-order params. Bybit derives the required
            # triggerDirection from these and forces reduce-only; a generic
            # triggerPrice order would be rejected without triggerDirection.
            if order_type == "stop_market":
                params["stopLossPrice"] = trigger
            else:
                params["takeProfitPrice"] = trigger
            # A market order must not carry a limit price; the trigger lives
            # in params. Passing both makes venues reject or mis-type it.
            price = None
            order_type = "market"

        raw = self.ex.create_order(symbol, order_type, request.side, amount, price, params)
        return OrderResult(
            id=str(raw.get("id")) if raw.get("id") is not None else None,
            client_id=request.client_id,
            status=str(raw.get("status") or "submitted"),
            filled=float(raw.get("filled") or 0.0),
            avg_price=float(raw["average"]) if raw.get("average") else None,
            raw=raw,
        )

    def cancel(self, order_id: str, symbol: str) -> None:
        self.load()
        self.ex.cancel_order(order_id, self.resolve_symbol(symbol))

    def order(self, order_id: str, symbol: str) -> OrderResult:
        self.load()
        raw = self.ex.fetch_order(order_id, self.resolve_symbol(symbol))
        return OrderResult(
            id=str(raw.get("id")) if raw.get("id") is not None else order_id,
            client_id=raw.get("clientOrderId"),
            status=str(raw.get("status") or "unknown"),
            filled=float(raw.get("filled") or 0.0),
            avg_price=float(raw["average"]) if raw.get("average") else None,
            raw=raw,
        )

    def cancel_all(self, symbol: str) -> None:
        self.load()
        try:
            self.ex.cancel_all_orders(self.resolve_symbol(symbol))
        except Exception as exc:
            raise ExchangeError(f"cancel_all failed: {exc}") from exc

    def close_position(self, symbol: str, qty: float | None = None) -> OrderResult:
        self.load()
        symbol = self.resolve_symbol(symbol)
        positions = [p for p in self.positions() if p.symbol == symbol]
        if not positions:
            raise ExchangeError(f"no open position for {symbol}")
        position = positions[0]
        amount = abs(qty) if qty is not None else abs(position.qty)
        return self.create_order(
            OrderRequest(
                symbol=symbol,
                side=position.side.close_side,
                type="market",
                amount=amount,
                reduce_only=True,
            )
        )


def build_exchange(settings: Settings) -> BaseExchange:
    from .paper import PaperExchange

    if settings.mode.value == "paper":
        return PaperExchange(settings)
    return CcxtExchange(settings)


def public_market_snapshot(settings: Settings, symbol: str) -> MarketSnapshot:
    """Full market metadata for analysis, even in paper mode.

    Paper execution stays deterministic and offline (it only needs a price),
    but regime and sentiment analysis need funding, OI, volume and spread.
    This uses a read-only CCXT client with no credentials for paper mode.
    """
    from dataclasses import replace

    if settings.mode.value == "paper":
        public = replace(settings, api_key="", api_secret="", api_password="")
        return CcxtExchange(public).market(symbol)
    return CcxtExchange(settings).market(symbol)


def with_retries(fn, retries: int, base_delay: float = 0.5):
    """Retry only transient transport errors, never validation errors."""
    last: Exception | None = None
    for attempt in range(retries + 1):
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001 - classified below
            last = exc
            name = type(exc).__name__.lower()
            message = str(exc).lower()
            transient = any(
                token in name or token in message
                for token in ("network", "timeout", "ratelimit", "temporar", "502", "503", "504")
            )
            if not transient or attempt == retries:
                raise
            time.sleep(base_delay * (2**attempt))
    raise last  # pragma: no cover
