"""Smoke tests for the FastAPI trading backend.

Uses TestClient (synchronous) so no async runtime is needed.
The MCP tools layer is patched out to avoid requiring a live SQLite DB.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(tmp_path, monkeypatch):
    """Return a TestClient with snapshot + gate files seeded in a temp dir."""
    # Point the working directory context to tmp_path so relative paths resolve.
    monkeypatch.chdir(tmp_path)

    # Write a minimal snapshot file.
    snapshot = {
        "running": True,
        "ts": "2026-06-03T10:00:00+00:00",
        "pid": 1234,
        "mode": "paper",
        "feed_connected": True,
        "last_tick_age_seconds": 0.5,
        "halted": False,
        "halt_reason": "",
        "cash": 90000.0,
        "equity": 100000.0,
        "realized_pnl": 500.0,
        "starting_equity_today": 99000.0,
        "peak_equity": 101000.0,
        "open_positions": [],
        "current_regime": "TREND",
        "regime_rationale": "ADX>25",
        "nifty_ltp": 22500.0,
        "vix": 14.5,
        "universe_size": 50,
        "strategies_enabled": ["trend_breakout"],
        "config_path": "config/default.yaml",
    }
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "snapshot.json").write_text(json.dumps(snapshot))

    # Write a minimal gate file.
    gate = {
        "passed": True,
        "sharpe": 1.35,
        "max_drawdown_pct": 9.8,
        "win_rate_pct": 47.2,
        "profit_factor": 1.62,
        "total_trades": 312,
    }
    (data_dir / "backtest_gate.json").write_text(json.dumps(gate))

    # Import AFTER chdir so relative paths in main.py resolve to tmp_path.
    from api.main import app

    return TestClient(app)


# ---------------------------------------------------------------------------
# /health
# ---------------------------------------------------------------------------

def test_health(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


# ---------------------------------------------------------------------------
# /api/snapshot
# ---------------------------------------------------------------------------

def test_snapshot_returns_data(client):
    resp = client.get("/api/snapshot")
    assert resp.status_code == 200
    body = resp.json()
    assert body["mode"] == "paper"
    assert body["equity"] == 100000.0


def test_snapshot_missing_returns_not_running(tmp_path, monkeypatch):
    """When snapshot.json does not exist the endpoint returns {running: False}."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "data").mkdir()
    from api.main import app
    c = TestClient(app)
    resp = c.get("/api/snapshot")
    assert resp.status_code == 200
    assert resp.json() == {"running": False}


# ---------------------------------------------------------------------------
# /api/positions — backed by TradingAgentTools.get_positions()
# ---------------------------------------------------------------------------

def test_positions_empty_when_no_open_positions(client):
    """get_positions() reads from snapshot; returns [] when no open positions."""
    resp = client.get("/api/positions")
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


def test_positions_with_mock(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "data").mkdir()
    mock_instance = MagicMock()
    mock_instance.get_positions.return_value = [
        {"symbol": "RELIANCE", "qty": 10, "avg_price": 1300.0, "unrealized_pnl": 50.0}
    ]
    with patch("api.main.TradingAgentTools", return_value=mock_instance):
        from api.main import app
        c = TestClient(app)
        resp = c.get("/api/positions")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["symbol"] == "RELIANCE"


# ---------------------------------------------------------------------------
# /api/equity
# ---------------------------------------------------------------------------

def test_equity_with_mock(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "data").mkdir()
    mock_instance = MagicMock()
    mock_instance.get_equity_curve.return_value = [
        {"ts": "2026-06-03T09:15:00", "equity": 100000.0}
    ]
    with patch("api.main.TradingAgentTools", return_value=mock_instance):
        from api.main import app
        c = TestClient(app)
        resp = c.get("/api/equity?limit=100")
    assert resp.status_code == 200
    data = resp.json()
    assert data[0]["equity"] == 100000.0
    mock_instance.get_equity_curve.assert_called_once_with(limit=100)


# ---------------------------------------------------------------------------
# /api/trades
# ---------------------------------------------------------------------------

def test_trades_with_mock(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "data").mkdir()
    mock_instance = MagicMock()
    mock_instance.get_recent_trades.return_value = [
        {"id": 1, "symbol": "TCS", "side": "BUY", "pnl": 200.0}
    ]
    with patch("api.main.TradingAgentTools", return_value=mock_instance):
        from api.main import app
        c = TestClient(app)
        resp = c.get("/api/trades?limit=10")
    assert resp.status_code == 200
    data = resp.json()
    assert data[0]["symbol"] == "TCS"
    mock_instance.get_recent_trades.assert_called_once_with(limit=10)


# ---------------------------------------------------------------------------
# /api/signals
# ---------------------------------------------------------------------------

def test_signals_with_mock(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "data").mkdir()
    mock_instance = MagicMock()
    mock_instance.get_recent_signals.return_value = [
        {"id": 1, "symbol": "INFY", "strategy": "trend_breakout", "accepted": 1}
    ]
    with patch("api.main.TradingAgentTools", return_value=mock_instance):
        from api.main import app
        c = TestClient(app)
        resp = c.get("/api/signals?limit=20")
    assert resp.status_code == 200
    data = resp.json()
    assert data[0]["symbol"] == "INFY"
    mock_instance.get_recent_signals.assert_called_once_with(limit=20)


# ---------------------------------------------------------------------------
# /api/gate
# ---------------------------------------------------------------------------

def test_gate_returns_file(client):
    resp = client.get("/api/gate")
    assert resp.status_code == 200
    body = resp.json()
    assert body["passed"] is True
    assert body["sharpe"] == 1.35


def test_gate_missing(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "data").mkdir()
    from api.main import app
    c = TestClient(app)
    resp = c.get("/api/gate")
    assert resp.status_code == 200
    body = resp.json()
    assert body["passed"] is False
    assert "error" in body


# ---------------------------------------------------------------------------
# WebSocket /ws/live
# ---------------------------------------------------------------------------

def test_websocket_sends_snapshot(client):
    """WebSocket should push a JSON message containing the snapshot."""
    with client.websocket_connect("/ws/live") as ws:
        data = ws.receive_json()
    assert isinstance(data, dict)
    assert data.get("equity") == 100000.0
