"""Tests for core/data/nse_vix.py — no real HTTP calls (all mocked)."""
from __future__ import annotations

import importlib
import time
from unittest.mock import MagicMock, patch


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_vix_response(vix_value: float) -> MagicMock:
    """Return a mock requests.Response whose .json() returns a valid NSE allIndices payload."""
    resp = MagicMock()
    resp.raise_for_status.return_value = None
    resp.json.return_value = {
        "data": [
            {"index": "NIFTY 50", "last": 22500.0},
            {"index": "INDIA VIX", "last": vix_value},
            {"index": "NIFTY BANK", "last": 48000.0},
        ]
    }
    return resp


def _make_empty_response() -> MagicMock:
    """Return a mock response with no INDIA VIX entry."""
    resp = MagicMock()
    resp.raise_for_status.return_value = None
    resp.json.return_value = {"data": [{"index": "NIFTY 50", "last": 22500.0}]}
    return resp


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestNseVixImport:
    def test_module_is_importable(self):
        import core.data.nse_vix as m  # noqa: F401
        assert hasattr(m, "get_vix")
        assert hasattr(m, "clear_cache")


class TestGetVix:
    def setup_method(self):
        """Reset cache before each test."""
        import core.data.nse_vix as m
        m.clear_cache()

    def test_returns_float_on_success(self):
        import core.data.nse_vix as m

        mock_requests = MagicMock()
        mock_requests.get.return_value = _make_vix_response(14.75)

        with patch.dict("sys.modules", {"requests": mock_requests}):
            importlib.reload(m)
            m.clear_cache()
            vix = m.get_vix()

        assert isinstance(vix, float)
        assert abs(vix - 14.75) < 1e-9

    def test_returns_none_on_network_error(self):
        import core.data.nse_vix as m

        mock_requests = MagicMock()
        mock_requests.get.side_effect = ConnectionError("timeout")

        with patch.dict("sys.modules", {"requests": mock_requests}):
            importlib.reload(m)
            m.clear_cache()
            vix = m.get_vix()

        assert vix is None

    def test_returns_none_when_india_vix_not_in_response(self):
        import core.data.nse_vix as m

        mock_requests = MagicMock()
        mock_requests.get.return_value = _make_empty_response()

        with patch.dict("sys.modules", {"requests": mock_requests}):
            importlib.reload(m)
            m.clear_cache()
            vix = m.get_vix()

        assert vix is None

    def test_returns_none_on_missing_last_field(self):
        import core.data.nse_vix as m

        mock_requests = MagicMock()
        bad_resp = MagicMock()
        bad_resp.raise_for_status.return_value = None
        bad_resp.json.return_value = {
            "data": [{"index": "INDIA VIX"}]  # "last" key missing
        }
        mock_requests.get.return_value = bad_resp

        with patch.dict("sys.modules", {"requests": mock_requests}):
            importlib.reload(m)
            m.clear_cache()
            vix = m.get_vix()

        assert vix is None

    def test_never_raises(self):
        """get_vix must never propagate an exception."""
        import core.data.nse_vix as m

        mock_requests = MagicMock()
        mock_requests.get.side_effect = RuntimeError("unexpected crash")

        with patch.dict("sys.modules", {"requests": mock_requests}):
            importlib.reload(m)
            m.clear_cache()
            try:
                vix = m.get_vix()
                assert vix is None
            except Exception as exc:
                raise AssertionError(f"get_vix raised unexpectedly: {exc}") from exc

    def test_cache_returns_stale_value_within_ttl(self):
        """Second call within TTL must return the cached value without a new HTTP request."""
        import core.data.nse_vix as m

        mock_requests = MagicMock()
        mock_requests.get.return_value = _make_vix_response(16.5)

        with patch.dict("sys.modules", {"requests": mock_requests}):
            importlib.reload(m)
            m.clear_cache()
            t0 = time.monotonic()
            vix1 = m.get_vix(_now=t0)
            vix2 = m.get_vix(_now=t0 + 120)  # 2 minutes later — still in 5-min cache

        assert vix1 == vix2
        # Only one HTTP call for the first fetch; none for the second
        assert mock_requests.get.call_count == 1

    def test_cache_refreshes_after_ttl(self):
        """After TTL expires the module should make a new HTTP request."""
        import core.data.nse_vix as m

        mock_requests = MagicMock()
        mock_requests.get.side_effect = [
            _make_vix_response(13.0),
            _make_vix_response(18.0),
        ]

        with patch.dict("sys.modules", {"requests": mock_requests}):
            importlib.reload(m)
            m.clear_cache()
            t0 = time.monotonic()
            vix1 = m.get_vix(_now=t0)
            vix2 = m.get_vix(_now=t0 + 6 * 60)  # 6 minutes later — cache expired

        assert abs(vix1 - 13.0) < 1e-9
        assert abs(vix2 - 18.0) < 1e-9
        assert mock_requests.get.call_count == 2

    def test_stale_cache_used_on_fetch_failure(self):
        """When fetch fails, the last known cached value is returned rather than None."""
        import core.data.nse_vix as m

        mock_requests = MagicMock()
        mock_requests.get.side_effect = [
            _make_vix_response(12.5),      # first call succeeds
            ConnectionError("down"),        # second call fails
        ]

        with patch.dict("sys.modules", {"requests": mock_requests}):
            importlib.reload(m)
            m.clear_cache()
            t0 = time.monotonic()
            vix1 = m.get_vix(_now=t0)
            # Cache expired, but network is down — should return stale value
            vix2 = m.get_vix(_now=t0 + 10 * 60)

        assert abs(vix1 - 12.5) < 1e-9
        assert abs(vix2 - 12.5) < 1e-9  # stale cache, not None
