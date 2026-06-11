"""End-to-end integration tests for the React+FastAPI stack.

Uses Starlette's TestClient (wraps httpx) to exercise the full ASGI app —
no external process or network required.
"""
from __future__ import annotations

import base64
import os
from pathlib import Path

import pytest
from starlette.testclient import TestClient

# SPA tests require a built React app.
# Use an absolute path anchored to the repo root (parent of tests/) so the
# check is stable regardless of which cwd other fixtures may have set.
_REPO_ROOT = Path(__file__).parent.parent
_REACT_BUILD_EXISTS = (_REPO_ROOT / "frontend" / "dist").exists()

# ---------------------------------------------------------------------------
# App + client fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def app():
    """Import (or reload) the FastAPI app from the repo root working directory.

    Reloads api.main so that the REACT_BUILD existence check runs from the
    correct working directory, even when other test modules called chdir().
    """
    import importlib
    import sys
    import os

    _orig_cwd = os.getcwd()
    os.chdir(str(_REPO_ROOT))
    try:
        import api.main as _mod
        importlib.reload(_mod)
        return _mod.app
    finally:
        os.chdir(_orig_cwd)


@pytest.fixture(scope="module")
def client(app):
    """Unauthenticated TestClient."""
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


@pytest.fixture(scope="module")
def auth_headers():
    """HTTP Basic credentials for the default dev password."""
    password = os.getenv("DASHBOARD_PASSWORD", "pypoc2024")
    token = base64.b64encode(f"admin:{password}".encode()).decode()
    return {"Authorization": f"Basic {token}"}


@pytest.fixture(scope="module")
def auth_client(app, auth_headers):
    """Authenticated TestClient with default credentials pre-applied."""
    with TestClient(app, raise_server_exceptions=False, headers=auth_headers) as c:
        yield c


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestHealthEndpoint:
    def test_health_endpoint(self, client):
        """GET /health returns 200 with status ok."""
        resp = client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data == {"status": "ok"}


class TestGateEndpoint:
    def test_gate_endpoint(self, auth_client):
        """GET /api/gate returns gate JSON with a checks array."""
        resp = auth_client.get("/api/gate")
        assert resp.status_code == 200
        data = resp.json()
        # Gate file always present in repo; must contain checks
        assert "checks" in data, f"Expected 'checks' key, got: {list(data.keys())}"
        assert isinstance(data["checks"], list)

    def test_gate_endpoint_requires_auth(self, client):
        """GET /api/gate returns 401 without credentials."""
        resp = client.get("/api/gate")
        assert resp.status_code == 401


class TestConfigEndpoint:
    def test_config_endpoint(self, auth_client):
        """GET /api/config returns config with strategies_enabled list."""
        resp = auth_client.get("/api/config")
        assert resp.status_code == 200
        data = resp.json()
        assert "strategies_enabled" in data, (
            f"Expected 'strategies_enabled' key, got: {list(data.keys())}"
        )
        assert isinstance(data["strategies_enabled"], list)
        assert len(data["strategies_enabled"]) > 0

    def test_config_endpoint_requires_auth(self, client):
        """GET /api/config returns 401 without credentials."""
        resp = client.get("/api/config")
        assert resp.status_code == 401


class TestSnapshotEndpoint:
    def test_snapshot_endpoint(self, auth_client):
        """GET /api/snapshot returns a stub or a real snapshot depending on env.

        When data/snapshot.json is absent the endpoint returns
        ``{"running": False}``; when an agent has written one (e.g. on a live
        deployment) it returns the full RuntimeSnapshot dict.
        """
        resp = auth_client.get("/api/snapshot")
        assert resp.status_code == 200
        data = resp.json()
        is_stub = data.get("running") is False
        is_real_snapshot = "equity" in data and "mode" in data
        assert is_stub or is_real_snapshot, f"Unexpected shape: {list(data.keys())}"

    def test_snapshot_endpoint_requires_auth(self, client):
        """GET /api/snapshot returns 401 without credentials."""
        resp = client.get("/api/snapshot")
        assert resp.status_code == 401


@pytest.mark.skipif(not _REACT_BUILD_EXISTS, reason="frontend/dist not built — run 'npm run build' in frontend/")
class TestSPARoutes:
    def test_spa_serves_html(self, client):
        """GET / returns HTML document starting with <!doctype."""
        resp = client.get("/")
        assert resp.status_code == 200
        text = resp.text.lower().lstrip()
        assert text.startswith("<!doctype"), (
            f"Expected HTML doctype, got first 60 chars: {resp.text[:60]!r}"
        )

    def test_spa_subroutes(self, client):
        """GET /live returns HTML — SPA routing is handled server-side."""
        resp = client.get("/live")
        assert resp.status_code == 200
        text = resp.text.lower().lstrip()
        assert text.startswith("<!doctype"), (
            f"Expected HTML for SPA subroute /live, got: {resp.text[:60]!r}"
        )

    def test_spa_settings_subroute(self, client):
        """GET /settings also returns SPA HTML."""
        resp = client.get("/settings")
        assert resp.status_code == 200
        assert "<!doctype" in resp.text.lower()


class TestApiNotHtml:
    def test_api_not_html(self, client):
        """GET /api/unknown returns 404 JSON, not an HTML error page."""
        resp = client.get("/api/unknown")
        assert resp.status_code == 404
        # Must be JSON
        data = resp.json()
        assert isinstance(data, dict)
        # Must not contain HTML
        assert "<html" not in resp.text.lower()
        assert "<!doctype" not in resp.text.lower()


class TestCostsEndpoint:
    def test_costs_endpoint(self, auth_client):
        """GET /api/costs returns a dict containing total_charges key."""
        resp = auth_client.get("/api/costs")
        assert resp.status_code == 200
        data = resp.json()
        assert "total_charges" in data, (
            f"Expected 'total_charges' key, got: {list(data.keys())}"
        )
        # Value should be numeric
        assert isinstance(data["total_charges"], (int, float))

    def test_costs_endpoint_requires_auth(self, client):
        """GET /api/costs returns 401 without credentials."""
        resp = client.get("/api/costs")
        assert resp.status_code == 401


class TestWebSocketEndpoint:
    def test_websocket_connects(self, app):
        """ws:// connection is accepted with correct token."""
        password = os.getenv("DASHBOARD_PASSWORD", "pypoc2024")
        with TestClient(app, raise_server_exceptions=False) as client:
            # Should connect successfully — no exception raised
            with client.websocket_connect(f"/ws/live?token={password}") as ws:
                # Connection was accepted; close cleanly
                ws.close()

    def test_websocket_rejected_without_token(self, app):
        """ws:// connection without token is rejected (close code 1008)."""
        with TestClient(app, raise_server_exceptions=False) as client:
            with pytest.raises(Exception):
                with client.websocket_connect("/ws/live") as ws:
                    ws.receive_text()


class TestCorsHeaders:
    def test_cors_headers_on_health(self, app):
        """API responses include CORS allow-origin header when Origin is sent."""
        with TestClient(app, raise_server_exceptions=False) as client:
            resp = client.get(
                "/health",
                headers={"Origin": "http://localhost:3000"},
            )
            assert resp.status_code == 200
            acao = resp.headers.get("access-control-allow-origin")
            assert acao is not None, (
                "Missing 'access-control-allow-origin' header — CORS middleware not active"
            )
            # Wildcard or explicit origin must be present
            assert acao in ("*", "http://localhost:3000")

    def test_cors_headers_on_api_endpoint(self, app):
        """CORS header present on a protected API response."""
        password = os.getenv("DASHBOARD_PASSWORD", "pypoc2024")
        token = base64.b64encode(f"admin:{password}".encode()).decode()
        with TestClient(app, raise_server_exceptions=False) as client:
            resp = client.get(
                "/api/gate",
                headers={
                    "Authorization": f"Basic {token}",
                    "Origin": "http://localhost:5173",
                },
            )
            assert resp.status_code == 200
            acao = resp.headers.get("access-control-allow-origin")
            assert acao is not None, "Missing CORS header on /api/gate"
