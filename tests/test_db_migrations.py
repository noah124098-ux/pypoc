"""Tests for SQLite schema migration logic in core/persistence/store.py."""
from __future__ import annotations

import sqlite3
import pytest

from core.persistence.store import (
    SCHEMA_VERSION,
    Store,
    _get_schema_version,
    _run_migrations,
    _set_schema_version,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _open(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path, isolation_level=None)
    conn.row_factory = sqlite3.Row
    return conn


# ---------------------------------------------------------------------------
# Unit tests for helper functions (no Store)
# ---------------------------------------------------------------------------

def test_get_schema_version_returns_zero_on_missing_table(tmp_path):
    db = str(tmp_path / "v.db")
    conn = _open(db)
    assert _get_schema_version(conn) == 0
    conn.close()


def test_set_and_get_schema_version_roundtrip(tmp_path):
    db = str(tmp_path / "v.db")
    conn = _open(db)
    _set_schema_version(conn, 7)
    assert _get_schema_version(conn) == 7
    conn.close()


def test_set_schema_version_is_idempotent(tmp_path):
    db = str(tmp_path / "v.db")
    conn = _open(db)
    _set_schema_version(conn, 2)
    _set_schema_version(conn, 2)
    assert _get_schema_version(conn) == 2
    conn.close()


def test_set_schema_version_overwrites_previous(tmp_path):
    db = str(tmp_path / "v.db")
    conn = _open(db)
    _set_schema_version(conn, 1)
    _set_schema_version(conn, 3)
    assert _get_schema_version(conn) == 3
    conn.close()


# ---------------------------------------------------------------------------
# Migration path: v0 → v3 on a fresh DB (Store constructor)
# ---------------------------------------------------------------------------

def test_fresh_db_reaches_current_schema_version(tmp_path):
    db = str(tmp_path / "agent.db")
    Store(db)
    conn = _open(db)
    assert _get_schema_version(conn) == SCHEMA_VERSION
    conn.close()


def test_fresh_db_has_strategy_column(tmp_path):
    db = str(tmp_path / "agent.db")
    Store(db)
    conn = _open(db)
    cols = {row[1] for row in conn.execute("PRAGMA table_info(trades)")}
    assert "strategy" in cols
    conn.close()


def test_fresh_db_has_exit_reason_column(tmp_path):
    db = str(tmp_path / "agent.db")
    Store(db)
    conn = _open(db)
    cols = {row[1] for row in conn.execute("PRAGMA table_info(trades)")}
    assert "exit_reason" in cols
    conn.close()


# ---------------------------------------------------------------------------
# Idempotency: running migrations twice is safe
# ---------------------------------------------------------------------------

def test_migrations_idempotent(tmp_path):
    db = str(tmp_path / "agent.db")
    Store(db)  # first run
    Store(db)  # second run — must not raise
    conn = _open(db)
    assert _get_schema_version(conn) == SCHEMA_VERSION
    conn.close()


def test_run_migrations_directly_twice_is_safe(tmp_path):
    db = str(tmp_path / "agent.db")
    Store(db)
    conn = _open(db)
    _run_migrations(conn)  # call a third time — must not raise
    assert _get_schema_version(conn) == SCHEMA_VERSION
    conn.close()


# ---------------------------------------------------------------------------
# Upgrade path: simulate an old DB that is missing the new columns
# ---------------------------------------------------------------------------

def _create_legacy_db_v1(db_path: str) -> None:
    """Simulate a v1 database: trades table without strategy or exit_reason columns."""
    conn = sqlite3.connect(db_path, isolation_level=None)
    conn.execute(
        """
        CREATE TABLE trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT NOT NULL,
            side TEXT NOT NULL,
            qty INTEGER NOT NULL,
            entry_price REAL NOT NULL,
            exit_price REAL NOT NULL,
            pnl REAL NOT NULL,
            charges REAL NOT NULL,
            opened_at TEXT NOT NULL,
            closed_at TEXT NOT NULL
        )
        """
    )
    # Mark as version 1 so migration starts from there
    conn.execute("CREATE TABLE _schema_version (version INTEGER)")
    conn.execute("INSERT INTO _schema_version VALUES (1)")
    conn.close()


def test_upgrade_from_v1_adds_strategy_column(tmp_path):
    db = str(tmp_path / "legacy.db")
    _create_legacy_db_v1(db)

    # Store.__init__ should apply migrations v2 and v3
    Store(db)

    conn = _open(db)
    cols = {row[1] for row in conn.execute("PRAGMA table_info(trades)")}
    assert "strategy" in cols
    conn.close()


def test_upgrade_from_v1_adds_exit_reason_column(tmp_path):
    db = str(tmp_path / "legacy.db")
    _create_legacy_db_v1(db)

    Store(db)

    conn = _open(db)
    cols = {row[1] for row in conn.execute("PRAGMA table_info(trades)")}
    assert "exit_reason" in cols
    conn.close()


def test_upgrade_from_v1_reaches_current_version(tmp_path):
    db = str(tmp_path / "legacy.db")
    _create_legacy_db_v1(db)

    Store(db)

    conn = _open(db)
    assert _get_schema_version(conn) == SCHEMA_VERSION
    conn.close()


def test_upgrade_from_v1_preserves_existing_rows(tmp_path):
    db = str(tmp_path / "legacy.db")
    _create_legacy_db_v1(db)

    # Insert a row in the old schema
    conn = sqlite3.connect(db, isolation_level=None)
    conn.execute(
        "INSERT INTO trades (symbol, side, qty, entry_price, exit_price, pnl, charges, opened_at, closed_at) "
        "VALUES ('INFY', 'BUY', 10, 1500.0, 1550.0, 500.0, 20.0, '2024-01-01', '2024-01-02')"
    )
    conn.close()

    Store(db)

    conn = _open(db)
    rows = list(conn.execute("SELECT * FROM trades"))
    assert len(rows) == 1
    assert rows[0]["symbol"] == "INFY"
    conn.close()


# ---------------------------------------------------------------------------
# SCHEMA_VERSION constant sanity check
# ---------------------------------------------------------------------------

def test_schema_version_constant_value():
    assert SCHEMA_VERSION == 3
