"""Tests for core/analytics/trade_replay.py — trade replay engine."""
from __future__ import annotations

import json
import sqlite3
import tempfile
from pathlib import Path

import pytest

from core.analytics.trade_replay import (
    TradeReplay,
    TradeReplayFrame,
    list_recent_trades,
    load_trade_replay,
)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _make_db(tmp_path: Path, trades=None, signals=None) -> str:
    """Create a minimal SQLite DB with trades and signals tables."""
    db_path = str(tmp_path / "test_agent.db")
    conn = sqlite3.connect(db_path)
    conn.execute("""
        CREATE TABLE trades (
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
        )
    """)
    conn.execute("""
        CREATE TABLE signals (
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
        )
    """)

    if trades:
        for t in trades:
            conn.execute(
                "INSERT INTO trades (symbol, side, qty, entry_price, exit_price, pnl, charges, strategy, exit_reason, opened_at, closed_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (
                    t["symbol"], t.get("side", "BUY"), t.get("qty", 1),
                    t["entry_price"], t["exit_price"], t["pnl"], t.get("charges", 0.0),
                    t.get("strategy", "trend_breakout"), t.get("exit_reason", "stop_loss"),
                    t["opened_at"], t["closed_at"],
                ),
            )

    if signals:
        for s in signals:
            conn.execute(
                "INSERT INTO signals (ts, symbol, side, strategy, regime, entry_price, stop_loss, target, confidence, rationale, accepted) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (
                    s["ts"], s["symbol"], s.get("side", "BUY"), s.get("strategy", "trend_breakout"),
                    s.get("regime", "TREND"), s["entry_price"], s.get("stop_loss", 0.0),
                    s.get("target", 0.0), s.get("confidence", 0.85),
                    s.get("rationale", "Strong breakout above resistance"),
                    s.get("accepted", 1),
                ),
            )

    conn.commit()
    conn.close()
    return db_path


def _make_events(tmp_path: Path, events: list) -> str:
    """Write a JSONL events file and return its path."""
    events_path = tmp_path / "events.jsonl"
    with open(events_path, "w", encoding="utf-8") as f:
        for ev in events:
            f.write(json.dumps(ev) + "\n")
    return str(events_path)


# ---------------------------------------------------------------------------
# list_recent_trades
# ---------------------------------------------------------------------------

def test_list_recent_trades_returns_empty_for_missing_db(tmp_path):
    result = list_recent_trades(str(tmp_path / "nonexistent.db"))
    assert result == []


def test_list_recent_trades_returns_empty_for_empty_table(tmp_path):
    db_path = _make_db(tmp_path)
    result = list_recent_trades(db_path)
    assert result == []


def test_list_recent_trades_returns_list_of_dicts(tmp_path):
    db_path = _make_db(tmp_path, trades=[
        {"symbol": "RELIANCE", "entry_price": 2800.0, "exit_price": 2850.0,
         "pnl": 50.0, "opened_at": "2026-01-02T09:30:00", "closed_at": "2026-01-02T15:00:00"},
        {"symbol": "TCS", "entry_price": 3500.0, "exit_price": 3480.0,
         "pnl": -20.0, "opened_at": "2026-01-03T10:00:00", "closed_at": "2026-01-03T15:00:00"},
    ])
    result = list_recent_trades(db_path)
    assert isinstance(result, list)
    assert len(result) == 2
    # Most recent first (ORDER BY id DESC)
    assert result[0]["symbol"] == "TCS"
    assert result[1]["symbol"] == "RELIANCE"


def test_list_recent_trades_respects_limit(tmp_path):
    trades = [
        {"symbol": f"SYM{i}", "entry_price": 100.0, "exit_price": 105.0,
         "pnl": 5.0, "opened_at": f"2026-01-{i+1:02d}T09:30:00",
         "closed_at": f"2026-01-{i+1:02d}T15:00:00"}
        for i in range(10)
    ]
    db_path = _make_db(tmp_path, trades=trades)
    result = list_recent_trades(db_path, limit=3)
    assert len(result) == 3


# ---------------------------------------------------------------------------
# load_trade_replay — error handling
# ---------------------------------------------------------------------------

def test_load_trade_replay_returns_none_for_invalid_id(tmp_path):
    db_path = _make_db(tmp_path)
    result = load_trade_replay(999, db_path)
    assert result is None


def test_load_trade_replay_returns_none_for_missing_db(tmp_path):
    result = load_trade_replay(1, str(tmp_path / "nonexistent.db"))
    assert result is None


# ---------------------------------------------------------------------------
# load_trade_replay — success path
# ---------------------------------------------------------------------------

def test_load_trade_replay_returns_trade_replay_object(tmp_path):
    db_path = _make_db(
        tmp_path,
        trades=[
            {"symbol": "INFY", "entry_price": 1400.0, "exit_price": 1450.0,
             "pnl": 50.0, "charges": 12.5, "strategy": "rsi_momentum",
             "exit_reason": "target", "qty": 1,
             "opened_at": "2026-01-05T09:30:00", "closed_at": "2026-01-05T14:00:00"},
        ],
        signals=[
            {"ts": "2026-01-05T09:28:00", "symbol": "INFY", "entry_price": 1400.0,
             "stop_loss": 1370.0, "target": 1450.0, "confidence": 0.9,
             "rationale": "RSI oversold bounce", "regime": "RANGE", "accepted": 1},
        ],
    )

    replay = load_trade_replay(1, db_path)
    assert replay is not None
    assert isinstance(replay, TradeReplay)


def test_load_trade_replay_fields_populated_correctly(tmp_path):
    db_path = _make_db(
        tmp_path,
        trades=[
            {"symbol": "HDFC", "entry_price": 1600.0, "exit_price": 1560.0,
             "pnl": -40.0, "charges": 15.0, "strategy": "mean_reversion",
             "exit_reason": "stop_loss", "qty": 2,
             "opened_at": "2026-02-10T09:45:00", "closed_at": "2026-02-10T11:30:00"},
        ],
        signals=[
            {"ts": "2026-02-10T09:43:00", "symbol": "HDFC", "entry_price": 1600.0,
             "stop_loss": 1565.0, "target": 1650.0, "confidence": 0.75,
             "rationale": "Bounce at support", "regime": "RANGE", "accepted": 1},
        ],
    )

    replay = load_trade_replay(1, db_path)
    assert replay is not None
    assert replay.trade_id == 1
    assert replay.symbol == "HDFC"
    assert replay.strategy == "mean_reversion"
    assert replay.entry_price == pytest.approx(1600.0)
    assert replay.exit_price == pytest.approx(1560.0)
    assert replay.stop_loss == pytest.approx(1565.0)
    assert replay.target == pytest.approx(1650.0)
    assert replay.pnl == pytest.approx(-40.0)
    assert replay.charges == pytest.approx(15.0)
    assert replay.entry_time == "2026-02-10T09:45:00"
    assert replay.exit_time == "2026-02-10T11:30:00"
    assert replay.exit_reason == "stop_loss"
    assert replay.regime_at_entry == "RANGE"
    assert replay.signal_rationale == "Bounce at support"
    assert isinstance(replay.frames, list)
    assert isinstance(replay.rejection_checks, list)


def test_load_trade_replay_parses_events_log(tmp_path):
    """Events log entries matching the symbol appear as frames."""
    db_path = _make_db(
        tmp_path,
        trades=[
            {"symbol": "WIPRO", "entry_price": 500.0, "exit_price": 510.0,
             "pnl": 10.0, "charges": 5.0, "qty": 1,
             "opened_at": "2026-03-01T09:30:00", "closed_at": "2026-03-01T12:00:00"},
        ],
    )
    events = [
        {"ts": "2026-03-01T09:30:00", "symbol": "WIPRO", "type": "entry", "price": 500.0,
         "regime": "TREND", "confidence": 0.88, "guardrail_notes": ["risk_ok"]},
        {"ts": "2026-03-01T10:00:00", "symbol": "WIPRO", "type": "bar", "price": 505.0,
         "regime": "TREND", "confidence": 0.88, "guardrail_notes": []},
        # Different symbol — should be excluded
        {"ts": "2026-03-01T10:00:00", "symbol": "RELIANCE", "type": "bar", "price": 2900.0,
         "regime": "TREND", "confidence": 0.7, "guardrail_notes": []},
        {"ts": "2026-03-01T12:00:00", "symbol": "WIPRO", "type": "target", "price": 510.0,
         "regime": "TREND", "confidence": 0.88, "guardrail_notes": []},
    ]
    events_path = _make_events(tmp_path, events)

    replay = load_trade_replay(1, db_path, events_path=events_path)
    assert replay is not None
    assert len(replay.frames) == 3  # WIPRO events only

    first = replay.frames[0]
    assert isinstance(first, TradeReplayFrame)
    assert first.action == "entry"
    assert first.price == pytest.approx(500.0)
    assert first.regime == "TREND"
    assert first.signal_confidence == pytest.approx(0.88)
    assert first.guardrail_notes == ["risk_ok"]

    # Target frame
    last = replay.frames[2]
    assert last.action == "target"
    assert last.price == pytest.approx(510.0)


def test_load_trade_replay_handles_missing_events_log(tmp_path):
    """Gracefully handles a non-existent events log — frames is empty."""
    db_path = _make_db(
        tmp_path,
        trades=[
            {"symbol": "LT", "entry_price": 3000.0, "exit_price": 3050.0,
             "pnl": 50.0, "charges": 8.0,
             "opened_at": "2026-04-01T09:30:00", "closed_at": "2026-04-01T13:00:00"},
        ],
    )
    replay = load_trade_replay(1, db_path, events_path=str(tmp_path / "missing.jsonl"))
    assert replay is not None
    assert replay.frames == []


def test_load_trade_replay_no_signal_graceful(tmp_path):
    """Trade with no matching signal still returns a valid TradeReplay."""
    db_path = _make_db(
        tmp_path,
        trades=[
            {"symbol": "SBIN", "entry_price": 800.0, "exit_price": 795.0,
             "pnl": -5.0, "charges": 4.0,
             "opened_at": "2026-05-01T09:30:00", "closed_at": "2026-05-01T15:00:00"},
        ],
    )
    replay = load_trade_replay(1, db_path)
    assert replay is not None
    assert replay.regime_at_entry == ""
    assert replay.signal_rationale == ""
    assert replay.stop_loss == 0.0
    assert replay.target == 0.0
