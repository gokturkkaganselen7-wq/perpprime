"""File-based guards: kill switch, live acknowledgement, symbol preflight."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .config import UniverseConfig
from .models import MarketSnapshot, Mode

LIVE_ACK_PHRASE = "I_UNDERSTAND_REAL_MONEY"


class KillSwitch:
    """A file is the switch. If it exists, no new risk is allowed.

    A file (rather than a database row or env var) survives crashes, works
    across processes and agents, and is trivial to inspect with `ls`.
    """

    def __init__(self, path: Path):
        self.path = path

    def engaged(self) -> tuple[bool, str | None]:
        if not self.path.exists():
            return False, None
        try:
            reason = self.path.read_text(encoding="utf-8").strip()
        except OSError as exc:  # unreadable switch still means engaged
            return True, f"unreadable kill file: {exc}"
        return True, reason or "no reason recorded"

    def engage(self, reason: str) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
        self.path.write_text(f"{stamp} {reason}\n", encoding="utf-8")

    def release(self) -> bool:
        if self.path.exists():
            self.path.unlink()
            return True
        return False


@dataclass(frozen=True)
class LiveCheck:
    ok: bool
    reason: str


def check_live_permission(mode: Mode, ack: str, confirm_live: bool) -> LiveCheck:
    if mode is Mode.PAPER:
        return LiveCheck(True, "paper mode")
    if mode is Mode.TESTNET:
        return LiveCheck(True, "testnet mode")
    if ack != LIVE_ACK_PHRASE:
        return LiveCheck(False, f"CFOS_LIVE_ACK must equal {LIVE_ACK_PHRASE!r}")
    if not confirm_live:
        return LiveCheck(False, "live order requires confirm_live=True (--confirm-live)")
    return LiveCheck(True, "live mode acknowledged")


def preflight_market(
    snapshot: MarketSnapshot,
    universe: UniverseConfig,
) -> tuple[bool, str]:
    if universe.allow and snapshot.symbol not in universe.allow:
        return False, f"{snapshot.symbol} is not in the allowlist"
    if snapshot.quote_volume_24h is not None:
        if snapshot.quote_volume_24h < universe.min_quote_volume_usd:
            return (
                False,
                f"24h quote volume {snapshot.quote_volume_24h:,.0f} USD below "
                f"{universe.min_quote_volume_usd:,.0f} USD",
            )
    if snapshot.price <= 0:
        return False, "non-positive price"
    return True, "ok"
