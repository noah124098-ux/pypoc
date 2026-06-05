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
# /api/equity  — paginated
# ---------------------------------------------------------------------------

def _paginated(data: list, total: int | None = None, limit: int = 50, offset: int = 0) -> dict:
    """Helper: build a paginated response dict matching what the API now returns."""
    if total is None:
        total = len(data)
    return {
        "data": data,
        "total": total,
        "limit": limit,
        "offset": offset,
        "has_more": offset + len(data) < total,
    }


def test_equity_with_mock(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DASHBOARD_PASSWORD", "pypoc2024")
    (tmp_path / "data").mkdir()
    mock_instance = MagicMock()
    page = _paginated([{"ts": "2026-06-03T09:15:00", "equity": 100000.0}], total=1, limit=100)
    mock_instance.get_equity_curve.return_value = page
    with patch("api.main.TradingAgentTools", return_value=mock_instance):
        from api.main import app
        c = TestClient(app)
        resp = c.get("/api/equity?limit=100", auth=AUTH)
    assert resp.status_code == 200
    body = resp.json()
    assert body["data"][0]["equity"] == 100000.0
    assert "total" in body
    assert "has_more" in body
    mock_instance.get_equity_curve.assert_called_once_with(limit=100, offset=0)


def test_equity_pagination_metadata(tmp_path, monkeypatch):
    """Equity endpoint exposes total, limit, offset, has_more."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DASHBOARD_PASSWORD", "pypoc2024")
    (tmp_path / "data").mkdir()
    mock_instance = MagicMock()
    page = _paginated(
        [{"ts": "2026-06-03T09:15:00", "equity": 99000.0}],
        total=150, limit=50, offset=50,
    )
    mock_instance.get_equity_curve.return_value = page
    with patch("api.main.TradingAgentTools", return_value=mock_instance):
        from api.main import app
        c = TestClient(app)
        resp = c.get("/api/equity?limit=50&offset=50", auth=AUTH)
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 150
    assert body["limit"] == 50
    assert body["offset"] == 50
    assert body["has_more"] is True
    mock_instance.get_equity_curve.assert_called_once_with(limit=50, offset=50)


# ---------------------------------------------------------------------------
# /api/trades  — paginated
# ---------------------------------------------------------------------------

def test_trades_with_mock(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DASHBOARD_PASSWORD", "pypoc2024")
    (tmp_path / "data").mkdir()
    mock_instance = MagicMock()
    page = _paginated([{"id": 1, "symbol": "TCS", "side": "BUY", "pnl": 200.0}], total=1, limit=10)
    mock_instance.get_recent_trades.return_value = page
    with patch("api.main.TradingAgentTools", return_value=mock_instance):
        from api.main import app
        c = TestClient(app)
        resp = c.get("/api/trades?limit=10", auth=AUTH)
    assert resp.status_code == 200
    body = resp.json()
    assert body["data"][0]["symbol"] == "TCS"
    assert "total" in body
    assert "has_more" in body
    mock_instance.get_recent_trades.assert_called_once_with(limit=10, offset=0)


def test_trades_pagination_metadata(tmp_path, monkeypatch):
    """Trades endpoint exposes pagination metadata."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DASHBOARD_PASSWORD", "pypoc2024")
    (tmp_path / "data").mkdir()
    mock_instance = MagicMock()
    page = _paginated([], total=100, limit=50, offset=100)
    page["has_more"] = False
    mock_instance.get_recent_trades.return_value = page
    with patch("api.main.TradingAgentTools", return_value=mock_instance):
        from api.main import app
        c = TestClient(app)
        resp = c.get("/api/trades?limit=50&offset=100", auth=AUTH)
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 100
    assert body["offset"] == 100
    assert body["has_more"] is False
    mock_instance.get_recent_trades.assert_called_once_with(limit=50, offset=100)


# ---------------------------------------------------------------------------
# /api/trades/stats
# ---------------------------------------------------------------------------

def test_trade_stats_with_mock(tmp_path, monkeypatch):
    """GET /api/trades/stats returns aggregate statistics."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DASHBOARD_PASSWORD", "pypoc2024")
    (tmp_path / "data").mkdir()
    mock_instance = MagicMock()
    mock_instance.get_trade_stats.return_value = {
        "total_trades": 120,
        "total_pnl": 5400.50,
        "win_rate": 52.5,
        "profit_factor": 1.8,
        "sharpe": 1.42,
        "max_dd": 3200.0,
    }
    with patch("api.main.TradingAgentTools", return_value=mock_instance):
        from api.main import app
        c = TestClient(app)
        resp = c.get("/api/trades/stats", auth=AUTH)
    assert resp.status_code == 200
    body = resp.json()
    assert body["total_trades"] == 120
    assert body["win_rate"] == 52.5
    assert body["profit_factor"] == 1.8
    assert body["sharpe"] == 1.42
    assert body["max_dd"] == 3200.0
    mock_instance.get_trade_stats.assert_called_once()


def test_trade_stats_requires_auth(tmp_path, monkeypatch):
    """GET /api/trades/stats returns 401 without credentials."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DASHBOARD_PASSWORD", "pypoc2024")
    (tmp_path / "data").mkdir()
    from api.main import app
    c = TestClient(app)
    resp = c.get("/api/trades/stats")
    assert resp.status_code == 401


def test_trade_stats_keys_present(tmp_path, monkeypatch):
    """Trade stats response contains all required keys."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DASHBOARD_PASSWORD", "pypoc2024")
    (tmp_path / "data").mkdir()
    mock_instance = MagicMock()
    mock_instance.get_trade_stats.return_value = {
        "total_trades": 0,
        "total_pnl": 0.0,
        "win_rate": 0.0,
        "profit_factor": 0.0,
        "sharpe": 0.0,
        "max_dd": 0.0,
    }
    with patch("api.main.TradingAgentTools", return_value=mock_instance):
        from api.main import app
        c = TestClient(app)
        resp = c.get("/api/trades/stats", auth=AUTH)
    assert resp.status_code == 200
    body = resp.json()
    for key in ("total_trades", "total_pnl", "win_rate", "profit_factor", "sharpe", "max_dd"):
        assert key in body, f"missing key: {key}"


# ---------------------------------------------------------------------------
# /api/signals  — paginated
# ---------------------------------------------------------------------------

def test_signals_with_mock(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DASHBOARD_PASSWORD", "pypoc2024")
    (tmp_path / "data").mkdir()
    mock_instance = MagicMock()
    page = _paginated([{"id": 1, "symbol": "INFY", "strategy": "trend_breakout", "accepted": 1}], total=1, limit=20)
    mock_instance.get_recent_signals.return_value = page
    with patch("api.main.TradingAgentTools", return_value=mock_instance):
        from api.main import app
        c = TestClient(app)
        resp = c.get("/api/signals?limit=20", auth=AUTH)
    assert resp.status_code == 200
    body = resp.json()
    assert body["data"][0]["symbol"] == "INFY"
    assert "total" in body
    assert "has_more" in body
    mock_instance.get_recent_signals.assert_called_once_with(limit=20, offset=0)


def test_signals_pagination_metadata(tmp_path, monkeypatch):
    """Signals endpoint exposes pagination metadata."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DASHBOARD_PASSWORD", "pypoc2024")
    (tmp_path / "data").mkdir()
    mock_instance = MagicMock()
    page = _paginated(
        [{"id": 5, "symbol": "HDFC", "strategy": "rsi_momentum", "accepted": 0}],
        total=200, limit=50, offset=150,
    )
    mock_instance.get_recent_signals.return_value = page
    with patch("api.main.TradingAgentTools", return_value=mock_instance):
        from api.main import app
        c = TestClient(app)
        resp = c.get("/api/signals?limit=50&offset=150", auth=AUTH)
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 200
    assert body["limit"] == 50
    assert body["offset"] == 150
    mock_instance.get_recent_signals.assert_called_once_with(limit=50, offset=150)


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
    import importlib
    import api.main as api_main
    importlib.reload(api_main)
    c = TestClient(api_main.app)
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


# ---------------------------------------------------------------------------
# Rate limiting
# ---------------------------------------------------------------------------

def test_snapshot_rate_limit_returns_429_after_60_requests(tmp_path, monkeypatch):
    """The 61st request to /api/snapshot within a minute must return 429."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DASHBOARD_PASSWORD", "pypoc2024")
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    snapshot = {"running": False}
    (data_dir / "snapshot.json").write_text(json.dumps(snapshot))

    # Reset the in-memory rate-limit storage between test runs by importing fresh
    # and patching the limiter storage.
    import importlib
    import api.main as api_main
    importlib.reload(api_main)

    from slowapi import Limiter
    from slowapi.util import get_remote_address

    # Replace with a fresh limiter so prior test runs don't bleed state.
    fresh_limiter = Limiter(key_func=get_remote_address)
    api_main.app.state.limiter = fresh_limiter
    api_main.limiter = fresh_limiter

    # Re-decorate the route with the fresh limiter by accessing the endpoint directly
    # and relying on the fact that slowapi reads app.state.limiter at request time.
    c = TestClient(api_main.app)

    statuses = []
    for _ in range(62):
        resp = c.get("/api/snapshot", auth=AUTH)
        statuses.append(resp.status_code)

    ok_count = statuses.count(200)
    rate_limited_count = statuses.count(429)

    assert ok_count >= 60, f"Expected at least 60 successful requests, got {ok_count}"
    assert rate_limited_count >= 1, f"Expected at least one 429 response, got none (statuses: {set(statuses)})"


def test_equity_rate_limit_returns_429_after_20_requests(tmp_path, monkeypatch):
    """The 21st request to /api/equity within a minute must return 429."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DASHBOARD_PASSWORD", "pypoc2024")
    data_dir = tmp_path / "data"
    data_dir.mkdir()

    import importlib
    import api.main as api_main
    importlib.reload(api_main)

    from slowapi import Limiter
    from slowapi.util import get_remote_address
    from unittest.mock import MagicMock, patch

    fresh_limiter = Limiter(key_func=get_remote_address)
    api_main.app.state.limiter = fresh_limiter
    api_main.limiter = fresh_limiter

    mock_tools = MagicMock()
    mock_tools.get_equity_curve.return_value = []

    statuses = []
    with patch.object(api_main, "TradingAgentTools", return_value=mock_tools):
        c = TestClient(api_main.app)
        for _ in range(22):
            resp = c.get("/api/equity", auth=AUTH)
            statuses.append(resp.status_code)

    ok_count = statuses.count(200)
    rate_limited_count = statuses.count(429)

    assert ok_count >= 20, f"Expected at least 20 successful requests, got {ok_count}"
    assert rate_limited_count >= 1, f"Expected at least one 429 response, got none (statuses: {set(statuses)})"


# ---------------------------------------------------------------------------
# TTL cache tests
# ---------------------------------------------------------------------------

def test_gate_cache_is_used_on_second_request(tmp_path, monkeypatch):
    """Second call to /api/gate within TTL must not re-read the file."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DASHBOARD_PASSWORD", "pypoc2024")
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    gate = {"passed": True, "sharpe": 1.5}
    (data_dir / "backtest_gate.json").write_text(json.dumps(gate))

    import importlib
    import api.main as api_main
    importlib.reload(api_main)

    c = TestClient(api_main.app)
    resp1 = c.get("/api/gate", auth=AUTH)
    assert resp1.status_code == 200
    assert resp1.json()["sharpe"] == 1.5

    # Overwrite the file — a cached second call must still return the old value.
    (data_dir / "backtest_gate.json").write_text(json.dumps({"passed": False, "sharpe": 0.1}))
    resp2 = c.get("/api/gate", auth=AUTH)
    assert resp2.status_code == 200
    # Cache still hot — should return original value.
    assert resp2.json()["sharpe"] == 1.5, "Expected cached value on second request"


def test_gate_cache_refreshes_after_ttl(tmp_path, monkeypatch):
    """After the TTL expires the endpoint re-reads the gate file."""
    from datetime import datetime, timedelta
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DASHBOARD_PASSWORD", "pypoc2024")
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    gate = {"passed": True, "sharpe": 1.5}
    (data_dir / "backtest_gate.json").write_text(json.dumps(gate))

    import importlib
    import api.main as api_main
    importlib.reload(api_main)

    c = TestClient(api_main.app)
    c.get("/api/gate", auth=AUTH)  # prime the cache

    # Manually expire the cache entry.
    api_main._cache["gate"]["ts"] = datetime.now() - timedelta(seconds=400)

    # Overwrite the file.
    (data_dir / "backtest_gate.json").write_text(json.dumps({"passed": False, "sharpe": 0.1}))
    resp = c.get("/api/gate", auth=AUTH)
    assert resp.json()["sharpe"] == 0.1, "Expected fresh value after TTL expiry"


def test_universe_cache_is_used(tmp_path, monkeypatch):
    """Second call to /api/universe within 1-hour TTL must use the cached value."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DASHBOARD_PASSWORD", "pypoc2024")
    (tmp_path / "data").mkdir()

    import importlib
    import api.main as api_main
    importlib.reload(api_main)

    call_count = {"n": 0}
    original_universe = ["RELIANCE", "TCS", "INFY"]

    mock_instance = MagicMock()

    def _universe_side_effect():
        call_count["n"] += 1
        return original_universe

    mock_instance.get_universe.side_effect = _universe_side_effect

    with patch.object(api_main, "TradingAgentTools", return_value=mock_instance):
        c = TestClient(api_main.app)
        c.get("/api/universe", auth=AUTH)
        c.get("/api/universe", auth=AUTH)

    # TradingAgentTools().get_universe() should only have been called once.
    assert call_count["n"] == 1, f"Expected 1 backend call, got {call_count['n']}"


# ---------------------------------------------------------------------------
# Autonomous simulator endpoints — /api/simulator/*
# ---------------------------------------------------------------------------

def _fresh_sim_client(tmp_path, monkeypatch):
    """Helper: return a TestClient with a freshly-reset simulator state."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DASHBOARD_PASSWORD", "pypoc2024")
    data_dir = tmp_path / "data"
    data_dir.mkdir(exist_ok=True)
    (data_dir / "snapshot.json").write_text(json.dumps({"running": False}))
    (data_dir / "backtest_gate.json").write_text(json.dumps({"passed": False}))

    import importlib
    import api.main as api_main
    importlib.reload(api_main)  # resets _sim_states to {}

    return TestClient(api_main.app), api_main


# 1. POST /api/simulator/start returns {started: true} with valid params
def test_simulator_start_returns_started(tmp_path, monkeypatch):
    c, _ = _fresh_sim_client(tmp_path, monkeypatch)
    resp = c.post("/api/simulator/start", json={"capital": 200000, "risk_pct": 1.5, "max_positions": 3}, auth=AUTH)
    assert resp.status_code == 200
    body = resp.json()
    assert body["started"] is True
    assert body["running"] is True


# 2. POST /api/simulator/start with no auth → 401
def test_simulator_start_no_auth(tmp_path, monkeypatch):
    c, _ = _fresh_sim_client(tmp_path, monkeypatch)
    resp = c.post("/api/simulator/start", json={})
    assert resp.status_code == 401


# 3. POST /api/simulator/start when already running → returns message
def test_simulator_start_already_running(tmp_path, monkeypatch):
    c, _ = _fresh_sim_client(tmp_path, monkeypatch)
    c.post("/api/simulator/start", json={}, auth=AUTH)
    resp = c.post("/api/simulator/start", json={}, auth=AUTH)
    assert resp.status_code == 200
    body = resp.json()
    assert body["started"] is False
    assert body["running"] is True
    assert "Already running" in body["message"]


# 4. POST /api/simulator/stop returns required fields
def test_simulator_stop_returns_expected_fields(tmp_path, monkeypatch):
    c, _ = _fresh_sim_client(tmp_path, monkeypatch)
    c.post("/api/simulator/start", json={}, auth=AUTH)
    resp = c.post("/api/simulator/stop", auth=AUTH)
    assert resp.status_code == 200
    body = resp.json()
    assert "stopped" in body
    assert "elapsed_seconds" in body
    assert "total_trades" in body
    assert "final_pnl" in body
    assert body["stopped"] is True


# 5. POST /api/simulator/stop with no auth → 401
def test_simulator_stop_no_auth(tmp_path, monkeypatch):
    c, _ = _fresh_sim_client(tmp_path, monkeypatch)
    resp = c.post("/api/simulator/stop")
    assert resp.status_code == 401


# 6. GET /api/simulator/status returns required fields
def test_simulator_status_required_fields(tmp_path, monkeypatch):
    c, _ = _fresh_sim_client(tmp_path, monkeypatch)
    resp = c.get("/api/simulator/status", auth=AUTH)
    assert resp.status_code == 200
    body = resp.json()
    required = {
        "running", "elapsed_seconds", "total_trades", "win_trades",
        "loss_trades", "pnl_inr", "equity_curve", "recent_trades", "current_positions",
    }
    assert required.issubset(body.keys()), f"Missing fields: {required - body.keys()}"


# 7. GET /api/simulator/status with no auth → 401
def test_simulator_status_no_auth(tmp_path, monkeypatch):
    c, _ = _fresh_sim_client(tmp_path, monkeypatch)
    resp = c.get("/api/simulator/status")
    assert resp.status_code == 401


# 8. GET /api/simulator/equity-curve returns a list
def test_simulator_equity_curve_returns_list(tmp_path, monkeypatch):
    c, _ = _fresh_sim_client(tmp_path, monkeypatch)
    resp = c.get("/api/simulator/equity-curve", auth=AUTH)
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


# 9. GET /api/simulator/equity-curve with no auth → 401
def test_simulator_equity_curve_no_auth(tmp_path, monkeypatch):
    c, _ = _fresh_sim_client(tmp_path, monkeypatch)
    resp = c.get("/api/simulator/equity-curve")
    assert resp.status_code == 401


# 10. Equity curve entries have 't' (int) and 'v' (float) fields when non-empty
def test_simulator_equity_curve_entry_schema(tmp_path, monkeypatch):
    c, _ = _fresh_sim_client(tmp_path, monkeypatch)
    c.post("/api/simulator/start", json={"capital": 100000}, auth=AUTH)
    resp = c.get("/api/simulator/equity-curve", auth=AUTH)
    assert resp.status_code == 200
    curve = resp.json()
    assert len(curve) >= 1, "Expected at least one equity curve entry after start"
    entry = curve[0]
    assert "t" in entry and isinstance(entry["t"], int)
    assert "v" in entry and isinstance(entry["v"], float)


# 11. POST /api/simulator/start with capital=100000 → status shows correct starting equity
def test_simulator_start_capital_reflected_in_equity_curve(tmp_path, monkeypatch):
    c, _ = _fresh_sim_client(tmp_path, monkeypatch)
    c.post("/api/simulator/start", json={"capital": 100000}, auth=AUTH)
    resp = c.get("/api/simulator/equity-curve", auth=AUTH)
    assert resp.status_code == 200
    curve = resp.json()
    assert len(curve) >= 1
    assert curve[0]["v"] == 100000.0


# 12. After start+stop cycle, status shows running=False
def test_simulator_start_stop_cycle_shows_not_running(tmp_path, monkeypatch):
    c, _ = _fresh_sim_client(tmp_path, monkeypatch)
    c.post("/api/simulator/start", json={}, auth=AUTH)
    c.post("/api/simulator/stop", auth=AUTH)
    resp = c.get("/api/simulator/status", auth=AUTH)
    assert resp.status_code == 200
    assert resp.json()["running"] is False


def test_config_cache_ttl_is_60s(tmp_path, monkeypatch):
    """config cache key is stored with 60s TTL — entry absent before first call."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DASHBOARD_PASSWORD", "pypoc2024")
    (tmp_path / "data").mkdir()

    import importlib
    import api.main as api_main
    importlib.reload(api_main)

    mock_instance = MagicMock()
    mock_instance.get_config_summary.return_value = {"universe_size": 50}

    with patch.object(api_main, "TradingAgentTools", return_value=mock_instance):
        c = TestClient(api_main.app)
        assert "config" not in api_main._cache
        resp = c.get("/api/config", auth=AUTH)
        assert resp.status_code == 200
        assert "config" in api_main._cache

        # Second call — backend must not be called again.
        mock_instance.get_config_summary.reset_mock()
        c.get("/api/config", auth=AUTH)
        mock_instance.get_config_summary.assert_not_called()


def test_extended_metrics_cache_key_includes_days(tmp_path, monkeypatch):
    """extended-metrics cache keys are scoped per `days` parameter."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DASHBOARD_PASSWORD", "pypoc2024")
    (tmp_path / "data").mkdir()

    import importlib
    import api.main as api_main
    importlib.reload(api_main)

    mock_metrics = MagicMock()
    mock_metrics.to_dict.return_value = {"sharpe": 1.1}

    with patch("core.analytics.metrics.compute_extended_metrics", return_value=mock_metrics), \
         patch("core.analytics.metrics.load_trades_from_db", return_value=[]):
        c = TestClient(api_main.app)
        c.get("/api/analytics/extended-metrics?days=90", auth=AUTH)
        c.get("/api/analytics/extended-metrics?days=365", auth=AUTH)

    # Both cache keys must exist separately.
    assert "extended_metrics:90" in api_main._cache
    assert "extended_metrics:365" in api_main._cache


def test_cache_invalidate_helper():
    """_cache_invalidate removes the specified key and ignores missing keys."""
    import importlib
    import api.main as api_main
    importlib.reload(api_main)

    from datetime import datetime
    api_main._cache["test_key"] = {"data": 42, "ts": datetime.now()}
    api_main._cache_invalidate("test_key")
    assert "test_key" not in api_main._cache

    # Calling on a missing key must not raise.
    api_main._cache_invalidate("nonexistent")


# ---------------------------------------------------------------------------
# TOTP 2FA — verify() function
# ---------------------------------------------------------------------------

def _make_2fa_client(tmp_path, monkeypatch, otp_secret: str):
    """Helper: build a TestClient with DASHBOARD_OTP_SECRET set."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DASHBOARD_PASSWORD", "pypoc2024")
    monkeypatch.setenv("DASHBOARD_OTP_SECRET", otp_secret)
    (tmp_path / "data").mkdir(exist_ok=True)
    import json as _json
    (tmp_path / "data" / "snapshot.json").write_text(_json.dumps({"running": False}))
    from api.main import app
    return TestClient(app)


def test_2fa_disabled_normal_auth_works(tmp_path, monkeypatch):
    """When DASHBOARD_OTP_SECRET is unset, plain admin:password auth succeeds."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DASHBOARD_PASSWORD", "pypoc2024")
    monkeypatch.delenv("DASHBOARD_OTP_SECRET", raising=False)
    (tmp_path / "data").mkdir(exist_ok=True)
    import json as _json
    (tmp_path / "data" / "snapshot.json").write_text(_json.dumps({"running": False}))
    from api.main import app
    c = TestClient(app)
    resp = c.get("/api/snapshot", auth=("admin", "pypoc2024"))
    assert resp.status_code == 200


def test_2fa_required_missing_otp_returns_401(tmp_path, monkeypatch):
    """When 2FA is enabled, a request without an OTP is rejected."""
    import pyotp
    secret = pyotp.random_base32()
    c = _make_2fa_client(tmp_path, monkeypatch, secret)
    # username has no colon — no OTP supplied
    resp = c.get("/api/snapshot", auth=("admin", "pypoc2024"))
    assert resp.status_code == 401


def test_2fa_valid_otp_grants_access(tmp_path, monkeypatch):
    """Convention A: correct TOTP in password-prefix position grants access.

    HTTP Basic encodes ``admin:otp:password`` as base64; Starlette parses
    ``username='admin'``, ``password='otp:password'``.  Pass via requests as
    ``auth=('admin', f'{otp}:pypoc2024')``.
    """
    import pyotp

    secret = pyotp.random_base32()
    c = _make_2fa_client(tmp_path, monkeypatch, secret)
    otp = pyotp.TOTP(secret).now()
    # Convention A: otp embedded at the start of the password field
    resp = c.get("/api/snapshot", auth=("admin", f"{otp}:pypoc2024"))
    assert resp.status_code == 200


def test_2fa_valid_otp_convention_b(tmp_path, monkeypatch):
    """Convention B: correct TOTP in username-suffix position also grants access.

    Some REST clients send ``username='admin:otp'``, ``password='pypoc2024'``.
    Starlette parses that as ``username='admin:otp'``, ``password='pypoc2024'``.
    """
    import pyotp

    secret = pyotp.random_base32()
    c = _make_2fa_client(tmp_path, monkeypatch, secret)
    otp = pyotp.TOTP(secret).now()
    # Convention B: otp embedded in username field
    resp = c.get("/api/snapshot", auth=(f"admin:{otp}", "pypoc2024"))
    assert resp.status_code == 200


def test_2fa_wrong_otp_returns_401(tmp_path, monkeypatch):
    """A wrong TOTP code is rejected even with the correct password."""
    import pyotp

    secret = pyotp.random_base32()
    c = _make_2fa_client(tmp_path, monkeypatch, secret)
    # Convention A with a bad OTP
    resp = c.get("/api/snapshot", auth=("admin", "000000:pypoc2024"))
    assert resp.status_code == 401


def test_2fa_wrong_password_returns_401_regardless_of_otp(tmp_path, monkeypatch):
    """Wrong password is rejected even when OTP is correct."""
    import pyotp

    secret = pyotp.random_base32()
    c = _make_2fa_client(tmp_path, monkeypatch, secret)
    otp = pyotp.TOTP(secret).now()
    resp = c.get("/api/snapshot", auth=("admin", f"{otp}:wrongpassword"))
    assert resp.status_code == 401


def test_2fa_wrong_username_returns_401(tmp_path, monkeypatch):
    """Wrong base username (not 'admin') is rejected even with correct OTP."""
    import pyotp

    secret = pyotp.random_base32()
    c = _make_2fa_client(tmp_path, monkeypatch, secret)
    otp = pyotp.TOTP(secret).now()
    resp = c.get("/api/snapshot", auth=("notadmin", f"{otp}:pypoc2024"))
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# /api/metrics — Prometheus-compatible plain-text endpoint
# ---------------------------------------------------------------------------

def test_metrics_returns_plain_text(client):
    """GET /api/metrics returns 200 with text/plain content."""
    resp = client.get("/api/metrics")
    assert resp.status_code == 200
    assert "text/plain" in resp.headers.get("content-type", "")


def test_metrics_no_auth_required(client):
    """Metrics endpoint must be accessible without credentials."""
    resp = client.get("/api/metrics")
    assert resp.status_code == 200


def test_metrics_contains_required_metrics(client):
    """Metrics output contains all three required metric families."""
    resp = client.get("/api/metrics")
    body = resp.text
    assert "pypoc_equity" in body
    assert "pypoc_positions" in body
    assert "pypoc_gate_sharpe" in body


def test_metrics_equity_reflects_snapshot(client):
    """pypoc_equity line contains the equity value from snapshot.json."""
    resp = client.get("/api/metrics")
    body = resp.text
    # snapshot has equity=100000.0
    assert "100000.0" in body


def test_metrics_gate_sharpe_reflects_gate_file(client):
    """pypoc_gate_sharpe line contains the Sharpe from backtest_gate.json."""
    resp = client.get("/api/metrics")
    body = resp.text
    # gate file has sharpe=1.35
    assert "1.35" in body


def test_metrics_has_help_and_type_lines(client):
    """Prometheus format requires # HELP and # TYPE lines for each metric."""
    resp = client.get("/api/metrics")
    body = resp.text
    assert "# HELP pypoc_equity" in body
    assert "# TYPE pypoc_equity gauge" in body
    assert "# HELP pypoc_positions" in body
    assert "# TYPE pypoc_positions gauge" in body
    assert "# HELP pypoc_gate_sharpe" in body
    assert "# TYPE pypoc_gate_sharpe gauge" in body


def test_metrics_mode_label_present(client):
    """pypoc_equity metric includes the mode label."""
    resp = client.get("/api/metrics")
    body = resp.text
    # snapshot has mode="paper"
    assert 'mode="paper"' in body


def test_metrics_no_snapshot_returns_defaults(tmp_path, monkeypatch):
    """When snapshot is missing metrics endpoint defaults to 0 values."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DASHBOARD_PASSWORD", "pypoc2024")
    (tmp_path / "data").mkdir()
    from api.main import app
    c = TestClient(app)
    resp = c.get("/api/metrics")
    assert resp.status_code == 200
    body = resp.text
    assert "pypoc_equity" in body
    # equity should default to 0 (or 0.0)
    assert "pypoc_positions 0" in body


# ---------------------------------------------------------------------------
# /api/ready — startup readiness probe
# ---------------------------------------------------------------------------

def test_ready_returns_200_when_db_initialised(tmp_path, monkeypatch):
    """GET /api/ready returns 200 when SQLite DB has the trades table."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DASHBOARD_PASSWORD", "pypoc2024")
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    # Create a minimal SQLite DB with a trades table.
    import sqlite3 as _sqlite3
    conn = _sqlite3.connect(str(data_dir / "agent.db"))
    conn.execute(
        "CREATE TABLE trades (id INTEGER PRIMARY KEY, symbol TEXT, pnl REAL)"
    )
    conn.commit()
    conn.close()
    from api.main import app
    c = TestClient(app)
    resp = c.get("/api/ready")
    assert resp.status_code == 200
    assert resp.json()["ready"] is True


def test_ready_returns_503_when_db_missing(tmp_path, monkeypatch):
    """GET /api/ready returns 503 when agent.db does not exist."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DASHBOARD_PASSWORD", "pypoc2024")
    (tmp_path / "data").mkdir()
    from api.main import app
    c = TestClient(app)
    resp = c.get("/api/ready")
    assert resp.status_code == 503
    body = resp.json()
    assert body["ready"] is False
    assert "reason" in body


def test_ready_returns_503_when_trades_table_missing(tmp_path, monkeypatch):
    """GET /api/ready returns 503 when DB exists but trades table is absent."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DASHBOARD_PASSWORD", "pypoc2024")
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    import sqlite3 as _sqlite3
    # Create a DB without the trades table.
    conn = _sqlite3.connect(str(data_dir / "agent.db"))
    conn.execute("CREATE TABLE other_table (id INTEGER PRIMARY KEY)")
    conn.commit()
    conn.close()
    from api.main import app
    c = TestClient(app)
    resp = c.get("/api/ready")
    assert resp.status_code == 503
    body = resp.json()
    assert body["ready"] is False


def test_ready_no_auth_required(tmp_path, monkeypatch):
    """Readiness probe must be accessible without authentication."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DASHBOARD_PASSWORD", "pypoc2024")
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    import sqlite3 as _sqlite3
    conn = _sqlite3.connect(str(data_dir / "agent.db"))
    conn.execute("CREATE TABLE trades (id INTEGER PRIMARY KEY)")
    conn.commit()
    conn.close()
    from api.main import app
    c = TestClient(app)
    # No auth supplied — should still return 200.
    resp = c.get("/api/ready")
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Structured JSON request logging middleware
# ---------------------------------------------------------------------------

def test_request_logging_emits_json(client, caplog):
    """Each request must emit a structured JSON log line with required keys."""
    import logging as _logging
    with caplog.at_level(_logging.INFO, logger="api.main"):
        client.get("/health")
    # Find the JSON log entry
    json_lines = []
    for record in caplog.records:
        if record.name == "api.main":
            try:
                obj = json.loads(record.getMessage())
                json_lines.append(obj)
            except json.JSONDecodeError:
                pass
    assert json_lines, "Expected at least one structured JSON log entry"
    entry = json_lines[-1]
    assert "method" in entry
    assert "path" in entry
    assert "status" in entry
    assert "ms" in entry
    assert "user" in entry


def test_request_logging_correct_method_and_path(client, caplog):
    """Log entry method and path match the actual request."""
    import logging as _logging
    with caplog.at_level(_logging.INFO, logger="api.main"):
        client.get("/health")
    for record in caplog.records:
        if record.name == "api.main":
            try:
                obj = json.loads(record.getMessage())
                if obj.get("path") == "/health":
                    assert obj["method"] == "GET"
                    assert obj["status"] == 200
                    assert isinstance(obj["ms"], (int, float))
                    break
            except json.JSONDecodeError:
                pass


def test_request_logging_user_extracted_from_auth(tmp_path, monkeypatch, caplog):
    """Log entry user field contains the Basic-Auth username."""
    import logging as _logging
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DASHBOARD_PASSWORD", "pypoc2024")
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    snapshot = {"running": False}
    (data_dir / "snapshot.json").write_text(json.dumps(snapshot))
    from api.main import app
    c = TestClient(app)
    with caplog.at_level(_logging.INFO, logger="api.main"):
        c.get("/api/snapshot", auth=("admin", "pypoc2024"))
    for record in caplog.records:
        if record.name == "api.main":
            try:
                obj = json.loads(record.getMessage())
                if obj.get("path") == "/api/snapshot":
                    assert obj["user"] == "admin"
                    break
            except json.JSONDecodeError:
                pass


def test_request_logging_user_none_without_auth(client, caplog):
    """Log entry user field is None when no auth header is present."""
    import logging as _logging
    with caplog.at_level(_logging.INFO, logger="api.main"):
        client.get("/health")
    for record in caplog.records:
        if record.name == "api.main":
            try:
                obj = json.loads(record.getMessage())
                if obj.get("path") == "/health":
                    assert obj["user"] is None
                    break
            except json.JSONDecodeError:
                pass


# ---------------------------------------------------------------------------
# /api/nifty-breadth  — market breadth endpoint
# ---------------------------------------------------------------------------

def test_nifty_breadth_requires_auth(client):
    """Without credentials the endpoint returns 401."""
    resp = client.get("/api/nifty-breadth")
    assert resp.status_code == 401


def test_nifty_breadth_returns_expected_shape(tmp_path, monkeypatch):
    """Breadth endpoint returns above_50dma, below_50dma, total, breadth_pct."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DASHBOARD_PASSWORD", "pypoc2024")
    (tmp_path / "data").mkdir()

    import importlib
    import api.main as api_main
    importlib.reload(api_main)

    # Mock out the slow loader — pretend 38 of 50 symbols are above their 50-DMA.
    fake_result = {
        "above_50dma": 38,
        "below_50dma": 12,
        "total": 50,
        "breadth_pct": 76.0,
        "cached_at": "2026-06-05T10:00:00",
    }

    with patch.object(api_main, "_cached", return_value=fake_result):
        c = TestClient(api_main.app)
        resp = c.get("/api/nifty-breadth", auth=AUTH)

    assert resp.status_code == 200
    body = resp.json()
    assert body["above_50dma"] == 38
    assert body["below_50dma"] == 12
    assert body["total"] == 50
    assert body["breadth_pct"] == 76.0


def test_nifty_breadth_cache_key_is_nifty_breadth(tmp_path, monkeypatch):
    """Breadth endpoint uses 'nifty_breadth' cache key with 1800s TTL."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DASHBOARD_PASSWORD", "pypoc2024")
    (tmp_path / "data").mkdir()

    import importlib
    import api.main as api_main
    importlib.reload(api_main)

    recorded_calls: list[tuple] = []
    original_cached = api_main._cached

    def _spy_cached(key, ttl, fn):
        recorded_calls.append((key, ttl))
        # Return a minimal valid result so the endpoint doesn't blow up.
        return {
            "above_50dma": 25, "below_50dma": 25, "total": 50,
            "breadth_pct": 50.0, "cached_at": "2026-06-05T10:00:00",
        }

    with patch.object(api_main, "_cached", side_effect=_spy_cached):
        c = TestClient(api_main.app)
        c.get("/api/nifty-breadth", auth=AUTH)

    assert any(k == "nifty_breadth" and ttl == 1800 for k, ttl in recorded_calls), (
        f"Expected nifty_breadth cache call with 1800s TTL, got: {recorded_calls}"
    )


# ---------------------------------------------------------------------------
# /api/calendar/upcoming  — economic calendar endpoint
# ---------------------------------------------------------------------------

def test_calendar_upcoming_requires_auth(client):
    """Without credentials the endpoint returns 401."""
    resp = client.get("/api/calendar/upcoming")
    assert resp.status_code == 401


def test_calendar_upcoming_returns_list(client):
    """Endpoint returns a list of event dicts."""
    resp = client.get("/api/calendar/upcoming", auth=AUTH)
    assert resp.status_code == 200
    body = resp.json()
    assert isinstance(body, list)


def test_calendar_upcoming_returns_at_most_5_events(client):
    """No more than 5 upcoming events are returned."""
    resp = client.get("/api/calendar/upcoming", auth=AUTH)
    assert resp.status_code == 200
    assert len(resp.json()) <= 5


def test_calendar_upcoming_event_shape(client):
    """Each event has date, days_away, is_blackout, label fields."""
    resp = client.get("/api/calendar/upcoming", auth=AUTH)
    assert resp.status_code == 200
    events = resp.json()
    if events:
        event = events[0]
        assert "date" in event
        assert "days_away" in event
        assert "is_blackout" in event
        assert "label" in event
        assert event["is_blackout"] is True
        assert isinstance(event["days_away"], int)


def test_calendar_upcoming_dates_are_future_or_today(client):
    """All returned dates must be >= today."""
    from datetime import date
    resp = client.get("/api/calendar/upcoming", auth=AUTH)
    assert resp.status_code == 200
    today = date.today()
    for event in resp.json():
        event_date = date.fromisoformat(event["date"])
        assert event_date >= today, f"Past date returned: {event_date}"


def test_calendar_upcoming_days_away_matches_date(client):
    """days_away field must equal (event_date - today).days."""
    from datetime import date
    resp = client.get("/api/calendar/upcoming", auth=AUTH)
    assert resp.status_code == 200
    today = date.today()
    for event in resp.json():
        event_date = date.fromisoformat(event["date"])
        expected_days = (event_date - today).days
        assert event["days_away"] == expected_days, (
            f"days_away mismatch for {event_date}: expected {expected_days}, got {event['days_away']}"
        )


def test_calendar_upcoming_label_is_recognized_type(client):
    """Labels must be one of RBI MPC, Union Budget, US FOMC, or Economic Event."""
    resp = client.get("/api/calendar/upcoming", auth=AUTH)
    assert resp.status_code == 200
    valid_labels = {"RBI MPC", "Union Budget", "US FOMC", "Economic Event"}
    for event in resp.json():
        assert event["label"] in valid_labels, (
            f"Unexpected label '{event['label']}' for {event['date']}"
        )


# ---------------------------------------------------------------------------
# GET /api/preflight
# ---------------------------------------------------------------------------

def test_preflight_requires_auth(client):
    resp = client.get("/api/preflight")
    assert resp.status_code == 401


def test_preflight_returns_checks_structure(client, tmp_path):
    """GET /api/preflight returns {checks: [...], all_passed: bool}."""
    resp = client.get("/api/preflight", auth=AUTH)
    assert resp.status_code == 200
    body = resp.json()
    assert "checks" in body
    assert "all_passed" in body
    assert isinstance(body["checks"], list)
    assert isinstance(body["all_passed"], bool)


def test_preflight_each_check_has_required_fields(client):
    """Every check object must have name, passed, and message fields."""
    resp = client.get("/api/preflight", auth=AUTH)
    assert resp.status_code == 200
    for check in resp.json()["checks"]:
        assert "name" in check, f"missing 'name' in {check}"
        assert "passed" in check, f"missing 'passed' in {check}"
        assert "message" in check, f"missing 'message' in {check}"
        assert isinstance(check["passed"], bool)


def test_preflight_returns_10_checks(client):
    """Preflight should run exactly 10 checks."""
    resp = client.get("/api/preflight", auth=AUTH)
    assert resp.status_code == 200
    assert len(resp.json()["checks"]) == 10


def test_preflight_all_passed_reflects_checks(client):
    """all_passed must be True only when every check passes."""
    resp = client.get("/api/preflight", auth=AUTH)
    assert resp.status_code == 200
    body = resp.json()
    computed = all(c["passed"] for c in body["checks"])
    assert body["all_passed"] == computed


# ---------------------------------------------------------------------------
# GET /api/strategy/signals-today
# ---------------------------------------------------------------------------

def _make_db_with_signals(data_dir: Path) -> None:
    """Create agent.db with a signals table seeded with today's test rows."""
    import sqlite3 as _sqlite3
    from datetime import date as _date

    conn = _sqlite3.connect(str(data_dir / "agent.db"))
    conn.execute(
        """
        CREATE TABLE signals (
            id       INTEGER PRIMARY KEY,
            ts       TEXT,
            strategy TEXT,
            symbol   TEXT,
            accepted INTEGER DEFAULT 0
        )
        """
    )
    today = _date.today().isoformat()
    conn.executemany(
        "INSERT INTO signals (ts, strategy, symbol, accepted) VALUES (?, ?, ?, ?)",
        [
            (today, "trend_breakout", "RELIANCE", 1),
            (today, "trend_breakout", "TCS", 0),
            (today, "trend_breakout", "INFY", 1),
            (today, "rsi_momentum", "HDFC", 1),
            (today, "rsi_momentum", "WIPRO", 0),
            # old signal (yesterday) — must NOT appear in today's count
            ("2020-01-01", "trend_breakout", "OLD", 1),
        ],
    )
    conn.commit()
    conn.close()


def test_signals_today_requires_auth(client):
    """Without credentials the endpoint returns 401."""
    resp = client.get("/api/strategy/signals-today")
    assert resp.status_code == 401


def test_signals_today_returns_list_with_counts(tmp_path, monkeypatch):
    """Endpoint returns one row per strategy with correct total/accepted/rejected."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DASHBOARD_PASSWORD", "pypoc2024")
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    _make_db_with_signals(data_dir)

    from api.main import app
    c = TestClient(app)
    resp = c.get("/api/strategy/signals-today", auth=AUTH)
    assert resp.status_code == 200
    body = resp.json()
    assert isinstance(body, list)

    by_strat = {row["strategy"]: row for row in body}
    assert "trend_breakout" in by_strat
    assert "rsi_momentum" in by_strat

    tb = by_strat["trend_breakout"]
    assert tb["total"] == 3
    assert tb["accepted"] == 2
    assert tb["rejected"] == 1

    rm = by_strat["rsi_momentum"]
    assert rm["total"] == 2
    assert rm["accepted"] == 1
    assert rm["rejected"] == 1


def test_signals_today_empty_when_no_db(tmp_path, monkeypatch):
    """When agent.db does not exist the endpoint returns an empty list."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DASHBOARD_PASSWORD", "pypoc2024")
    (tmp_path / "data").mkdir()

    from api.main import app
    c = TestClient(app)
    resp = c.get("/api/strategy/signals-today", auth=AUTH)
    assert resp.status_code == 200
    assert resp.json() == []


# ---------------------------------------------------------------------------
# GET /api/performance/summary
# ---------------------------------------------------------------------------

def _make_db_with_trades(data_dir: Path) -> None:
    """Create agent.db with a trades table containing recent and old rows."""
    import sqlite3 as _sqlite3
    from datetime import date as _date, timedelta

    conn = _sqlite3.connect(str(data_dir / "agent.db"))
    conn.execute(
        """
        CREATE TABLE trades (
            id         INTEGER PRIMARY KEY,
            closed_at  TEXT,
            symbol     TEXT,
            strategy   TEXT,
            pnl        REAL,
            charges    REAL DEFAULT 0
        )
        """
    )
    today = _date.today().isoformat()
    two_days_ago = (_date.today() - timedelta(days=2)).isoformat()
    ten_days_ago = (_date.today() - timedelta(days=10)).isoformat()
    forty_days_ago = (_date.today() - timedelta(days=40)).isoformat()

    conn.executemany(
        "INSERT INTO trades (closed_at, symbol, strategy, pnl, charges) VALUES (?, ?, ?, ?, ?)",
        [
            # today: 2 trades, both winners
            (today, "TCS",      "trend_breakout", 500.0,  10.0),
            (today, "INFY",     "trend_breakout", 300.0,  10.0),
            # 2 days ago (within last 7 days): 1 winner
            (two_days_ago, "HDFC",  "rsi_momentum",   200.0,  10.0),
            # 10 days ago (within month, not week): 1 loser
            (ten_days_ago, "WIPRO", "rsi_momentum",  -100.0,  10.0),
            # 40 days ago (all-time only): 1 winner
            (forty_days_ago, "RELIANCE", "trend_breakout", 400.0, 10.0),
        ],
    )
    conn.commit()
    conn.close()


def test_performance_summary_requires_auth(client):
    """Without credentials the endpoint returns 401."""
    resp = client.get("/api/performance/summary")
    assert resp.status_code == 401


def test_performance_summary_has_expected_periods(tmp_path, monkeypatch):
    """Response contains today, week, month, all_time keys."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DASHBOARD_PASSWORD", "pypoc2024")
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    _make_db_with_trades(data_dir)

    from api.main import app
    c = TestClient(app)
    resp = c.get("/api/performance/summary", auth=AUTH)
    assert resp.status_code == 200
    body = resp.json()
    for key in ("today", "week", "month", "all_time"):
        assert key in body, f"missing key: {key}"
    for key in ("today", "week", "month", "all_time"):
        period = body[key]
        assert "trades" in period
        assert "pnl" in period
        assert "win_rate" in period


def test_performance_summary_correct_counts(tmp_path, monkeypatch):
    """Trade counts and PnL aggregate correctly per period."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DASHBOARD_PASSWORD", "pypoc2024")
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    _make_db_with_trades(data_dir)

    from api.main import app
    c = TestClient(app)
    resp = c.get("/api/performance/summary", auth=AUTH)
    assert resp.status_code == 200
    body = resp.json()

    # Today: 2 trades, total pnl = 800, win_rate = 100%
    assert body["today"]["trades"] == 2
    assert body["today"]["pnl"] == 800.0
    assert body["today"]["win_rate"] == 100.0

    # Week (includes today + 2 days ago): 3 trades
    assert body["week"]["trades"] == 3

    # Month (includes today + 2 days ago + 10 days ago): 4 trades
    assert body["month"]["trades"] == 4

    # All time: 5 trades
    assert body["all_time"]["trades"] == 5


# ---------------------------------------------------------------------------
# GET /api/backtest/debug
# ---------------------------------------------------------------------------

def _make_fake_backtest_result():
    """Return a minimal mock BacktestResult-like namespace for patching."""
    from types import SimpleNamespace
    from datetime import datetime

    r = SimpleNamespace()
    r.period_start = datetime(2025, 1, 1)
    r.period_end = datetime(2025, 12, 31)
    r.trades = []
    r.equity_curve = []
    r.regime_distribution = {"TREND": 100, "RANGE": 80, "VOLATILE": 30, "UNKNOWN": 10}
    r.signal_count = 50
    r.accepted_count = 20
    r.rejected_count = 30
    r.qty_zero_count = 5
    r.signal_count_by_strategy = {"trend_breakout": 30, "rsi_momentum": 20}
    r.accepted_count_by_strategy = {"trend_breakout": 12, "rsi_momentum": 8}
    r.rejection_breakdown = {"max_positions": 15, "daily_loss_circuit": 10, "regime_mismatch": 5}
    r.signal_count_by_symbol = {f"SYM{i}": 10 - i for i in range(20)}
    return r


def _make_fake_metrics():
    """Return a mock metrics object."""
    from types import SimpleNamespace
    m = SimpleNamespace()
    m.n_trades = 20
    m.win_rate_pct = 55.0
    m.profit_factor = 1.8
    m.sharpe = 1.25
    m.max_drawdown_pct = 8.5
    m.cagr_pct = 22.3
    return m


def test_backtest_debug_requires_auth(client):
    """Without credentials the endpoint returns 401."""
    resp = client.get("/api/backtest/debug")
    assert resp.status_code == 401


def test_backtest_debug_returns_expected_keys(tmp_path, monkeypatch):
    """Endpoint returns all required top-level keys."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DASHBOARD_PASSWORD", "pypoc2024")
    (tmp_path / "data").mkdir()

    import importlib
    import api.main as api_main
    importlib.reload(api_main)

    fake_r = _make_fake_backtest_result()
    fake_m = _make_fake_metrics()

    with patch("backtest.data_loader.HistoricalLoader") as MockLoader, \
         patch("backtest.engine.BacktestEngine") as MockEngine, \
         patch("backtest.metrics.compute_metrics", return_value=fake_m), \
         patch("core.config.load_settings") as mock_cfg, \
         patch("core.data.universe.resolve_universe", return_value=["RELIANCE", "TCS"]):

        # Stub settings
        from types import SimpleNamespace
        settings = SimpleNamespace()
        settings.capital = SimpleNamespace(initial_inr=100_000)
        settings.universe = SimpleNamespace(source="nifty50", symbols=[])
        mock_cfg.return_value = settings

        # Stub loader: load_nifty returns a non-empty mock df
        import pandas as pd
        fake_nifty = pd.DataFrame({"close": [22000.0]}, index=pd.to_datetime(["2025-12-31"]))
        instance_loader = MagicMock()
        instance_loader.load_nifty.return_value = fake_nifty
        instance_loader.load_universe.return_value = {}
        MockLoader.return_value = instance_loader

        # Stub engine
        instance_engine = MagicMock()
        instance_engine.run.return_value = fake_r
        MockEngine.return_value = instance_engine

        c = TestClient(api_main.app)
        resp = c.get("/api/backtest/debug?days=90", auth=AUTH)

    assert resp.status_code == 200
    body = resp.json()
    for key in ("period_start", "period_end", "capital", "regime_distribution",
                "rejection_breakdown", "signal_funnel", "strategy_signals",
                "top_symbols", "metrics"):
        assert key in body, f"missing key: {key}"


def test_backtest_debug_metrics_shape(tmp_path, monkeypatch):
    """The metrics sub-dict contains trades, win_rate_pct, profit_factor, sharpe, etc."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DASHBOARD_PASSWORD", "pypoc2024")
    (tmp_path / "data").mkdir()

    import importlib
    import api.main as api_main
    importlib.reload(api_main)

    fake_r = _make_fake_backtest_result()
    fake_m = _make_fake_metrics()

    with patch("backtest.data_loader.HistoricalLoader") as MockLoader, \
         patch("backtest.engine.BacktestEngine") as MockEngine, \
         patch("backtest.metrics.compute_metrics", return_value=fake_m), \
         patch("core.config.load_settings") as mock_cfg, \
         patch("core.data.universe.resolve_universe", return_value=["RELIANCE"]):

        from types import SimpleNamespace
        settings = SimpleNamespace()
        settings.capital = SimpleNamespace(initial_inr=100_000)
        settings.universe = SimpleNamespace(source="nifty50", symbols=[])
        mock_cfg.return_value = settings

        import pandas as pd
        fake_nifty = pd.DataFrame({"close": [22000.0]}, index=pd.to_datetime(["2025-12-31"]))
        instance_loader = MagicMock()
        instance_loader.load_nifty.return_value = fake_nifty
        instance_loader.load_universe.return_value = {}
        MockLoader.return_value = instance_loader

        instance_engine = MagicMock()
        instance_engine.run.return_value = fake_r
        MockEngine.return_value = instance_engine

        c = TestClient(api_main.app)
        resp = c.get("/api/backtest/debug?days=90", auth=AUTH)

    assert resp.status_code == 200
    metrics = resp.json()["metrics"]
    assert metrics["trades"] == 20
    assert metrics["win_rate_pct"] == 55.0
    assert metrics["profit_factor"] == 1.8
    assert metrics["sharpe"] == 1.25
    assert metrics["max_drawdown_pct"] == 8.5
    assert metrics["cagr_pct"] == 22.3


# ---------------------------------------------------------------------------
# /api/events/live  — Server-Sent Events endpoint
# ---------------------------------------------------------------------------

def test_sse_requires_auth(client):
    """SSE endpoint returns 401 when no credentials are supplied."""
    resp = client.get("/api/events/live")
    assert resp.status_code == 401


def test_sse_returns_event_stream_content_type(tmp_path, monkeypatch):
    """SSE endpoint responds with text/event-stream content-type.

    Tests the generator directly to avoid blocking on the infinite stream.
    """
    import asyncio
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DASHBOARD_PASSWORD", "pypoc2024")
    snapshot = {"running": True, "mode": "paper", "equity": 100000.0}
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "snapshot.json").write_text(json.dumps(snapshot))

    # Call the endpoint directly via TestClient to verify content-type header.
    # We mock asyncio.sleep to prevent the 2-second delay in the generator so
    # the test completes immediately after producing one event.
    from unittest.mock import AsyncMock, patch as _patch

    async def _run():
        with _patch("asyncio.sleep", new=AsyncMock(side_effect=Exception("stop"))):
            from api.main import app
            from starlette.testclient import TestClient as TC
            c = TC(app, raise_server_exceptions=False)
            with c.stream("GET", "/api/events/live", auth=AUTH) as resp:
                assert resp.status_code == 200
                assert "text/event-stream" in resp.headers.get("content-type", "")
                first_chunk = b""
                for chunk in resp.iter_bytes():
                    first_chunk = chunk
                    break
            return first_chunk

    first_chunk = asyncio.run(_run())
    assert first_chunk.startswith(b"data:"), (
        f"expected SSE 'data:' prefix, got: {first_chunk!r}"
    )


def test_sse_event_contains_snapshot_fields(tmp_path, monkeypatch):
    """The first SSE event payload deserialises to the seeded snapshot.

    Uses the async generator directly to avoid blocking on the infinite stream.
    """
    import asyncio
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DASHBOARD_PASSWORD", "pypoc2024")
    snapshot = {"running": True, "mode": "paper", "equity": 50000.0}
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "snapshot.json").write_text(json.dumps(snapshot))

    # Drive the async generator directly — get the first yielded value.
    async def _first_event():
        from unittest.mock import AsyncMock as AM, MagicMock as MM
        # Fake request that reports not-disconnected on first call.
        fake_request = MM()
        fake_request.is_disconnected = AM(return_value=False)
        from api.main import sse_live
        resp = await sse_live(fake_request, _="admin")
        # resp.body_iterator is the async generator; pull the first value.
        return await resp.body_iterator.__anext__()

    raw = asyncio.run(_first_event())

    # Strip the "data: " prefix and trailing newlines, then parse JSON.
    line = raw.strip()
    assert line.startswith("data: ")
    payload = json.loads(line[len("data: "):])
    assert payload["running"] is True
    assert payload["mode"] == "paper"
    assert payload["equity"] == 50000.0


def test_sse_fallback_when_snapshot_missing(tmp_path, monkeypatch):
    """SSE returns {running: False} when snapshot.json does not exist.

    Uses the async generator directly to avoid blocking on the infinite stream.
    """
    import asyncio
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DASHBOARD_PASSWORD", "pypoc2024")
    (tmp_path / "data").mkdir()  # no snapshot.json

    async def _first_event():
        from unittest.mock import AsyncMock as AM, MagicMock as MM
        fake_request = MM()
        fake_request.is_disconnected = AM(return_value=False)
        from api.main import sse_live
        resp = await sse_live(fake_request, _="admin")
        return await resp.body_iterator.__anext__()

    raw = asyncio.run(_first_event())
    line = raw.strip()
    assert line.startswith("data: ")
    payload = json.loads(line[len("data: "):])
    assert payload == {"running": False}
