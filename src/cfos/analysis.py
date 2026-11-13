"""Deterministic market-regime classification from OHLCV.

No ML, no hidden weights: EMA structure, ATR expansion and range compression,
with funding as a crowding overlay. The output is a label plus the numbers that
produced it, so a human can disagree with the label.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from .models import utcnow


@dataclass
class RegimeReport:
    symbol: str
    timeframe: str
    price: float
    ema20: float
    ema50: float
    ema200: float
    atr_pct: float
    range_pct: float
    regime: str
    trend: str
    funding_rate: float | None = None
    funding_bias: str = "unknown"
    notes: list[str] = field(default_factory=list)
    ts: str = field(default_factory=utcnow)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def ema(values: list[float], period: int) -> float:
    if len(values) < period:
        raise ValueError(f"need at least {period} values, got {len(values)}")
    multiplier = 2.0 / (period + 1)
    current = sum(values[:period]) / period
    for value in values[period:]:
        current = (value - current) * multiplier + current
    return current


def atr(candles: list[list[float]], period: int = 14) -> float:
    if len(candles) < period + 1:
        raise ValueError(f"need at least {period + 1} candles")
    ranges: list[float] = []
    for index in range(1, len(candles)):
        high = float(candles[index][2])
        low = float(candles[index][3])
        prev_close = float(candles[index - 1][4])
        ranges.append(max(high - low, abs(high - prev_close), abs(low - prev_close)))
    recent = ranges[-period:]
    return sum(recent) / len(recent)


def funding_bias(rate: float | None) -> str:
    if rate is None:
        return "unknown"
    if rate >= 0.0005:
        return "crowded_long"
    if rate <= -0.0005:
        return "crowded_short"
    return "neutral"


def compute_regime(
    candles: list[list[float]],
    *,
    symbol: str = "",
    timeframe: str = "4h",
    funding_rate: float | None = None,
) -> RegimeReport:
    if len(candles) < 60:
        raise ValueError(f"need at least 60 candles, got {len(candles)}")
    closes = [float(c[4]) for c in candles]
    highs = [float(c[2]) for c in candles]
    lows = [float(c[3]) for c in candles]
    price = closes[-1]
    if price <= 0:
        raise ValueError("last close must be > 0")

    e20 = ema(closes, 20)
    e50 = ema(closes, 50)
    e200 = ema(closes, min(200, len(closes)))
    atr_pct = atr(candles) / price * 100.0
    window = min(20, len(candles))
    range_pct = (max(highs[-window:]) - min(lows[-window:])) / price * 100.0

    if atr_pct >= 6.0:
        regime = "chaos"
        trend = "none"
    elif range_pct <= 2.5 and atr_pct <= 2.0:
        regime = "squeeze"
        trend = "coiling"
    elif price > e50 > e200 and e20 > e50:
        regime = "trend_up"
        trend = "up"
    elif price < e50 < e200 and e20 < e50:
        regime = "trend_down"
        trend = "down"
    else:
        regime = "range"
        trend = "flat"

    bias = funding_bias(funding_rate)
    notes: list[str] = []
    if regime == "chaos":
        notes.append("ATR >= 6%: widen stops or stand aside; leverage is the enemy")
    if regime == "squeeze" and bias == "crowded_long":
        notes.append("compression with crowded longs: downside sweep risk")
    if regime == "squeeze" and bias == "crowded_short":
        notes.append("compression with crowded shorts: upside squeeze risk")
    if regime == "trend_up" and bias == "crowded_long":
        notes.append("trend is real but positioning is one-sided; avoid late chases")
    if regime == "trend_down" and bias == "crowded_short":
        notes.append("trend is real but shorts are crowded; rallies can be violent")

    return RegimeReport(
        symbol=symbol,
        timeframe=timeframe,
        price=price,
        ema20=e20,
        ema50=e50,
        ema200=e200,
        atr_pct=atr_pct,
        range_pct=range_pct,
        regime=regime,
        trend=trend,
        funding_rate=funding_rate,
        funding_bias=bias,
        notes=notes,
    )
