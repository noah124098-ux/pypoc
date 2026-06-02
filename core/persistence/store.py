"""SQLite persistence — trades, signals, equity snapshots, regime calls.

Schema is intentionally simple (one row per event). Used by dashboard and EOD reviewer.
"""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator, Optional

SCHEMA_VERSION = 3  # increment when schema changes

SCHEMA = """
CREATE TABLE IF NOT EXISTS trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL,
    side TEXT NOT NULL,
    qty INTEGER NOT NULL,
    entry_price REAL NOT NULL,
    exit_price REAL NOT NULL,
    pnl REAL NOT NULL,
    charges REAL NOT NULL,
    strategy TEXT,
    exit_reason TEXT,
    opened_at TEXT NOT NULL,
    closed_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS signals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    symbol TEXT NOT NULL,
    side TEXT NOT NULL,
    strategy TEXT NOT NULL,
    regime TEXT NOT NULL,
    entry_price REAL NOT NULL,
    stop_loss REAL NOT NULL,
    target REAL NOT NULL,
    confidence REAL,
    rationale TEXT,
    accepted INTEGER NOT NULL,
    rejection_reason TEXT
);

CREATE TABLE IF NOT EXISTS equity_snapshots (
    ts TEXT PRIMARY KEY,
    cash REAL NOT NULL,
    equity REAL NOT NULL,
    realized_pnl REAL NOT NULL,
    open_positions INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS regime_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    regime TEXT NOT NULL,
    nifty_change_pct REAL,
    vix REAL,
    adx REAL,
    bb_width REAL
);

CREATE TABLE IF NOT EXISTS guardrail_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    rule TEXT NOT NULL,
    symbol TEXT,
    detail TEXT
);

CREATE INDEX IF NOT EXISTS idx_trades_closed_at ON trades(closed_at);
CREATE INDEX IF NOT EXISTS idx_signals_ts ON signals(ts);
"""


def _get_schema_version(conn: sqlite3.Connection) -> int:
    try:
        row = conn.execute("SELECT version FROM _schema_version").fetchone()
        return row[0] if row else 0
    except sqlite3.OperationalError:
        return 0


def _set_schema_version(conn: sqlite3.Connection, version: int) -> None:
    conn.execute("CREATE TABLE IF NOT EXISTS _schema_version (version INTEGER)")
    conn.execute("DELETE FROM _schema_version")
    conn.execute("INSERT INTO _schema_version VALUES (?)", (version,))


def _run_migrations(conn: sqlite3.Connection) -> None:
    current = _get_schema_version(conn)
    if current < 1:
        # v1: base schema (already exists if DB is fresh, migration is a no-op)
        _set_schema_version(conn, 1)
    if current < 2:
        # v2: add strategy column to trades if missing
        try:
            conn.execute("ALTER TABLE trades ADD COLUMN strategy TEXT DEFAULT ''")
        except sqlite3.OperationalError:
            pass  # column already exists
        _set_schema_version(conn, 2)
    if current < 3:
        # v3: add exit_reason column to trades if missing
        try:
            conn.execute("ALTER TABLE trades ADD COLUMN exit_reason TEXT DEFAULT ''")
        except sqlite3.OperationalError:
            pass  # column already exists
        _set_schema_version(conn, 3)


class Store:
    def __init__(self, db_path: str):
        self.db_path = db_path
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as c:
            c.executescript(SCHEMA)
            _run_migrations(c)

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.db_path, isolation_level=None)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()

    # ----- writes -----

    def record_trade(self, **fields: Any) -> None:
        with self.connect() as c:
            c.execute(
                """
                INSERT INTO trades (symbol, side, qty, entry_price, exit_price,
                                    pnl, charges, strategy, exit_reason, opened_at, closed_at)
                VALUES (:symbol, :side, :qty, :entry_price, :exit_price,
                        :pnl, :charges, :strategy, :exit_reason, :opened_at, :closed_at)
                """,
                fields,
            )

    def record_signal(self, *, accepted: bool, rejection_reason: Optional[str] = None, **fields: Any) -> None:
        fields = {**fields, "accepted": 1 if accepted else 0, "rejection_reason": rejection_reason}
        with self.connect() as c:
            c.execute(
                """
                INSERT INTO signals (ts, symbol, side, strategy, regime, entry_price,
                                     stop_loss, target, confidence, rationale, accepted, rejection_reason)
                VALUES (:ts, :symbol, :side, :strategy, :regime, :entry_price,
                        :stop_loss, :target, :confidence, :rationale, :accepted, :rejection_reason)
                """,
                fields,
            )

    def record_equity(self, *, cash: float, equity: float, realized_pnl: float, open_positions: int) -> None:
        with self.connect() as c:
            c.execute(
                """
                INSERT OR REPLACE INTO equity_snapshots (ts, cash, equity, realized_pnl, open_positions)
                VALUES (?, ?, ?, ?, ?)
                """,
                (datetime.utcnow().isoformat(), cash, equity, realized_pnl, open_positions),
            )

    def record_regime(self, **fields: Any) -> None:
        with self.connect() as c:
            c.execute(
                """
                INSERT INTO regime_log (ts, regime, nifty_change_pct, vix, adx, bb_width)
                VALUES (:ts, :regime, :nifty_change_pct, :vix, :adx, :bb_width)
                """,
                fields,
            )

    def record_guardrail(self, *, rule: str, symbol: Optional[str], detail: str) -> None:
        with self.connect() as c:
            c.execute(
                "INSERT INTO guardrail_events (ts, rule, symbol, detail) VALUES (?, ?, ?, ?)",
                (datetime.utcnow().isoformat(), rule, symbol, detail),
            )

    # ----- reads -----

    def trades_today(self) -> list[sqlite3.Row]:
        today = datetime.utcnow().date().isoformat()
        with self.connect() as c:
            cur = c.execute(
                "SELECT * FROM trades WHERE closed_at >= ? ORDER BY closed_at DESC",
                (today,),
            )
            return list(cur.fetchall())

    def equity_curve(self, limit: int = 1000) -> list[sqlite3.Row]:
        with self.connect() as c:
            cur = c.execute(
                "SELECT * FROM equity_snapshots ORDER BY ts DESC LIMIT ?", (limit,)
            )
            return list(cur.fetchall())
