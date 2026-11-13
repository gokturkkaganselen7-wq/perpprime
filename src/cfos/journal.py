"""SQLite journal: decisions, orders, positions, equity, review.

The journal is the only durable source of truth for "what did the agent do
and why". It is deliberately boring: one file, no ORM, no migrations.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

from .models import dumps

SCHEMA = """
CREATE TABLE IF NOT EXISTS decisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    mode TEXT NOT NULL,
    intent TEXT,
    symbol TEXT,
    side TEXT,
    verdict TEXT NOT NULL,
    reasons TEXT,
    plan TEXT
);
CREATE TABLE IF NOT EXISTS orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    client_id TEXT UNIQUE,
    venue TEXT,
    symbol TEXT,
    type TEXT,
    side TEXT,
    qty REAL,
    price REAL,
    status TEXT,
    raw TEXT
);
CREATE TABLE IF NOT EXISTS positions (
    id TEXT PRIMARY KEY,
    opened_at TEXT NOT NULL,
    closed_at TEXT,
    symbol TEXT NOT NULL,
    side TEXT NOT NULL,
    qty REAL NOT NULL,
    entry REAL NOT NULL,
    exit REAL,
    stop REAL,
    tp REAL,
    leverage REAL,
    risk_amount REAL,
    pnl REAL,
    fees REAL DEFAULT 0,
    status TEXT NOT NULL,
    plan TEXT
);
CREATE TABLE IF NOT EXISTS equity (
    ts TEXT PRIMARY KEY,
    equity REAL NOT NULL,
    realized REAL,
    unrealized REAL
);
CREATE INDEX IF NOT EXISTS idx_positions_closed ON positions(closed_at);
CREATE INDEX IF NOT EXISTS idx_decisions_ts ON decisions(ts);
"""


def _midnight_utc(days_ago: int = 0) -> str:
    now = datetime.now(timezone.utc)
    start = datetime(now.year, now.month, now.day, tzinfo=timezone.utc)
    return (start - timedelta(days=days_ago)).isoformat(timespec="seconds")


class Journal:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.path, timeout=10)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(SCHEMA)
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    # -- writes ---------------------------------------------------------
    def log_decision(
        self,
        *,
        mode: str,
        verdict: str,
        intent: str = "",
        symbol: str = "",
        side: str = "",
        reasons: Iterable[str] = (),
        plan: dict[str, Any] | None = None,
        ts: str | None = None,
    ) -> int:
        cur = self._conn.execute(
            "INSERT INTO decisions(ts, mode, intent, symbol, side, verdict, reasons, plan)"
            " VALUES (?,?,?,?,?,?,?,?)",
            (
                ts or datetime.now(timezone.utc).isoformat(timespec="seconds"),
                mode,
                intent,
                symbol,
                side,
                verdict,
                dumps(list(reasons), indent=None),
                dumps(plan, indent=None) if plan else None,
            ),
        )
        self._conn.commit()
        return int(cur.lastrowid)

    def log_order(
        self,
        *,
        client_id: str,
        venue: str,
        symbol: str,
        type: str,
        side: str,
        qty: float,
        price: float | None,
        status: str,
        raw: dict[str, Any] | None = None,
        ts: str | None = None,
    ) -> None:
        self._conn.execute(
            "INSERT INTO orders(ts, client_id, venue, symbol, type, side, qty, price, status, raw)"
            " VALUES (?,?,?,?,?,?,?,?,?,?)"
            " ON CONFLICT(client_id) DO UPDATE SET status=excluded.status, raw=excluded.raw",
            (
                ts or datetime.now(timezone.utc).isoformat(timespec="seconds"),
                client_id,
                venue,
                symbol,
                type,
                side,
                qty,
                price,
                status,
                dumps(raw, indent=None) if raw else None,
            ),
        )
        self._conn.commit()

    def open_position(
        self,
        *,
        position_id: str,
        symbol: str,
        side: str,
        qty: float,
        entry: float,
        stop: float | None,
        tp: float | None,
        leverage: float,
        risk_amount: float,
        plan: dict[str, Any] | None = None,
    ) -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO positions(id, opened_at, symbol, side, qty, entry, stop, tp,"
            " leverage, risk_amount, status, plan) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                position_id,
                datetime.now(timezone.utc).isoformat(timespec="seconds"),
                symbol,
                side,
                qty,
                entry,
                stop,
                tp,
                leverage,
                risk_amount,
                "open",
                dumps(plan, indent=None) if plan else None,
            ),
        )
        self._conn.commit()

    def close_position(
        self,
        position_id: str,
        *,
        exit_price: float,
        pnl: float,
        fees: float = 0.0,
        qty: float | None = None,
    ) -> None:
        """Close a position, or realize part of it.

        ``qty=None`` keeps the legacy full-close behaviour. When ``qty`` is
        provided, the row stays open until the remaining size reaches zero and
        PnL/fees accumulate across partial take-profits.
        """
        if qty is None:
            self._conn.execute(
                "UPDATE positions SET closed_at=?, exit=?, pnl=?, fees=?, status='closed'"
                " WHERE id=? AND status='open'",
                (
                    datetime.now(timezone.utc).isoformat(timespec="seconds"),
                    exit_price,
                    pnl,
                    fees,
                    position_id,
                ),
            )
            self._conn.commit()
            return

        row = self._conn.execute(
            "SELECT qty FROM positions WHERE id=? AND status='open'", (position_id,)
        ).fetchone()
        if row is None:
            return
        remaining = float(row["qty"]) - float(qty)
        if remaining <= 1e-12:
            self._conn.execute(
                "UPDATE positions SET qty=0, closed_at=?, exit=?,"
                " pnl=COALESCE(pnl,0)+?, fees=COALESCE(fees,0)+?, status='closed'"
                " WHERE id=? AND status='open'",
                (
                    datetime.now(timezone.utc).isoformat(timespec="seconds"),
                    exit_price,
                    pnl,
                    fees,
                    position_id,
                ),
            )
        else:
            self._conn.execute(
                "UPDATE positions SET qty=?, exit=?, pnl=COALESCE(pnl,0)+?,"
                " fees=COALESCE(fees,0)+? WHERE id=? AND status='open'",
                (remaining, exit_price, pnl, fees, position_id),
            )
        self._conn.commit()

    def snapshot_equity(
        self, equity: float, realized: float = 0.0, unrealized: float = 0.0
    ) -> None:
        # Microsecond precision keeps multiple snapshots in the same second
        # distinct; `ts` is the primary key.
        self._conn.execute(
            "INSERT OR REPLACE INTO equity(ts, equity, realized, unrealized) VALUES (?,?,?,?)",
            (
                datetime.now(timezone.utc).isoformat(),
                equity,
                realized,
                unrealized,
            ),
        )
        self._conn.commit()

    # -- reads ----------------------------------------------------------
    def open_positions(self) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT * FROM positions WHERE status='open' ORDER BY opened_at"
        ).fetchall()
        return [dict(r) for r in rows]

    def closed_positions(self, limit: int = 200) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT * FROM positions WHERE status='closed' ORDER BY closed_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]

    def recent_decisions(self, limit: int = 20) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT * FROM decisions ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]

    def trades_today(self) -> int:
        row = self._conn.execute(
            "SELECT COUNT(*) AS n FROM positions WHERE opened_at >= ?", (_midnight_utc(),)
        ).fetchone()
        return int(row["n"])

    def realized_today(self) -> float:
        row = self._conn.execute(
            "SELECT COALESCE(SUM(pnl), 0) AS p FROM positions"
            " WHERE status='closed' AND closed_at >= ?",
            (_midnight_utc(),),
        ).fetchone()
        return float(row["p"])

    def realized_since(self, iso: str) -> float:
        row = self._conn.execute(
            "SELECT COALESCE(SUM(pnl), 0) AS p FROM positions"
            " WHERE status='closed' AND closed_at >= ?",
            (iso,),
        ).fetchone()
        return float(row["p"])

    def consecutive_losses(self) -> tuple[int, str | None]:
        rows = self._conn.execute(
            "SELECT pnl, closed_at FROM positions WHERE status='closed'"
            " ORDER BY closed_at DESC LIMIT 20"
        ).fetchall()
        count = 0
        last_ts = None
        for row in rows:
            pnl = row["pnl"] or 0.0
            if pnl < 0:
                count += 1
                if last_ts is None:
                    last_ts = row["closed_at"]
            else:
                break
        return count, last_ts

    def peak_equity(self) -> float | None:
        row = self._conn.execute("SELECT MAX(equity) AS e FROM equity").fetchone()
        return float(row["e"]) if row and row["e"] is not None else None

    def equity_at_or_before(self, iso: str) -> float | None:
        row = self._conn.execute(
            "SELECT equity FROM equity WHERE ts <= ? ORDER BY ts DESC LIMIT 1", (iso,)
        ).fetchone()
        return float(row["equity"]) if row else None

    def equity_curve(self, limit: int = 500) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT * FROM equity ORDER BY ts DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in reversed(rows)]

    def summary(self) -> dict[str, Any]:
        rows = self.closed_positions(limit=10_000)
        pnls = [float(r["pnl"] or 0.0) for r in rows]
        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p < 0]
        gross_win = sum(wins)
        gross_loss = abs(sum(losses))
        fees = sum(float(r["fees"] or 0.0) for r in rows)
        r_values = [
            float(r["pnl"]) / float(r["risk_amount"])
            for r in rows
            if r.get("risk_amount") and float(r["risk_amount"]) > 0 and r.get("pnl") is not None
        ]
        return {
            "trades": len(rows),
            "wins": len(wins),
            "losses": len(losses),
            "win_rate": round(len(wins) / len(rows) * 100, 2) if rows else 0.0,
            "total_pnl": round(sum(pnls), 4),
            "avg_win": round(gross_win / len(wins), 4) if wins else 0.0,
            "avg_loss": round(-gross_loss / len(losses), 4) if losses else 0.0,
            "expectancy": round(sum(pnls) / len(rows), 4) if rows else 0.0,
            "profit_factor": round(gross_win / gross_loss, 3) if gross_loss else None,
            "avg_r": round(sum(r_values) / len(r_values), 3) if r_values else 0.0,
            "fees": round(fees, 4),
        }
