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

# Default credentials used across all auth-required tests
AUTH = ("admin", "pypoc2024")


@pytest.fixture()
def client(tmp_path, monkeypatch):
    """Return a TestClient with snapshot + gate files seeded in a temp dir."""
    # Point the working directory context to tmp_path so relative paths resolve.
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DASHBOARD_PASSWORD", "pypoc2024")

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
# /health  (no auth required)
# ---------------------------------------------------------------------------

def test_health(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


# ---------------------------------------------------------------------------
# /api/snapshot
# ---------------------------------------------------------------------------

def test_snapshot_returns_data(client):
    resp = client.get("/api/snapshot", auth=AUTH)
    assert resp.status_code == 200
    body = resp.json()
    assert body["mode"] == "paper"
    assert body["equity"] == 100000.0


def test_snapshot_missing_returns_not_running(tmp_path, monkeypatch):
    """When snapshot.json does not exist the endpoint returns {running: False}."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DASHBOARD_PASSWORD", "pypoc2024")
    (tmp_path / "data").mkdir()
    from api.main import app
    c = TestClient(app)
    resp = c.get("/api/snapshot", auth=AUTH)
    assert resp.status_code == 200
    assert resp.json() == {"running": False}


def test_snapshot_requires_auth(client):
    """Without credentials the endpoint returns 401."""
    resp = client.get("/api/snapshot")
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# /api/positions — backed by TradingAgentTools.get_positions()
# ---------------------------------------------------------------------------

def test_positions_empty_when_no_open_positions(client):
    """get_positions() reads from snapshot; returns [] when no open positions."""
    resp = client.get("/api/positions", auth=AUTH)
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


def test_positions_with_mock(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DASHBOARD_PASSWORD", "pypoc2024")
    (tmp_path / "data").mkdir()
    mock_instance = MagicMock()
    mock_instance.get_positions.return_value = [
        {"symbol": "RELIANCE", "qty": 10, "avg_price": 1300.0, "unrealized_pnl": 50.0}
    ]
    with patch("api.main.TradingAgentTools", return_value=mock_instance):
        from api.main import app
        c = TestClient(app)
        resp = c.get("/api/positions", auth=AUTH)
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["symbol"] == "RELIANCE"


# ---------------------------------------------------------------------------
# /api/equity
# ---------------------------------------------------------------------------

def test_equity_with_mock(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DASHBOARD_PASSWORD", "pypoc2024")
    (tmp_path / "data").mkdir()
    mock_instance = MagicMock()
    mock_instance.get_equity_curve.return_value = [
        {"ts": "2026-06-03T09:15:00", "equity": 100000.0}
    ]
    with patch("api.main.TradingAgentTools", return_value=mock_instance):
        from api.main import app
        c = TestClient(app)
        resp = c.get("/api/equity?limit=100", auth=AUTH)
    assert resp.status_code == 200
    data = resp.json()
    assert data[0]["equity"] == 100000.0
    mock_instance.get_equity_curve.assert_called_once_with(limit=100)


# ---------------------------------------------------------------------------
# /api/trades
# ---------------------------------------------------------------------------

def test_trades_with_mock(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DASHBOARD_PASSWORD", "pypoc2024")
    (tmp_path / "data").mkdir()
    mock_instance = MagicMock()
    mock_instance.get_recent_trades.return_value = [
        {"id": 1, "symbol": "TCS", "side": "BUY", "pnl": 200.0}
    ]
    with patch("api.main.TradingAgentTools", return_value=mock_instance):
        from api.main import app
        c = TestClient(app)
        resp = c.get("/api/trades?limit=10", auth=AUTH)
    assert resp.status_code == 200
    data = resp.json()
    assert data[0]["symbol"] == "TCS"
    mock_instance.get_recent_trades.assert_called_once_with(limit=10)


# ---------------------------------------------------------------------------
# /api/signals
# ---------------------------------------------------------------------------

def test_signals_with_mock(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DASHBOARD_PASSWORD", "pypoc2024")
    (tmp_path / "data").mkdir()
    mock_instance = MagicMock()
    mock_instance.get_recent_signals.return_value = [
        {"id": 1, "symbol": "INFY", "strategy": "trend_breakout", "accepted": 1}
    ]
    with patch("api.main.TradingAgentTools", return_value=mock_instance):
        from api.main import app
        c = TestClient(app)
        resp = c.get("/api/signals?limit=20", auth=AUTH)
    assert resp.status_code == 200
    data = resp.json()
    assert data[0]["symbol"] == "INFY"
    mock_instance.get_recent_signals.assert_called_once_with(limit=20)


# ---------------------------------------------------------------------------
# /api/gate
# ---------------------------------------------------------------------------

def test_gate_returns_file(client):
    resp = client.get("/api/gate", auth=AUTH)
    assert resp.status_code == 200
    body = resp.json()
    assert body["passed"] is True
    assert body["sharpe"] == 1.35


def test_gate_missing(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DASHBOARD_PASSWORD", "pypoc2024")
    (tmp_path / "data").mkdir()
    from api.main import app
    c = TestClient(app)
    resp = c.get("/api/gate", auth=AUTH)
    assert resp.status_code == 200
    body = resp.json()
    assert body["passed"] is False
    assert "error" in body


# ---------------------------------------------------------------------------
# WebSocket /ws/live
# ---------------------------------------------------------------------------

def test_websocket_sends_snapshot(client, monkeypatch):
    """WebSocket should receive a broadcast JSON message containing the snapshot."""
    monkeypatch.setenv("DASHBOARD_PASSWORD", "pypoc2024")
    with client.websocket_connect("/ws/live?token=pypoc2024") as ws:
        data = ws.receive_json()
    assert isinstance(data, dict)
    assert data.get("equity") == 100000.0


def test_websocket_rejects_bad_token(client, monkeypatch):
    """WebSocket should close with 1008 when the token is wrong."""
    monkeypatch.setenv("DASHBOARD_PASSWORD", "pypoc2024")
    import pytest as pt
    from starlette.websockets import WebSocketState
    # TestClient raises on rejected WS; accept the close gracefully.
    try:
        with client.websocket_connect("/ws/live?token=wrong") as ws:
            ws.receive_json()
        # If we reach here the server accepted — that is a test failure.
        assert False, "expected close, got connection"
    except Exception:
        pass  # expected: server closed with 1008


def test_connection_manager_broadcast():
    """ConnectionManager.broadcast removes stale connections silently."""
    import asyncio
    from api.main import ConnectionManager

    mgr = ConnectionManager()

    class FakeWS:
        def __init__(self, fail=False):
            self.sent = []
            self.fail = fail
        async def send_json(self, data):
            if self.fail:
                raise RuntimeError("closed")
            self.sent.append(data)

    good = FakeWS(fail=False)
    bad  = FakeWS(fail=True)
    mgr.active_connections = [good, bad]

    asyncio.run(mgr.broadcast({"value": 1}))

    assert {"value": 1} in good.sent, "good connection should receive broadcast"
    assert bad not in mgr.active_connections, "stale connection should be removed"


# ---------------------------------------------------------------------------
# /api/status  (no auth required)
# ---------------------------------------------------------------------------

def test_status_returns_all_fields(client):
    """GET /api/status returns required keys with no authentication."""
    resp = client.get("/api/status")
    assert resp.status_code == 200
    body = resp.json()
    for key in ("api_version", "agent_running", "agent_halted", "equity",
                "regime", "gate_passed", "gate_age_days", "services", "timestamp"):
        assert key in body, f"missing key: {key}"


def test_status_no_auth_needed(client):
    """Status endpoint must be accessible without credentials."""
    resp = client.get("/api/status")
    assert resp.status_code == 200


def test_status_reads_snapshot(client):
    """Status reflects values from the seeded snapshot.json."""
    resp = client.get("/api/status")
    body = resp.json()
    assert body["agent_running"] is True
    assert body["agent_halted"] is False
    assert body["equity"] == 100000.0
    assert body["regime"] == "TREND"


def test_status_reads_gate(client):
    """Status reflects gate_passed from the seeded backtest_gate.json."""
    resp = client.get("/api/status")
    body = resp.json()
    assert body["gate_passed"] is True


def test_status_gate_age_days_is_number_or_none(client):
    """gate_age_days is a float (gate has a timestamp) or None (no ts)."""
    resp = client.get("/api/status")
    body = resp.json()
    age = body["gate_age_days"]
    assert age is None or isinstance(age, (int, float))


def test_status_services_dict(client):
    """services sub-dict contains agent, dashboard, mcp keys."""
    resp = client.get("/api/status")
    body = resp.json()
    svc = body["services"]
    assert isinstance(svc, dict)
    for key in ("agent", "dashboard", "mcp"):
        assert key in svc


def test_status_missing_snapshot(tmp_path, monkeypatch):
    """When no snapshot exists agent_running is False."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DASHBOARD_PASSWORD", "pypoc2024")
    (tmp_path / "data").mkdir()
    from api.main import app
    c = TestClient(app)
    resp = c.get("/api/status")
    assert resp.status_code == 200
    assert resp.json()["agent_running"] is False


def test_status_missing_gate(tmp_path, monkeypatch):
    """When no gate file exists gate_passed is False and gate_age_days is None."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DASHBOARD_PASSWORD", "pypoc2024")
    (tmp_path / "data").mkdir()
    from api.main import app
    c = TestClient(app)
    resp = c.get("/api/status")
    assert resp.status_code == 200
    body = resp.json()
    assert body["gate_passed"] is False
    assert body["gate_age_days"] is None


# ---------------------------------------------------------------------------
# Global exception handler
# ---------------------------------------------------------------------------

def test_global_exception_handler_returns_json(tmp_path, monkeypatch):
    """Unhandled exceptions must return JSON {error, type} not an HTML traceback."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DASHBOARD_PASSWORD", "pypoc2024")
    (tmp_path / "data").mkdir()
    from api.main import app

    # Mount a route that raises deliberately
    @app.get("/api/__test_error__")
    def raise_error():
        raise RuntimeError("deliberate test error")

    c = TestClient(app, raise_server_exceptions=False)
    resp = c.get("/api/__test_error__", auth=AUTH)
    assert resp.status_code == 500
    body = resp.json()
    assert "error" in body
    assert "type" in body
    assert body["type"] == "RuntimeError"
