"""Environment and safety diagnostics. ``cfos doctor`` is the first command."""

from __future__ import annotations

import importlib.util
import sqlite3
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import Settings
from .guards import KillSwitch, LIVE_ACK_PHRASE
from .journal import Journal
from .models import Mode


def _check(name: str, status: str, detail: str) -> dict[str, str]:
    return {"check": name, "status": status, "detail": detail}


def run(settings: Settings) -> list[dict[str, str]]:
    checks: list[dict[str, str]] = []

    version = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    checks.append(
        _check(
            "python",
            "OK" if sys.version_info >= (3, 11) else "FAIL",
            f"{version} (>=3.11 required)",
        )
    )

    spec = importlib.util.find_spec("ccxt")
    if spec is None:
        checks.append(
            _check(
                "ccxt",
                "WARN" if settings.mode is Mode.PAPER else "FAIL",
                "not installed; paper mode works, testnet/live do not",
            )
        )
    else:
        import ccxt

        checks.append(_check("ccxt", "OK", getattr(ccxt, "__version__", "unknown")))

    checks.append(
        _check(
            "mode",
            "OK",
            f"{settings.mode.value} / venue {settings.venue.name} / "
            f"sandbox={settings.venue.sandbox}",
        )
    )

    if settings.mode is Mode.LIVE:
        if settings.live_ack == LIVE_ACK_PHRASE:
            checks.append(_check("live_ack", "OK", "acknowledged"))
        else:
            checks.append(
                _check(
                    "live_ack",
                    "FAIL",
                    f"CFOS_LIVE_ACK must be {LIVE_ACK_PHRASE} for live mode",
                )
            )
    else:
        checks.append(_check("live_ack", "OK", "not required in paper/testnet"))

    if settings.mode in (Mode.TESTNET, Mode.LIVE):
        if settings.api_key and settings.api_secret:
            checks.append(_check("credentials", "OK", "API key and secret present"))
        else:
            checks.append(
                _check("credentials", "FAIL", "CFOS_API_KEY / CFOS_API_SECRET missing")
            )
    else:
        checks.append(_check("credentials", "OK", "paper mode, no keys needed"))

    for name in ("risk.toml", "universe.toml"):
        path = settings.home / "config" / name
        checks.append(
            _check(f"config:{name}", "OK" if path.is_file() else "FAIL", str(path))
        )

    engaged, reason = KillSwitch(settings.kill_file).engaged()
    checks.append(
        _check(
            "kill_switch",
            "WARN" if engaged else "OK",
            f"ENGAGED: {reason}" if engaged else "released",
        )
    )

    try:
        probe = settings.data_dir / ".write_probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        checks.append(_check("data_dir", "OK", str(settings.data_dir)))
    except OSError as exc:
        checks.append(_check("data_dir", "FAIL", f"not writable: {exc}"))

    try:
        journal = Journal(settings.db_path)
        journal.close()
        checks.append(
            _check(
                "journal",
                "OK",
                f"{settings.db_path} (sqlite {sqlite3.sqlite_version})",
            )
        )
    except Exception as exc:  # noqa: BLE001
        checks.append(_check("journal", "FAIL", str(exc)))

    checks.append(
        _check(
            "clock",
            "OK",
            datetime.now(timezone.utc).isoformat(timespec="seconds"),
        )
    )

    if settings.mode is Mode.PAPER:
        state = settings.data_dir / "paper_state.json"
        checks.append(
            _check(
                "paper_state",
                "OK",
                f"{state} ({'exists' if state.is_file() else 'fresh account'})",
            )
        )

    return checks


def worst(checks: list[dict[str, str]]) -> str:
    if any(c["status"] == "FAIL" for c in checks):
        return "FAIL"
    if any(c["status"] == "WARN" for c in checks):
        return "WARN"
    return "OK"
