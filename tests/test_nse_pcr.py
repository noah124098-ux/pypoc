"""Tests for core/data/nse_pcr.py — no real HTTP calls (all mocked)."""
from __future__ import annotations

import importlib
import time
from typing import Any
from unittest.mock import MagicMock, patch


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_oc_response(pe_oi: int, ce_oi: int) -> MagicMock:
    """Return a mock requests.Response whose .json() returns a valid NSE OC payload."""
    resp = MagicMock()
    resp.raise_for_status.return_value = None
    resp.json.return_value = {
        "filtered": {
            "PE": {"totOI": pe_oi},
            "CE": {"totOI": ce_oi},
        }
    }
    return resp


def _make_home_response() -> MagicMock:
    resp = MagicMock()
    resp.raise_for_status.return_value = None
    return resp


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestNsePcrImport:
    def test_module_is_importable(self):
        import core.data.nse_pcr as m  # noqa: F401
        assert hasattr(m, "get_nifty_pcr")
        assert hasattr(m, "clear_cache")


class TestGetNiftyPcr:
    def setup_method(self):
        """Reset cache before each test."""
        import core.data.nse_pcr as m
        m.clear_cache()

    def _patch_session(self, home_resp: Any, oc_resp: Any):
        """Context manager that patches requests.Session used inside _fetch_pcr."""
        mock_session = MagicMock()
        mock_session.__enter__ = MagicMock(return_value=mock_session)
        mock_session.__exit__ = MagicMock(return_value=False)
        mock_session.get.side_effect = [home_resp, oc_resp]
        mock_session.headers = MagicMock()
        mock_session.headers.update = MagicMock()

        mock_session_cls = MagicMock(return_value=mock_session)
        return patch("core.data.nse_pcr.requests.Session", mock_session_cls), mock_session

    def test_returns_float_on_success(self):
        import core.data.nse_pcr as m

        mock_requests = MagicMock()
        mock_session = MagicMock()
        mock_requests.Session.return_value = mock_session
        mock_session.get.side_effect = [
            _make_home_response(),
            _make_oc_response(pe_oi=1_500_000, ce_oi=1_000_000),
        ]

        with patch.dict("sys.modules", {"requests": mock_requests}):
            # Reload to pick up patched requests
            importlib.reload(m)
            m.clear_cache()
            pcr = m.get_nifty_pcr()

        assert isinstance(pcr, float)
        assert abs(pcr - 1.5) < 1e-9

    def test_returns_none_on_network_error(self):
        import core.data.nse_pcr as m

        mock_requests = MagicMock()
        mock_session = MagicMock()
        mock_requests.Session.return_value = mock_session
        mock_session.get.side_effect = ConnectionError("timeout")

        with patch.dict("sys.modules", {"requests": mock_requests}):
            importlib.reload(m)
            m.clear_cache()
            pcr = m.get_nifty_pcr()

        assert pcr is None

    def test_returns_none_on_missing_oi_fields(self):
        import core.data.nse_pcr as m

        mock_requests = MagicMock()
        mock_session = MagicMock()
        mock_requests.Session.return_value = mock_session
        bad_resp = MagicMock()
        bad_resp.raise_for_status.return_value = None
        bad_resp.json.return_value = {"filtered": {}}  # missing PE/CE
        mock_session.get.side_effect = [_make_home_response(), bad_resp]

        with patch.dict("sys.modules", {"requests": mock_requests}):
            importlib.reload(m)
            m.clear_cache()
            pcr = m.get_nifty_pcr()

        assert pcr is None

    def test_never_raises(self):
        """get_nifty_pcr must never propagate an exception."""
        import core.data.nse_pcr as m

        mock_requests = MagicMock()
        mock_session = MagicMock()
        mock_requests.Session.return_value = mock_session
        mock_session.get.side_effect = RuntimeError("unexpected crash")

        with patch.dict("sys.modules", {"requests": mock_requests}):
            importlib.reload(m)
            m.clear_cache()
            try:
                pcr = m.get_nifty_pcr()
                assert pcr is None
            except Exception as exc:
                raise AssertionError(f"get_nifty_pcr raised unexpectedly: {exc}") from exc

    def test_cache_returns_stale_value_within_ttl(self):
        """Second call within TTL must return the cached value without a new HTTP request."""
        import core.data.nse_pcr as m

        mock_requests = MagicMock()
        mock_session = MagicMock()
        mock_requests.Session.return_value = mock_session
        mock_session.get.side_effect = [
            _make_home_response(),
            _make_oc_response(pe_oi=1_200_000, ce_oi=1_000_000),
        ]

        with patch.dict("sys.modules", {"requests": mock_requests}):
            importlib.reload(m)
            m.clear_cache()
            t0 = time.monotonic()
            pcr1 = m.get_nifty_pcr(_now=t0)
            pcr2 = m.get_nifty_pcr(_now=t0 + 60)  # 1 minute later — still in cache

        assert pcr1 == pcr2
        # Only 2 session.get calls for the first fetch; none for the second
        assert mock_session.get.call_count == 2

    def test_cache_refreshes_after_ttl(self):
        """After TTL expires the module should make a new HTTP request."""
        import core.data.nse_pcr as m

        mock_requests = MagicMock()
        mock_session = MagicMock()
        mock_requests.Session.return_value = mock_session
        mock_session.get.side_effect = [
            _make_home_response(),
            _make_oc_response(pe_oi=800_000, ce_oi=1_000_000),
            _make_home_response(),
            _make_oc_response(pe_oi=900_000, ce_oi=1_000_000),
        ]

        with patch.dict("sys.modules", {"requests": mock_requests}):
            importlib.reload(m)
            m.clear_cache()
            t0 = time.monotonic()
            pcr1 = m.get_nifty_pcr(_now=t0)
            pcr2 = m.get_nifty_pcr(_now=t0 + 16 * 60)  # 16 minutes later — cache expired

        assert abs(pcr1 - 0.8) < 1e-9
        assert abs(pcr2 - 0.9) < 1e-9
        assert mock_session.get.call_count == 4

    def test_pcr_below_threshold_is_bearish(self):
        """Verify PCR < 0.7 can be detected by callers."""
        import core.data.nse_pcr as m

        mock_requests = MagicMock()
        mock_session = MagicMock()
        mock_requests.Session.return_value = mock_session
        mock_session.get.side_effect = [
            _make_home_response(),
            _make_oc_response(pe_oi=600_000, ce_oi=1_000_000),
        ]

        with patch.dict("sys.modules", {"requests": mock_requests}):
            importlib.reload(m)
            m.clear_cache()
            pcr = m.get_nifty_pcr()

        assert pcr is not None
        assert pcr < 0.7  # bearish signal
