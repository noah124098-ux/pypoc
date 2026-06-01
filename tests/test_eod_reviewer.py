"""Tests for core.llm.eod_reviewer."""
from __future__ import annotations

import json
import sqlite3
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from core.llm.eod_reviewer import (
    ParameterSuggestion,
    ReviewReport,
    _build_prompt,
    _parse_response,
    _query_last_24h,
    run_eod_review,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_db(tmp_path: Path) -> str:
    """Create a minimal SQLite DB with the expected schema and seed data."""
    db_path = str(tmp_path / "test.db")
    conn = sqlite3.connect(db_path)
    conn.executescript("""
        CREATE TABLE trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT, side TEXT, qty INTEGER,
            entry_price REAL, exit_price REAL,
            pnl REAL, charges REAL, strategy TEXT,
            exit_reason TEXT, opened_at TEXT, closed_at TEXT
        );
        CREATE TABLE signals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT, symbol TEXT, side TEXT,
            strategy TEXT, regime TEXT,
            entry_price REAL, stop_loss REAL, target REAL,
            confidence REAL, rationale TEXT,
            accepted INTEGER, rejection_reason TEXT
        );
        CREATE TABLE guardrail_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT, rule TEXT, symbol TEXT, detail TEXT
        );
        CREATE TABLE regime_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT, regime TEXT,
            nifty_change_pct REAL, vix REAL, adx REAL, bb_width REAL
        );
    """)

    now = datetime.utcnow().isoformat()
    recent = (datetime.utcnow() - timedelta(hours=1)).isoformat()

    # One closed trade today
    conn.execute(
        "INSERT INTO trades VALUES (NULL,?,?,?,?,?,?,?,?,?,?,?)",
        ("RELIANCE", "BUY", 5, 2900.0, 2950.0, 250.0, 12.5,
         "trend_breakout", "stop_target", recent, now),
    )

    # Accepted signal
    conn.execute(
        "INSERT INTO signals VALUES (NULL,?,?,?,?,?,?,?,?,?,?,?,?)",
        (now, "TCS", "BUY", "rsi_momentum", "TREND", 3500.0, 3450.0, 3600.0,
         0.8, "RSI oversold", 1, None),
    )

    # Rejected signal
    conn.execute(
        "INSERT INTO signals VALUES (NULL,?,?,?,?,?,?,?,?,?,?,?,?)",
        (now, "INFY", "BUY", "trend_breakout", "RANGE", 1700.0, 1670.0, 1760.0,
         0.5, "Breakout", 0, "wrong_regime"),
    )

    # Guardrail event
    conn.execute(
        "INSERT INTO guardrail_events VALUES (NULL,?,?,?,?)",
        (now, "qty_zero", "HDFCBANK", "ATR stop too wide, qty rounds to 0"),
    )

    # Regime entries
    conn.execute(
        "INSERT INTO regime_log VALUES (NULL,?,?,?,?,?,?)",
        (now, "TREND", -0.3, 14.5, 28.0, 0.04),
    )
    conn.execute(
        "INSERT INTO regime_log VALUES (NULL,?,?,?,?,?,?)",
        (now, "RANGE", 0.1, 13.0, 18.0, 0.02),
    )

    conn.commit()
    conn.close()
    return db_path


_VALID_RESPONSE = json.dumps({
    "summary": "Quiet day. One trade taken, profitable.",
    "suggestions": [
        {
            "strategy": "trend_breakout",
            "parameter": "atr_multiplier",
            "current_value": 2.0,
            "suggested_value": 1.5,
            "rationale": "Wide stops causing qty=0 rejections.",
        }
    ],
    "flags": ["low_trade_count"],
})


def _make_mock_client(response_text: str = _VALID_RESPONSE) -> MagicMock:
    mock_client = MagicMock()
    mock_msg = MagicMock()
    mock_msg.content = [MagicMock(text=response_text)]
    mock_client.messages.create.return_value = mock_msg
    return mock_client


# ---------------------------------------------------------------------------
# Tests: _query_last_24h
# ---------------------------------------------------------------------------

def test_query_last_24h_missing_db(tmp_path):
    result = _query_last_24h(str(tmp_path / "nonexistent.db"))
    assert result["trades"] == []
    assert result["signals_accepted"] == []
    assert result["signals_rejected"] == []
    assert result["guardrail_events"] == []
    assert result["regime_counts"] == {}


def test_query_last_24h_populated(tmp_path):
    db_path = _make_db(tmp_path)
    result = _query_last_24h(db_path)
    assert len(result["trades"]) == 1
    assert result["trades"][0]["symbol"] == "RELIANCE"
    assert len(result["signals_accepted"]) == 1
    assert len(result["signals_rejected"]) == 1
    assert len(result["guardrail_events"]) == 1
    assert result["guardrail_events"][0]["rule"] == "qty_zero"
    assert "TREND" in result["regime_counts"]
    assert "RANGE" in result["regime_counts"]


# ---------------------------------------------------------------------------
# Tests: _build_prompt
# ---------------------------------------------------------------------------

def test_build_prompt_contains_key_sections(tmp_path):
    db_path = _make_db(tmp_path)
    data = _query_last_24h(db_path)
    snapshot = {"equity": 1050000.0, "halted": False}
    prompt = _build_prompt(data, snapshot)

    assert "TODAY'S TRADING SUMMARY" in prompt
    assert "RELIANCE" in prompt
    assert "trend_breakout" in prompt
    assert "qty_zero" in prompt
    assert "TREND" in prompt
    assert "INSTRUCTIONS" in prompt
    assert '"summary"' in prompt


def test_build_prompt_no_trades(tmp_path):
    data = {
        "trades": [],
        "signals_accepted": [],
        "signals_rejected": [],
        "guardrail_events": [],
        "regime_counts": {},
    }
    prompt = _build_prompt(data, {})
    assert "no closed trades today" in prompt


# ---------------------------------------------------------------------------
# Tests: _parse_response
# ---------------------------------------------------------------------------

def test_parse_response_valid():
    summary, suggestions, flags = _parse_response(_VALID_RESPONSE)
    assert summary == "Quiet day. One trade taken, profitable."
    assert len(suggestions) == 1
    s = suggestions[0]
    assert isinstance(s, ParameterSuggestion)
    assert s.strategy == "trend_breakout"
    assert s.parameter == "atr_multiplier"
    assert s.current_value == 2.0
    assert s.suggested_value == 1.5
    assert "qty=0" in s.rationale
    assert flags == ["low_trade_count"]


def test_parse_response_empty_arrays():
    raw = json.dumps({"summary": "Boring day.", "suggestions": [], "flags": []})
    summary, suggestions, flags = _parse_response(raw)
    assert summary == "Boring day."
    assert suggestions == []
    assert flags == []


def test_parse_response_markdown_fence():
    wrapped = "```json\n" + _VALID_RESPONSE + "\n```"
    summary, suggestions, flags = _parse_response(wrapped)
    assert summary == "Quiet day. One trade taken, profitable."
    assert len(suggestions) == 1


def test_parse_response_invalid_json_raises():
    import pytest
    with pytest.raises(json.JSONDecodeError):
        _parse_response("not json at all")


# ---------------------------------------------------------------------------
# Tests: run_eod_review
# ---------------------------------------------------------------------------

def test_run_eod_review_returns_none_when_no_api_key(tmp_path):
    db_path = _make_db(tmp_path)
    result = run_eod_review(
        db_path=db_path,
        snapshot_path=str(tmp_path / "snap.json"),
        api_key="",
    )
    assert result is None


def test_run_eod_review_with_mock_client(tmp_path):
    db_path = _make_db(tmp_path)
    mock_client = _make_mock_client()

    report = run_eod_review(
        db_path=db_path,
        snapshot_path=str(tmp_path / "snap.json"),
        api_key="",
        client=mock_client,
    )

    assert report is not None
    assert isinstance(report, ReviewReport)
    assert report.summary == "Quiet day. One trade taken, profitable."
    assert len(report.suggestions) == 1
    assert report.flags == ["low_trade_count"]
    assert _VALID_RESPONSE in report.raw_response or report.raw_response == _VALID_RESPONSE


def test_run_eod_review_fields_populated(tmp_path):
    db_path = _make_db(tmp_path)
    mock_client = _make_mock_client()

    report = run_eod_review(
        db_path=db_path,
        snapshot_path=str(tmp_path / "snap.json"),
        api_key="sk-fake",
        client=mock_client,
    )

    assert report is not None
    s = report.suggestions[0]
    assert s.strategy == "trend_breakout"
    assert s.current_value == 2.0
    assert s.suggested_value == 1.5


def test_run_eod_review_api_failure_returns_none(tmp_path):
    db_path = _make_db(tmp_path)
    mock_client = MagicMock()
    mock_client.messages.create.side_effect = RuntimeError("network error")

    result = run_eod_review(
        db_path=db_path,
        snapshot_path=str(tmp_path / "snap.json"),
        api_key="sk-fake",
        client=mock_client,
    )

    assert result is None


def test_run_eod_review_bad_json_returns_parse_error_report(tmp_path):
    db_path = _make_db(tmp_path)
    mock_client = _make_mock_client(response_text="this is not json")

    report = run_eod_review(
        db_path=db_path,
        snapshot_path=str(tmp_path / "snap.json"),
        api_key="sk-fake",
        client=mock_client,
    )

    assert report is not None
    assert "parse error" in report.summary
    assert "response_parse_error" in report.flags
    assert report.raw_response == "this is not json"


def test_run_eod_review_missing_db_still_works(tmp_path):
    """Should not raise even if db_path doesn't exist — just sends an empty-data prompt."""
    mock_client = _make_mock_client()
    report = run_eod_review(
        db_path=str(tmp_path / "missing.db"),
        snapshot_path=str(tmp_path / "snap.json"),
        api_key="",
        client=mock_client,
    )
    assert report is not None
    assert isinstance(report, ReviewReport)


def test_run_eod_review_calls_anthropic_with_correct_model(tmp_path):
    db_path = _make_db(tmp_path)
    mock_client = _make_mock_client()

    run_eod_review(
        db_path=db_path,
        snapshot_path=str(tmp_path / "snap.json"),
        model="claude-opus-4-7",
        api_key="sk-fake",
        client=mock_client,
    )

    call_kwargs = mock_client.messages.create.call_args
    assert call_kwargs.kwargs.get("model") == "claude-opus-4-7" or \
           (call_kwargs.args and call_kwargs.args[0] == "claude-opus-4-7") or \
           call_kwargs.kwargs.get("model") == "claude-opus-4-7"
    # Verify model was passed correctly via keyword
    assert mock_client.messages.create.called
    _, kwargs = mock_client.messages.create.call_args
    assert kwargs.get("model") == "claude-opus-4-7"
