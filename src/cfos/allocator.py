"""Regime-aware strategy allocator: the "brain" the router never had.

The router maps a user intent to a skill chain. This module answers a
different question: given the current market regime and the measured
out-of-sample record of every strategy, is there anything worth trading at
all?

It is deliberately fail-closed. A strategy is only eligible when its
registry status is ``EDGE_CONFIRMED`` (or ``EDGE_CANDIDATE`` in paper mode),
its regime matches, and its measured OOS record clears the charter. With
today's data every strategy is ``NO_EDGE`` or ``FEE_BLOCKED``, so the
correct answer is ``NO_TRADE`` — and the allocator says exactly that.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

CONFIRMED = "EDGE_CONFIRMED"
CANDIDATE = "EDGE_CANDIDATE"
FEE_BLOCKED = "FEE_BLOCKED"
NO_EDGE = "NO_EDGE"
INSUFFICIENT = "INSUFFICIENT_DATA"

TRADEABLE = {CONFIRMED}


@dataclass(frozen=True)
class StrategyRecord:
    name: str
    skill: str
    regimes: tuple[str, ...]
    status: str
    chain: tuple[str, ...] = ()
    oos_avg: float | None = None
    positive_windows: int | None = None
    windows: int | None = None
    pf: float | None = None
    max_dd: float | None = None
    oos_trades: int | None = None
    evidence: str = ""
    note: str = ""

    @property
    def score(self) -> float:
        """Ranking score: OOS return per unit of drawdown."""
        if self.oos_avg is None:
            return float("-inf")
        return self.oos_avg / max(self.max_dd or 1.0, 0.1)


@dataclass
class Allocation:
    decision: str                    # TRADE or NO_TRADE
    regime: str
    strategy: str | None = None
    chain: list[str] = field(default_factory=list)
    oos_avg: float | None = None
    positive_windows: str | None = None
    max_dd: float | None = None
    reasons: list[str] = field(default_factory=list)
    rejected: list[dict[str, str]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "decision": self.decision,
            "regime": self.regime,
            "strategy": self.strategy,
            "chain": self.chain,
            "oos_avg": self.oos_avg,
            "positive_windows": self.positive_windows,
            "max_dd": self.max_dd,
            "reasons": self.reasons,
            "rejected": self.rejected,
        }


def load_registry(path: str | Path | None = None) -> list[StrategyRecord]:
    root = Path(__file__).resolve().parents[2]
    path = Path(path) if path else root / "config" / "strategy_registry.json"
    data = json.loads(Path(path).read_text())
    out = []
    for item in data.get("strategies", []):
        out.append(StrategyRecord(
            name=item["name"],
            skill=item.get("skill", ""),
            regimes=tuple(item.get("regimes", [])),
            status=item.get("status", NO_EDGE),
            chain=tuple(item.get("chain", [])),
            oos_avg=item.get("oos_avg"),
            positive_windows=item.get("positive_windows"),
            windows=item.get("windows"),
            pf=item.get("pf"),
            max_dd=item.get("max_dd"),
            oos_trades=item.get("oos_trades"),
            evidence=item.get("evidence", ""),
            note=item.get("note", ""),
        ))
    return out


def allocate(regime: str, records: list[StrategyRecord], *,
             kill_engaged: bool = False, allow_candidate: bool = False
             ) -> Allocation:
    """Pick at most one strategy for the current regime, or stand aside."""
    if kill_engaged:
        return Allocation("NO_TRADE", regime,
                          reasons=["kill switch engaged"])
    if regime == "chaos":
        return Allocation("NO_TRADE", regime,
                          reasons=["chaos regime: no new risk"])
    if regime == "warmup":
        return Allocation("NO_TRADE", regime,
                          reasons=["warmup: not enough history"])

    allowed = {CONFIRMED} | ({CANDIDATE} if allow_candidate else set())
    rejected: list[dict[str, str]] = []
    candidates: list[StrategyRecord] = []
    for rec in records:
        if regime not in rec.regimes:
            continue
        if rec.status in allowed:
            candidates.append(rec)
            continue
        rejected.append({"strategy": rec.name, "status": rec.status,
                         "why": rec.note or rec.evidence})

    if not candidates:
        return Allocation(
            "NO_TRADE", regime,
            reasons=[f"no strategy with a measured edge in '{regime}'"],
            rejected=rejected,
        )

    best = max(candidates, key=lambda r: r.score)
    return Allocation(
        "TRADE" if best.status == CONFIRMED else "PAPER",
        regime,
        strategy=best.name,
        chain=list(best.chain or (best.skill,)),
        oos_avg=best.oos_avg,
        positive_windows=(f"{best.positive_windows}/{best.windows}"
                          if best.positive_windows is not None else None),
        max_dd=best.max_dd,
        reasons=[best.evidence] if best.evidence else [],
        rejected=rejected,
    )
