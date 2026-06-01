"""Tests for core/data/nse_fii_dii.py — no real HTTP calls (all mocked)."""
from __future__ import annotations

import importlib
import time
from unittest.mock import MagicMock, patch


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_fii_dii_response(records: list[dict]) -> MagicMock:
    """Return a mock requests.Response whose .json() returns a valid NSE FII/DII payload."""
    resp = MagicMock()
    resp.raise_for_status.return_value = None
    resp.json.return_value = records
    return resp


def _sample_records(count: int = 5) -> list[dict]:
    """Return *count* sample FII/DII records with varying net values."""
    base = [
        {"date": "30-May-2025", "fiiNetDii": "1200.50", "diiNetDii": "-300.25"},
        {"date": "29-May-2025", "fiiNetDii": "800.00",  "diiNetDii": "150.00"},
        {"date": "28-May-2025", "fiiNetDii": "-200.75", "diiNetDii": "500.00"},
        {"date": "27-May-2025", "fiiNetDii": "650.00",  "diiNetDii": "-100.00"},
        {"date": "26-May-2025", "fiiNetDii": "-1500.00","diiNetDii": "700.00"},
    ]
    return base[:count]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestNseFiiDiiImport:
    def test_module_is_importable(self):
        import core.data.nse_fii_dii as m  # noqa: F401
        assert hasattr(m, "get_fii_dii_flows")
        assert hasattr(m, "get_institutional_sentiment")
        assert hasattr(m, "clear_cache")
        assert hasattr(m, "FIIDIIFlow")


class TestGetFiiDiiFlows:
    def setup_method(self):
        """Reset cache before each test."""
        import core.data.nse_fii_dii as m
        m.clear_cache()

    def test_returns_list_of_fiidii_flow_on_success(self):
        import core.data.nse_fii_dii as m

        mock_requests = MagicMock()
        mock_requests.get.return_value = _make_fii_dii_response(_sample_records(3))

        with patch.dict("sys.modules", {"requests": mock_requests}):
            importlib.reload(m)
            m.clear_cache()
            flows = m.get_fii_dii_flows(days=5)

        assert isinstance(flows, list)
        assert len(flows) == 3
        for flow in flows:
            assert isinstance(flow, m.FIIDIIFlow)
            assert isinstance(flow.date, str)
            assert isinstance(flow.fii_net_inr_cr, float)
            assert isinstance(flow.dii_net_inr_cr, float)
            assert isinstance(flow.combined_net, float)

    def test_combined_net_equals_fii_plus_dii(self):
        import core.data.nse_fii_dii as m

        records = [{"date": "30-May-2025", "fiiNetDii": "1000.0", "diiNetDii": "250.0"}]
        mock_requests = MagicMock()
        mock_requests.get.return_value = _make_fii_dii_response(records)

        with patch.dict("sys.modules", {"requests": mock_requests}):
            importlib.reload(m)
            m.clear_cache()
            flows = m.get_fii_dii_flows()

        assert len(flows) == 1
        assert abs(flows[0].fii_net_inr_cr - 1000.0) < 1e-9
        assert abs(flows[0].dii_net_inr_cr - 250.0) < 1e-9
        assert abs(flows[0].combined_net - 1250.0) < 1e-9

    def test_returns_empty_list_on_network_failure(self):
        import core.data.nse_fii_dii as m

        mock_requests = MagicMock()
        mock_requests.get.side_effect = ConnectionError("timeout")

        with patch.dict("sys.modules", {"requests": mock_requests}):
            importlib.reload(m)
            m.clear_cache()
            flows = m.get_fii_dii_flows()

        assert flows == []

    def test_returns_empty_list_on_http_error(self):
        import core.data.nse_fii_dii as m

        mock_requests = MagicMock()
        bad_resp = MagicMock()
        bad_resp.raise_for_status.side_effect = Exception("403 Forbidden")
        mock_requests.get.return_value = bad_resp

        with patch.dict("sys.modules", {"requests": mock_requests}):
            importlib.reload(m)
            m.clear_cache()
            flows = m.get_fii_dii_flows()

        assert flows == []

    def test_returns_empty_list_when_response_not_a_list(self):
        import core.data.nse_fii_dii as m

        mock_requests = MagicMock()
        bad_resp = MagicMock()
        bad_resp.raise_for_status.return_value = None
        bad_resp.json.return_value = {"error": "not a list"}
        mock_requests.get.return_value = bad_resp

        with patch.dict("sys.modules", {"requests": mock_requests}):
            importlib.reload(m)
            m.clear_cache()
            flows = m.get_fii_dii_flows()

        assert flows == []

    def test_skips_entries_with_missing_fields(self):
        import core.data.nse_fii_dii as m

        records = [
            {"date": "30-May-2025", "fiiNetDii": "1200.0", "diiNetDii": "300.0"},
            {"date": "29-May-2025"},                   # missing fiiNetDii and diiNetDii
            {"date": "28-May-2025", "fiiNetDii": None, "diiNetDii": "100.0"},  # null fii
        ]
        mock_requests = MagicMock()
        mock_requests.get.return_value = _make_fii_dii_response(records)

        with patch.dict("sys.modules", {"requests": mock_requests}):
            importlib.reload(m)
            m.clear_cache()
            flows = m.get_fii_dii_flows()

        # Only the first record is valid
        assert len(flows) == 1
        assert abs(flows[0].fii_net_inr_cr - 1200.0) < 1e-9

    def test_days_parameter_limits_results(self):
        import core.data.nse_fii_dii as m

        mock_requests = MagicMock()
        mock_requests.get.return_value = _make_fii_dii_response(_sample_records(5))

        with patch.dict("sys.modules", {"requests": mock_requests}):
            importlib.reload(m)
            m.clear_cache()
            flows = m.get_fii_dii_flows(days=2)

        assert len(flows) == 2

    def test_never_raises(self):
        """get_fii_dii_flows must never propagate an exception."""
        import core.data.nse_fii_dii as m

        mock_requests = MagicMock()
        mock_requests.get.side_effect = RuntimeError("unexpected crash")

        with patch.dict("sys.modules", {"requests": mock_requests}):
            importlib.reload(m)
            m.clear_cache()
            try:
                flows = m.get_fii_dii_flows()
                assert flows == []
            except Exception as exc:
                raise AssertionError(f"get_fii_dii_flows raised unexpectedly: {exc}") from exc

    def test_cache_returns_same_result_within_ttl(self):
        """Second call within TTL must not make a new HTTP request."""
        import core.data.nse_fii_dii as m

        mock_requests = MagicMock()
        mock_requests.get.return_value = _make_fii_dii_response(_sample_records(3))

        with patch.dict("sys.modules", {"requests": mock_requests}):
            importlib.reload(m)
            m.clear_cache()
            t0 = time.monotonic()
            flows1 = m.get_fii_dii_flows(_now=t0)
            flows2 = m.get_fii_dii_flows(_now=t0 + 5 * 60)  # 5 minutes — still in 30-min cache

        assert len(flows1) == len(flows2)
        # Only one HTTP call; second is served from cache
        assert mock_requests.get.call_count == 1

    def test_cache_refreshes_after_ttl(self):
        """After 30-minute TTL expires, a new HTTP request should be made."""
        import core.data.nse_fii_dii as m

        first_records = [{"date": "30-May-2025", "fiiNetDii": "1000.0", "diiNetDii": "200.0"}]
        second_records = [{"date": "30-May-2025", "fiiNetDii": "2000.0", "diiNetDii": "300.0"}]

        mock_requests = MagicMock()
        mock_requests.get.side_effect = [
            _make_fii_dii_response(first_records),
            _make_fii_dii_response(second_records),
        ]

        with patch.dict("sys.modules", {"requests": mock_requests}):
            importlib.reload(m)
            m.clear_cache()
            t0 = time.monotonic()
            flows1 = m.get_fii_dii_flows(_now=t0)
            flows2 = m.get_fii_dii_flows(_now=t0 + 31 * 60)  # 31 minutes — cache expired

        assert abs(flows1[0].fii_net_inr_cr - 1000.0) < 1e-9
        assert abs(flows2[0].fii_net_inr_cr - 2000.0) < 1e-9
        assert mock_requests.get.call_count == 2

    def test_stale_cache_used_on_fetch_failure(self):
        """When fetch fails after TTL, the last known cached value is returned."""
        import core.data.nse_fii_dii as m

        records = [{"date": "30-May-2025", "fiiNetDii": "800.0", "diiNetDii": "100.0"}]

        mock_requests = MagicMock()
        mock_requests.get.side_effect = [
            _make_fii_dii_response(records),  # first call succeeds
            ConnectionError("network down"),   # second call fails
        ]

        with patch.dict("sys.modules", {"requests": mock_requests}):
            importlib.reload(m)
            m.clear_cache()
            t0 = time.monotonic()
            flows1 = m.get_fii_dii_flows(_now=t0)
            flows2 = m.get_fii_dii_flows(_now=t0 + 35 * 60)  # TTL expired, but network is down

        assert len(flows1) == 1
        assert len(flows2) == 1  # stale cache, not []
        assert abs(flows2[0].fii_net_inr_cr - 800.0) < 1e-9


class TestGetInstitutionalSentiment:
    def setup_method(self):
        """Reset cache before each test."""
        import core.data.nse_fii_dii as m
        m.clear_cache()

    def test_returns_bullish_when_avg_fii_above_500cr(self):
        import core.data.nse_fii_dii as m

        # 3 days with avg FII well above 500 cr
        records = [
            {"date": "30-May-2025", "fiiNetDii": "1500.0", "diiNetDii": "200.0"},
            {"date": "29-May-2025", "fiiNetDii": "1200.0", "diiNetDii": "100.0"},
            {"date": "28-May-2025", "fiiNetDii": "800.0",  "diiNetDii": "-50.0"},
        ]
        mock_requests = MagicMock()
        mock_requests.get.return_value = _make_fii_dii_response(records)

        with patch.dict("sys.modules", {"requests": mock_requests}):
            importlib.reload(m)
            m.clear_cache()
            sentiment = m.get_institutional_sentiment()

        assert sentiment == "BULLISH"

    def test_returns_bearish_when_avg_fii_below_minus_500cr(self):
        import core.data.nse_fii_dii as m

        # 3 days with avg FII well below -500 cr
        records = [
            {"date": "30-May-2025", "fiiNetDii": "-1800.0", "diiNetDii": "400.0"},
            {"date": "29-May-2025", "fiiNetDii": "-900.0",  "diiNetDii": "200.0"},
            {"date": "28-May-2025", "fiiNetDii": "-600.0",  "diiNetDii": "100.0"},
        ]
        mock_requests = MagicMock()
        mock_requests.get.return_value = _make_fii_dii_response(records)

        with patch.dict("sys.modules", {"requests": mock_requests}):
            importlib.reload(m)
            m.clear_cache()
            sentiment = m.get_institutional_sentiment()

        assert sentiment == "BEARISH"

    def test_returns_none_when_avg_fii_neutral(self):
        import core.data.nse_fii_dii as m

        # avg FII = (300 + -100 + 200) / 3 = 133 cr — inside ±500 band
        records = [
            {"date": "30-May-2025", "fiiNetDii": "300.0",  "diiNetDii": "50.0"},
            {"date": "29-May-2025", "fiiNetDii": "-100.0", "diiNetDii": "30.0"},
            {"date": "28-May-2025", "fiiNetDii": "200.0",  "diiNetDii": "20.0"},
        ]
        mock_requests = MagicMock()
        mock_requests.get.return_value = _make_fii_dii_response(records)

        with patch.dict("sys.modules", {"requests": mock_requests}):
            importlib.reload(m)
            m.clear_cache()
            sentiment = m.get_institutional_sentiment()

        assert sentiment is None

    def test_returns_none_on_empty_flows(self):
        import core.data.nse_fii_dii as m

        mock_requests = MagicMock()
        mock_requests.get.side_effect = ConnectionError("network down")

        with patch.dict("sys.modules", {"requests": mock_requests}):
            importlib.reload(m)
            m.clear_cache()
            sentiment = m.get_institutional_sentiment()

        assert sentiment is None

    def test_sentiment_exactly_at_bullish_boundary(self):
        """avg FII == 500 should NOT be BULLISH (strictly >)."""
        import core.data.nse_fii_dii as m

        records = [
            {"date": "30-May-2025", "fiiNetDii": "500.0", "diiNetDii": "0.0"},
        ]
        mock_requests = MagicMock()
        mock_requests.get.return_value = _make_fii_dii_response(records)

        with patch.dict("sys.modules", {"requests": mock_requests}):
            importlib.reload(m)
            m.clear_cache()
            sentiment = m.get_institutional_sentiment()

        assert sentiment is None  # exactly at boundary = neutral

    def test_sentiment_exactly_at_bearish_boundary(self):
        """avg FII == -500 should NOT be BEARISH (strictly <)."""
        import core.data.nse_fii_dii as m

        records = [
            {"date": "30-May-2025", "fiiNetDii": "-500.0", "diiNetDii": "0.0"},
        ]
        mock_requests = MagicMock()
        mock_requests.get.return_value = _make_fii_dii_response(records)

        with patch.dict("sys.modules", {"requests": mock_requests}):
            importlib.reload(m)
            m.clear_cache()
            sentiment = m.get_institutional_sentiment()

        assert sentiment is None  # exactly at boundary = neutral

    def test_uses_only_last_3_days(self):
        """Sentiment is based on the 3 most-recent days, older data is ignored."""
        import core.data.nse_fii_dii as m

        # First 3 records are bullish; the 4th and 5th are very bearish
        # Since get_fii_dii_flows(days=3) is called, only first 3 matter
        records = [
            {"date": "30-May-2025", "fiiNetDii": "1000.0", "diiNetDii": "100.0"},
            {"date": "29-May-2025", "fiiNetDii": "1200.0", "diiNetDii": "200.0"},
            {"date": "28-May-2025", "fiiNetDii": "900.0",  "diiNetDii": "150.0"},
            {"date": "27-May-2025", "fiiNetDii": "-5000.0","diiNetDii": "100.0"},
            {"date": "26-May-2025", "fiiNetDii": "-5000.0","diiNetDii": "100.0"},
        ]
        mock_requests = MagicMock()
        mock_requests.get.return_value = _make_fii_dii_response(records)

        with patch.dict("sys.modules", {"requests": mock_requests}):
            importlib.reload(m)
            m.clear_cache()
            sentiment = m.get_institutional_sentiment()

        assert sentiment == "BULLISH"

    def test_never_raises(self):
        """get_institutional_sentiment must never propagate an exception."""
        import core.data.nse_fii_dii as m

        mock_requests = MagicMock()
        mock_requests.get.side_effect = RuntimeError("unexpected crash")

        with patch.dict("sys.modules", {"requests": mock_requests}):
            importlib.reload(m)
            m.clear_cache()
            try:
                sentiment = m.get_institutional_sentiment()
                assert sentiment is None
            except Exception as exc:
                raise AssertionError(
                    f"get_institutional_sentiment raised unexpectedly: {exc}"
                ) from exc


class TestClearCache:
    def test_clear_cache_resets_state(self):
        """After clear_cache(), the next call must make a fresh HTTP request."""
        import core.data.nse_fii_dii as m

        records = [{"date": "30-May-2025", "fiiNetDii": "500.1", "diiNetDii": "0.0"}]

        mock_requests = MagicMock()
        mock_requests.get.return_value = _make_fii_dii_response(records)

        with patch.dict("sys.modules", {"requests": mock_requests}):
            importlib.reload(m)
            m.clear_cache()
            t0 = time.monotonic()

            m.get_fii_dii_flows(_now=t0)          # populates cache
            m.clear_cache()                         # resets it
            m.get_fii_dii_flows(_now=t0 + 1)      # should fetch again

        assert mock_requests.get.call_count == 2
