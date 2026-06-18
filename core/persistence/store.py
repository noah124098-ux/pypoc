"""SQLite persistence — trades, signals, equity snapshots, regime calls.

Schema is intentionally simple (one row per event). Used by dashboard and EOD reviewer.
"""
from __future__ import annotations

import json
import logging
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator, Optional

log = logging.getLogger(__name__)

SCHEMA_VERSION = 5  # increment when schema changes

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

-- Per-trading-day circuit baselines. One row per IST trading date. Lets the agent
-- restore starting_equity_today / peak_equity after a mid-day restart so the
-- daily-loss and drawdown circuits keep the correct baseline (else a restart
-- silently re-baselines and the circuits stop protecting the day's true P&L).
CREATE TABLE IF NOT EXISTS daily_state (
    trade_date TEXT PRIMARY KEY,
    starting_equity_today REAL NOT NULL,
    peak_equity REAL NOT NULL,
    updated_at TEXT NOT NULL
);

-- Live broker state (open positions + cash + realized P&L) for crash-safe restart.
-- Single row (id=1), stored as JSON, tagged with the IST trade date so the orchestrator
-- only restores positions from the SAME trading day (prior-day positions are stale —
-- a real broker squares off intraday at 3:20pm).
CREATE TABLE IF NOT EXISTS broker_state (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    trade_date TEXT NOT NULL,
    state_json TEXT NOT NULL,
    updated_at TEXT NOT NULL
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
    if current < 4:
        # v4: daily_state table for circuit-baseline persistence (created by SCHEMA
        # above on fresh DBs; this ensures existing DBs get it too).
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS daily_state (
                trade_date TEXT PRIMARY KEY,
                starting_equity_today REAL NOT NULL,
                peak_equity REAL NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        _set_schema_version(conn, 4)
    if current < 5:
        # v5: broker_state table for crash-safe open-position persistence.
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS broker_state (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                trade_date TEXT NOT NULL,
                state_json TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        _set_schema_version(conn, 5)


class Store:
    def __init__(self, db_path: str):
        self.db_path = db_path
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as c:
            # Performance PRAGMAs — set before schema creation so WAL applies from the start.
            c.execute("PRAGMA journal_mode=WAL")
            c.execute("PRAGMA synchronous=NORMAL")
            c.execute("PRAGMA cache_size=-64000")   # 64 MB cache
            c.executescript(SCHEMA)
            _run_migrations(c)
            # Integrity check — warn if the database is corrupt (never raises).
            try:
                result = c.execute("PRAGMA integrity_check").fetchone()[0]
                if result != "ok":
                    log.warning("Database integrity check: %s", result)
            except Exception:
                pass

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

    def save_daily_state(
        self, *, trade_date: str, starting_equity_today: float, peak_equity: float
    ) -> None:
        """Upsert the circuit baselines for a trading day (keyed by IST date string)."""
        with self.connect() as c:
            c.execute(
                """
                INSERT INTO daily_state (trade_date, starting_equity_today, peak_equity, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(trade_date) DO UPDATE SET
                    starting_equity_today = excluded.starting_equity_today,
                    peak_equity = excluded.peak_equity,
                    updated_at = excluded.updated_at
                """,
                (trade_date, starting_equity_today, peak_equity, datetime.utcnow().isoformat()),
            )

    def load_daily_state(self, trade_date: str) -> Optional[dict]:
        """Return {starting_equity_today, peak_equity} for trade_date, or None if absent."""
        with self.connect() as c:
            row = c.execute(
                "SELECT starting_equity_today, peak_equity FROM daily_state WHERE trade_date = ?",
                (trade_date,),
            ).fetchone()
        if row is None:
            return None
        return {
            "starting_equity_today": row["starting_equity_today"],
            "peak_equity": row["peak_equity"],
        }

    def save_broker_state(self, *, trade_date: str, state: dict) -> None:
        """Persist the live broker's open positions + cash (single row, id=1)."""
        with self.connect() as c:
            c.execute(
                """
                INSERT INTO broker_state (id, trade_date, state_json, updated_at)
                VALUES (1, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    trade_date = excluded.trade_date,
                    state_json = excluded.state_json,
                    updated_at = excluded.updated_at
                """,
                (trade_date, json.dumps(state), datetime.utcnow().isoformat()),
            )

    def load_broker_state(self, trade_date: str) -> Optional[dict]:
        """Return the saved broker state ONLY if it was written for `trade_date`
        (same-day restart). Prior-day state is ignored — those positions are stale."""
        with self.connect() as c:
            row = c.execute(
                "SELECT trade_date, state_json FROM broker_state WHERE id = 1"
            ).fetchone()
        if row is None or row["trade_date"] != trade_date:
            return None
        try:
            return json.loads(row["state_json"])
        except (ValueError, TypeError):
            return None

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
