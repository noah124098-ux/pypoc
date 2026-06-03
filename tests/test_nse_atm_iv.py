"""Tests for core/data/nse_atm_iv.py — no real HTTP calls (all mocked)."""
from __future__ import annotations

import importlib
import time
from unittest.mock import MagicMock, patch


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_home_response() -> MagicMock:
    resp = MagicMock()
    resp.raise_for_status.return_value = None
    resp.status_code = 200
    return resp


def _make_oc_response(spot: float, strikes: list[dict]) -> MagicMock:
    """Return a mock requests.Response whose .json() returns a valid NSE OC payload."""
    resp = MagicMock()
    resp.raise_for_status.return_value = None
    resp.status_code = 200
    resp.json.return_value = {
        "records": {
            "underlyingValue": spot,
            "data": strikes,
        }
    }
    return resp


def _strike_entry(strike: float, ce_iv: float | None, pe_iv: float | None = None) -> dict:
    """Helper to build a single option chain entry."""
    entry: dict = {"strikePrice": strike}
    if ce_iv is not None:
        entry["CE"] = {"impliedVolatility": ce_iv, "openInterest": 100000}
    else:
        entry["CE"] = {}
    if pe_iv is not None:
        entry["PE"] = {"impliedVolatility": pe_iv, "openInterest": 80000}
    return entry


def _default_strikes(spot: float = 24500.0, atm_iv: float = 13.5) -> list[dict]:
    """Build a realistic-ish set of strikes bracketing the given spot."""
    base = round(spot / 50) * 50  # nearest 50-point strike
    return [
        _strike_entry(base - 100, atm_iv + 1.0),
        _strike_entry(base - 50, atm_iv + 0.5),
        _strike_entry(base, atm_iv),          # ATM strike
        _strike_entry(base + 50, atm_iv + 0.5),
        _strike_entry(base + 100, atm_iv + 1.0),
    ]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestNseAtmIvImport:
    def test_module_is_importable(self):
        import core.data.nse_atm_iv as m  # noqa: F401
        assert hasattr(m, "get_atm_iv")
        assert hasattr(m, "clear_cache")


class TestGetAtmIv:
    def setup_method(self):
        """Reset cache and rate-limiter state before each test."""
        import core.data.nse_atm_iv as m
        import core.data.nse_rate_limiter as rl
        m.clear_cache()
        rl.reset_for_testing()

    # ------------------------------------------------------------------
    # Test 1 — happy path: correct ATM strike is selected
    # ------------------------------------------------------------------
    def test_returns_atm_ce_iv_for_closest_strike(self):
        """Should return the CE IV for the strike closest to spot price."""
        import core.data.nse_atm_iv as m

        spot = 24500.0
        strikes = _default_strikes(spot=spot, atm_iv=13.5)
        # ATM strike is 24500 with IV=13.5; surrounding strikes have higher IVs.

        mock_requests = MagicMock()
        mock_session = MagicMock()
        mock_requests.Session.return_value = mock_session
        mock_session.get.side_effect = [
            _make_home_response(),
            _make_oc_response(spot, strikes),
        ]

        with patch.dict("sys.modules", {"requests": mock_requests}):
            importlib.reload(m)
            m.clear_cache()
            iv = m.get_atm_iv()

        assert isinstance(iv, float)
        assert abs(iv - 13.5) < 1e-6

    # ------------------------------------------------------------------
    # Test 2 — network failure returns None (fail-open)
    # ------------------------------------------------------------------
    def test_returns_none_on_network_error(self):
        """A connection error must return None rather than raise."""
        import core.data.nse_atm_iv as m

        mock_requests = MagicMock()
        mock_session = MagicMock()
        mock_requests.Session.return_value = mock_session
        mock_session.get.side_effect = ConnectionError("timeout")

        with patch.dict("sys.modules", {"requests": mock_requests}):
            importlib.reload(m)
            m.clear_cache()
            iv = m.get_atm_iv()

        assert iv is None

    # ------------------------------------------------------------------
    # Test 3 — missing CE impliedVolatility field returns None
    # ------------------------------------------------------------------
    def test_returns_none_when_ce_iv_missing(self):
        """If the ATM strike's CE has no impliedVolatility key, return None."""
        import core.data.nse_atm_iv as m

        spot = 24500.0
        strikes = [
            {"strikePrice": 24500, "CE": {}, "PE": {"impliedVolatility": 13.0}},
        ]

        mock_requests = MagicMock()
        mock_session = MagicMock()
        mock_requests.Session.return_value = mock_session
        mock_session.get.side_effect = [
            _make_home_response(),
            _make_oc_response(spot, strikes),
        ]

        with patch.dict("sys.modules", {"requests": mock_requests}):
            importlib.reload(m)
            m.clear_cache()
            iv = m.get_atm_iv()

        assert iv is None

    # ------------------------------------------------------------------
    # Test 4 — never raises (fail-open contract)
    # ------------------------------------------------------------------
    def test_never_raises(self):
        """get_atm_iv must never propagate an exception to the caller."""
        import core.data.nse_atm_iv as m

        mock_requests = MagicMock()
        mock_session = MagicMock()
        mock_requests.Session.return_value = mock_session
        mock_session.get.side_effect = RuntimeError("unexpected crash")

        with patch.dict("sys.modules", {"requests": mock_requests}):
            importlib.reload(m)
            m.clear_cache()
            try:
                iv = m.get_atm_iv()
                assert iv is None
            except Exception as exc:
                raise AssertionError(f"get_atm_iv raised unexpectedly: {exc}") from exc

    # ------------------------------------------------------------------
    # Test 5 — cache: second call within TTL returns cached value
    # ------------------------------------------------------------------
    def test_cache_returns_cached_value_within_ttl(self):
        """Second call within TTL must return the cached value without a new HTTP request."""
        import core.data.nse_atm_iv as m

        spot = 24500.0
        strikes = _default_strikes(spot=spot, atm_iv=14.0)

        mock_requests = MagicMock()
        mock_session = MagicMock()
        mock_requests.Session.return_value = mock_session
        mock_session.get.side_effect = [
            _make_home_response(),
            _make_oc_response(spot, strikes),
        ]

        with patch.dict("sys.modules", {"requests": mock_requests}):
            importlib.reload(m)
            m.clear_cache()
            t0 = time.monotonic()
            iv1 = m.get_atm_iv(_now=t0)
            iv2 = m.get_atm_iv(_now=t0 + 120)  # 2 minutes later — still in 5-min cache

        assert iv1 == iv2
        # Only 2 session.get calls for the first fetch (home + OC); none for second
        assert mock_session.get.call_count == 2
