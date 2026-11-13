"""Executable intent router for crypto futures.

The prose contract lives in skills/cfos-router/SKILL.md. This module is the
deterministic part: it maps an intent to a skill chain and the gates that must
be open before the chain may place risk.
"""

from __future__ import annotations

import re

from .models import RoutePlan

GATES = [
    "kill_switch",
    "universe",
    "daily_loss",
    "weekly_loss",
    "drawdown",
    "trade_count",
    "max_positions",
    "max_per_symbol",
    "correlation",
    "portfolio_heat",
    "cooldown",
    "reward_risk",
    "data_freshness",
    "spread",
    "leverage",
]

INTENTS: dict[str, dict] = {
    "regime": {
        "skills": ["cfos-regime", "cfos-derivatives-sentiment", "cfos-liquidation-map"],
        "note": "No trade. Establish the market state before any setup.",
    },
    "trend": {
        "skills": ["cfos-regime", "cfos-trend", "cfos-sizing", "cfos-execution"],
        "note": "Trend continuation. Entry only with the higher-timeframe bias.",
    },
    "breakout": {
        "skills": ["cfos-regime", "cfos-breakout", "cfos-sizing", "cfos-execution"],
        "note": "Range compression breakout. Reject in high-funding chases.",
    },
    "mean_reversion": {
        "skills": [
            "cfos-regime",
            "cfos-mean-reversion",
            "cfos-derivatives-sentiment",
            "cfos-sizing",
        ],
        "note": "Fade extension. Never against a regime break.",
    },
    "funding_carry": {
        "skills": ["cfos-derivatives-sentiment", "cfos-funding-carry", "cfos-sizing", "cfos-execution"],
        "note": "Delta-neutral or basis trade. Liquidation risk must be modelled.",
    },
    "liquidation_reversal": {
        "skills": ["cfos-liquidation-map", "cfos-liquidation-reversal", "cfos-sizing"],
        "note": "Wick reversal after a liquidation cascade. Wait for reclaim.",
    },
    "grid": {
        "skills": ["cfos-regime", "cfos-grid", "cfos-sizing", "cfos-ops"],
        "note": "Range-only. Kill the grid on a regime break.",
    },
    "news": {
        "skills": ["cfos-macro-calendar", "cfos-news-catalyst", "cfos-regime", "cfos-sizing"],
        "note": "Event risk. Size down or stand aside before the print.",
    },
    "hedge": {
        "skills": ["cfos-portfolio-state", "cfos-exit-management", "cfos-execution"],
        "note": "Reduce or hedge existing exposure, never add risk.",
    },
    "size": {
        "skills": ["cfos-sizing", "cfos-risk-gate"],
        "note": "Sizing question only. Do not invent a setup.",
    },
    "review": {
        "skills": ["cfos-journal", "cfos-postmortem", "cfos-edge-review"],
        "note": "Review only. No new orders.",
    },
    "backtest": {
        "skills": ["cfos-backtest", "cfos-walk-forward", "cfos-edge-review"],
        "note": "Validation only. A backtest is not a trade signal.",
    },
    "ops": {
        "skills": ["cfos-ops", "cfos-data-quality"],
        "note": "Infrastructure. Do not place orders from an ops check.",
    },
}

_KEYWORDS: list[tuple[str, tuple[str, ...]]] = [
    ("grid", ("grid", "ızgara", "izgara", "dca")),
    ("funding_carry", ("funding", "basis", "carry", "taşıma", "tasima", "cash and carry")),
    (
        "liquidation_reversal",
        ("likidasyon", "liquidation", "liq map", "wick", "sweep", "stop hunt"),
    ),
    ("backtest", ("backtest", "geriye dönük", "geriye donuk", "walk forward", "optimiz")),
    ("review", ("review", "postmortem", "değerlendir", "degerlendir", "özet", "ozet", "günlük")),
    ("hedge", ("hedge", "koruma", "delta nötr", "delta notr", "reduce")),
    ("news", ("haber", "news", "cpi", "fomc", "nfp", "etkinlik", "takvim", "macro")),
    ("breakout", ("kırılım", "kirilim", "breakout", "range break", "sıkışma", "sikisma")),
    ("mean_reversion", ("mean reversion", "ortalamaya dönüş", "ortalamaya donus", "fade", "rsi")),
    ("trend", ("trend", "momentum", "takip", "ema", "sma", "yön", "yon")),
    ("size", ("boyut", "size", "kaç lot", "kac lot", "pozisyon büyüklüğü", "risk")),
    ("regime", ("rejim", "regime", "piyasa nasıl", "piyasa nasil", "risk iştahı", "risk istahi")),
]


def detect_intent(text: str) -> str:
    lowered = text.lower()
    for intent, words in _KEYWORDS:
        for word in words:
            if re.search(rf"(?<!\w){re.escape(word)}(?!\w)", lowered):
                return intent
    return "regime"


def route(intent: str | None = None, text: str | None = None) -> RoutePlan:
    if not intent and text:
        intent = detect_intent(text)
    intent = intent or "regime"
    if intent not in INTENTS:
        raise KeyError(f"unknown intent {intent!r}; choose from {sorted(INTENTS)}")
    entry = INTENTS[intent]
    return RoutePlan(
        intent=intent,
        skills=list(entry["skills"]),
        gates=list(GATES),
        note=str(entry["note"]),
    )


def catalogue() -> dict[str, list[str]]:
    return {name: list(entry["skills"]) for name, entry in INTENTS.items()}
