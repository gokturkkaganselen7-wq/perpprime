"""Typed domain objects shared by the risk kernel, executor, CLI and MCP server."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field, is_dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def json_default(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if is_dataclass(value):
        return asdict(value)
    if isinstance(value, datetime):
        return value.isoformat()
    raise TypeError(f"not serializable: {type(value)!r}")


def dumps(value: Any, indent: int | None = 2) -> str:
    return json.dumps(value, default=json_default, indent=indent, sort_keys=False)


class Side(str, Enum):
    LONG = "long"
    SHORT = "short"

    @property
    def order_side(self) -> str:
        """CCXT order side for opening this position."""
        return "buy" if self is Side.LONG else "sell"

    @property
    def close_side(self) -> str:
        return "sell" if self is Side.LONG else "buy"

    @property
    def sign(self) -> int:
        return 1 if self is Side.LONG else -1


class Mode(str, Enum):
    PAPER = "paper"
    TESTNET = "testnet"
    LIVE = "live"


class GateStatus(str, Enum):
    ALLOW = "ALLOW"
    WARN = "WARN"
    BLOCK = "BLOCK"


class Verdict(str, Enum):
    EXECUTED = "EXECUTED"
    BLOCKED = "BLOCKED"
    DRY_RUN = "DRY_RUN"
    ERROR = "ERROR"


@dataclass
class GateResult:
    name: str
    status: GateStatus
    reason: str
    value: Any = None

    @property
    def blocked(self) -> bool:
        return self.status is GateStatus.BLOCK


@dataclass
class MarketSnapshot:
    symbol: str
    price: float
    ts: str = field(default_factory=utcnow)
    funding_rate: float | None = None
    open_interest: float | None = None
    quote_volume_24h: float | None = None
    spread_bps: float | None = None

    def age_seconds(self, now: datetime | None = None) -> float:
        then = datetime.fromisoformat(self.ts)
        current = now or datetime.now(timezone.utc)
        return max(0.0, (current - then).total_seconds())


@dataclass
class AccountState:
    equity: float
    peak_equity: float = 0.0
    day_start_equity: float = 0.0
    week_start_equity: float = 0.0
    realized_pnl_today: float = 0.0
    consecutive_losses: int = 0
    last_loss_ts: str | None = None
    open_positions: int = 0
    open_risk: float = 0.0
    open_symbols: list[str] = field(default_factory=list)
    cluster_counts: dict[str, int] = field(default_factory=dict)
    trades_today: int = 0

    def __post_init__(self) -> None:
        if self.peak_equity <= 0:
            self.peak_equity = self.equity
        if self.day_start_equity <= 0:
            self.day_start_equity = self.equity
        if self.week_start_equity <= 0:
            self.week_start_equity = self.equity

    @property
    def drawdown_pct(self) -> float:
        if self.peak_equity <= 0:
            return 0.0
        return max(0.0, (self.peak_equity - self.equity) / self.peak_equity * 100.0)

    @property
    def daily_pnl_pct(self) -> float:
        if self.day_start_equity <= 0:
            return 0.0
        return (self.equity - self.day_start_equity) / self.day_start_equity * 100.0


@dataclass
class SizeResult:
    ok: bool
    qty: float
    notional: float
    leverage: float
    risk_amount: float
    risk_pct: float
    stop_distance: float
    liq_price: float | None
    warnings: list[str] = field(default_factory=list)
    reason: str = ""


@dataclass
class TradePlan:
    symbol: str
    side: Side
    entry: float
    stop: float
    take_profits: list[float]
    qty: float
    leverage: float
    risk_amount: float
    notional: float
    rr: float
    mode: Mode
    venue: str
    intent: str = "manual"
    rationale: str = ""
    invalidation: str = ""
    id: str = ""
    ts: str = field(default_factory=utcnow)
    warnings: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.id:
            self.id = f"{self.symbol.replace('/', '').replace(':', '')}-{self.side.value}-{self.ts}"

    @property
    def risk_per_unit(self) -> float:
        return abs(self.entry - self.stop)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class OrderRequest:
    symbol: str
    side: str
    type: str
    amount: float
    price: float | None = None
    reduce_only: bool = False
    post_only: bool = False
    client_id: str | None = None
    params: dict[str, Any] = field(default_factory=dict)


@dataclass
class OrderResult:
    id: str | None
    client_id: str | None
    status: str
    filled: float
    avg_price: float | None
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class PositionSnapshot:
    symbol: str
    side: Side
    qty: float
    entry: float
    mark: float
    leverage: float
    unrealized: float
    liq_price: float | None = None
    stop: float | None = None
    take_profit: float | None = None


@dataclass
class ExecutionReport:
    ok: bool
    verdict: Verdict
    mode: Mode
    symbol: str
    plan: dict[str, Any] | None = None
    orders: list[dict[str, Any]] = field(default_factory=list)
    messages: list[str] = field(default_factory=list)
    gates: list[dict[str, Any]] = field(default_factory=list)
    position: dict[str, Any] | None = None


@dataclass
class RoutePlan:
    intent: str
    skills: list[str]
    gates: list[str]
    note: str = ""
