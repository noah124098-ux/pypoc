"""Tests for core/analytics/decision_trace.py."""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from core.analytics.decision_trace import trace_signal_decision


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _make_db(tmp_path: Path, signals=None, trades=None) -> str:
    db_path = str(tmp_path / "test.db")
    conn = sqlite3.connect(db_path)
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
    if signals:
        for s in signals:
            conn.execute(
                "INSERT INTO signals (ts, symbol, side, strategy, regime, entry_price, "
                "stop_loss, target, confidence, rationale, accepted, rejection_reason) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    s["ts"], s["symbol"], s.get("side", "BUY"),
                    s.get("strategy", "trend_breakout"), s.get("regime", "TREND"),
                    s["entry_price"], s.get("stop_loss", 0.0), s.get("target", 0.0),
                    s.get("confidence", 0.85), s.get("rationale", ""),
                    int(s.get("accepted", 1)), s.get("rejection_reason"),
                ),
            )
    if trades:
        for t in trades:
            conn.execute(
                "INSERT INTO trades (symbol, side, qty, entry_price, exit_price, pnl, "
                "charges, strategy, exit_reason, opened_at, closed_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (
                    t["symbol"], t.get("side", "BUY"), t.get("qty", 10),
                    t["entry_price"], t.get("exit_price", t["entry_price"]),
                    t.get("pnl", 0.0), t.get("charges", 0.0),
                    t.get("strategy", "trend_breakout"), t.get("exit_reason", "target"),
                    t["opened_at"], t.get("closed_at", t["opened_at"]),
                ),
            )
    conn.commit()
    conn.close()
    return db_path


# ---------------------------------------------------------------------------
# error / empty paths
# ---------------------------------------------------------------------------

def test_returns_empty_dict_for_missing_db(tmp_path):
    result = trace_signal_decision(1, str(tmp_path / "nonexistent.db"))
    assert result == {}


def test_returns_empty_dict_for_unknown_signal_id(tmp_path):
    db_path = _make_db(tmp_path)
    result = trace_signal_decision(999, db_path)
    assert result == {}


# ---------------------------------------------------------------------------
# accepted signal
# ---------------------------------------------------------------------------

def test_accepted_signal_returns_correct_structure(tmp_path):
    db_path = _make_db(
        tmp_path,
        signals=[{
            "ts": "2026-01-05T09:28:00", "symbol": "INFY",
            "entry_price": 1400.0, "stop_loss": 1370.0, "target": 1450.0,
            "confidence": 0.9, "rationale": "Breakout", "accepted": 1,
        }],
        trades=[{
            "symbol": "INFY", "entry_price": 1400.0, "qty": 10,
            "opened_at": "2026-01-05T09:28:30",
            "closed_at": "2026-01-05T14:00:00",
        }],
    )
    result = trace_signal_decision(1, db_path)
    assert result != {}
    assert result["final_decision"] == "ACCEPTED"
    assert result["rejection_reason"] is None
    assert result["signal"]["symbol"] == "INFY"
    assert result["signal"]["entry_price"] == pytest.approx(1400.0)
    assert result["signal"]["stop_loss"] == pytest.approx(1370.0)
    assert result["signal"]["confidence"] == pytest.approx(0.9)
    assert isinstance(result["guardrail_checks"], list)
    assert len(result["guardrail_checks"]) > 0


def test_accepted_signal_sizing_fields_present(tmp_path):
    db_path = _make_db(
        tmp_path,
        signals=[{
            "ts": "2026-01-06T09:30:00", "symbol": "HDFC",
            "entry_price": 1600.0, "stop_loss": 1565.0, "target": 1670.0,
            "confidence": 0.8, "accepted": 1,
        }],
        trades=[{
            "symbol": "HDFC", "entry_price": 1600.0, "qty": 5,
            "opened_at": "2026-01-06T09:30:30",
            "closed_at": "2026-01-06T14:00:00",
        }],
    )
    result = trace_signal_decision(1, db_path)
    sz = result["sizing"]
    assert sz["final_qty"] == 5
    assert sz["raw_qty"] == 5
    assert sz["risk_per_share"] == pytest.approx(35.0)


def test_all_guardrail_checks_pass_for_accepted_signal(tmp_path):
    db_path = _make_db(
        tmp_path,
        signals=[{
            "ts": "2026-02-01T10:00:00", "symbol": "TCS",
            "entry_price": 3500.0, "stop_loss": 3450.0, "target": 3600.0,
            "accepted": 1,
        }],
        trades=[{
            "symbol": "TCS", "entry_price": 3500.0, "qty": 3,
            "opened_at": "2026-02-01T10:00:30",
            "closed_at": "2026-02-01T14:00:00",
        }],
    )
    result = trace_signal_decision(1, db_path)
    checks = result["guardrail_checks"]
    # All checks whose detail does not say "N/A" should pass for accepted signal
    for chk in checks:
        assert chk["passed"] in (True, None), (
            f"Rule {chk['rule']} unexpectedly failed for an accepted signal"
        )


# ---------------------------------------------------------------------------
# rejected signal
# ---------------------------------------------------------------------------

def test_rejected_signal_marks_fired_rule_as_failed(tmp_path):
    db_path = _make_db(
        tmp_path,
        signals=[{
            "ts": "2026-03-01T09:30:00", "symbol": "WIPRO",
            "entry_price": 500.0, "stop_loss": 490.0, "target": 520.0,
            "accepted": 0,
            "rejection_reason": "max_open_positions: already 5 open",
        }],
    )
    result = trace_signal_decision(1, db_path)
    assert result["final_decision"] == "REJECTED"
    assert result["rejection_reason"] == "max_open_positions: already 5 open"

    fired = [c for c in result["guardrail_checks"] if c["rule"] == "max_open_positions"]
    assert fired, "max_open_positions check not found"
    assert fired[0]["passed"] is False


def test_rejected_signal_later_rules_marked_not_reached(tmp_path):
    db_path = _make_db(
        tmp_path,
        signals=[{
            "ts": "2026-04-01T09:30:00", "symbol": "LT",
            "entry_price": 3000.0, "stop_loss": 2950.0, "target": 3100.0,
            "accepted": 0,
            "rejection_reason": "market_window: past no-new-entries cutoff",
        }],
    )
    result = trace_signal_decision(1, db_path)
    checks = result["guardrail_checks"]
    rules_by_name = {c["rule"]: c for c in checks}

    # market_window should be failed
    assert rules_by_name["market_window"]["passed"] is False

    # Rules after market_window should be not-reached (passed=None)
    after_market_window = [
        "stale_data", "black_swan_nifty", "black_swan_vix",
        "max_open_positions", "per_trade_risk", "max_position_size",
        "liquidity", "spread", "daily_loss_circuit",
        "drawdown_circuit", "signal_cooldown", "duplicate_position",
    ]
    for rule in after_market_window:
        if rule in rules_by_name:
            assert rules_by_name[rule]["passed"] is None, (
                f"Expected {rule} to be None (not reached) but got {rules_by_name[rule]['passed']}"
            )


# ---------------------------------------------------------------------------
# edge cases
# ---------------------------------------------------------------------------

def test_signal_without_matching_trade(tmp_path):
    """Accepted signal with no trade row should still return valid trace."""
    db_path = _make_db(
        tmp_path,
        signals=[{
            "ts": "2026-05-01T09:30:00", "symbol": "SBIN",
            "entry_price": 800.0, "stop_loss": 785.0, "target": 830.0,
            "accepted": 1,
        }],
    )
    result = trace_signal_decision(1, db_path)
    assert result["final_decision"] == "ACCEPTED"
    assert result["sizing"]["final_qty"] == 0   # no trade row → qty = 0
