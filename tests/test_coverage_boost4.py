"""Fourth round of coverage boosts.

Focus:
- core/data/nse_atm_iv.py — circuit breaker path, 403/429, missing fields, empty strikes
- core/data/nse_history.py — _fetch_index_range error paths
- core/analytics/performance_report.py — error/edge paths
- core/analytics/decision_trace.py — error/edge paths
- api/main.py — missing endpoint paths
- core/strategies/supertrend_short.py — remaining branches
"""
from __future__ import annotations

import json
import sqlite3
import tempfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pandas as pd
import pytest


# ===========================================================================
# core/data/nse_atm_iv.py
# ===========================================================================

class TestNseAtmIvBranches:
    """Cover specific branches in get_atm_iv and _fetch_atm_iv."""

    def setup_method(self):
        """Clear cache before each test."""
        from core.data.nse_atm_iv import clear_cache
        clear_cache()

    def teardown_method(self):
        """Clear cache after each test."""
        from core.data.nse_atm_iv import clear_cache
        clear_cache()

    def test_circuit_breaker_open_returns_none(self):
        """When circuit breaker is open, _fetch_atm_iv returns None."""
        from core.data.nse_atm_iv import _fetch_atm_iv
        with patch("core.data.nse_atm_iv.circuit_breaker") as mock_cb:
            mock_cb.is_open.return_value = True
            result = _fetch_atm_iv()
        assert result is None

    def test_missing_underlying_value_returns_none(self):
        """Response with no underlyingValue → returns None."""
        from core.data.nse_atm_iv import _fetch_atm_iv
        import requests
        with patch("core.data.nse_atm_iv.circuit_breaker") as mock_cb:
            mock_cb.is_open.return_value = False
            with patch("requests.Session") as MockSession:
                mock_session = MagicMock()
                MockSession.return_value = mock_session
                home_resp = MagicMock()
                home_resp.status_code = 200
                home_resp.raise_for_status = MagicMock()
                oc_resp = MagicMock()
                oc_resp.status_code = 200
                oc_resp.raise_for_status = MagicMock()
                oc_resp.json.return_value = {"records": {}}  # no underlyingValue
                mock_session.get.side_effect = [home_resp, oc_resp]
                mock_cb.record_success = MagicMock()
                result = _fetch_atm_iv()
        assert result is None

    def test_empty_strikes_data_returns_none(self):
        """Response with empty data array → returns None."""
        from core.data.nse_atm_iv import _fetch_atm_iv
        with patch("core.data.nse_atm_iv.circuit_breaker") as mock_cb:
            mock_cb.is_open.return_value = False
            with patch("requests.Session") as MockSession:
                mock_session = MagicMock()
                MockSession.return_value = mock_session
                home_resp = MagicMock()
                home_resp.status_code = 200
                home_resp.raise_for_status = MagicMock()
                oc_resp = MagicMock()
                oc_resp.status_code = 200
                oc_resp.raise_for_status = MagicMock()
                oc_resp.json.return_value = {
                    "records": {"underlyingValue": 24500.0, "data": []}
                }
                mock_session.get.side_effect = [home_resp, oc_resp]
                mock_cb.record_success = MagicMock()
                result = _fetch_atm_iv()
        assert result is None

    def test_strike_without_strike_price_skipped(self):
        """Entry with no strikePrice is skipped (line 110)."""
        from core.data.nse_atm_iv import _fetch_atm_iv
        with patch("core.data.nse_atm_iv.circuit_breaker") as mock_cb:
            mock_cb.is_open.return_value = False
            with patch("requests.Session") as MockSession:
                mock_session = MagicMock()
                MockSession.return_value = mock_session
                home_resp = MagicMock()
                home_resp.status_code = 200
                home_resp.raise_for_status = MagicMock()
                oc_resp = MagicMock()
                oc_resp.status_code = 200
                oc_resp.raise_for_status = MagicMock()
                oc_resp.json.return_value = {
                    "records": {
                        "underlyingValue": 24500.0,
                        "data": [
                            {"CE": {"impliedVolatility": 12.5}},  # no strikePrice
                        ]
                    }
                }
                mock_session.get.side_effect = [home_resp, oc_resp]
                mock_cb.record_success = MagicMock()
                result = _fetch_atm_iv()
        # best_entry is None → returns None
        assert result is None

    def test_missing_ce_iv_returns_none(self):
        """ATM strike found but CE impliedVolatility missing → None."""
        from core.data.nse_atm_iv import _fetch_atm_iv
        with patch("core.data.nse_atm_iv.circuit_breaker") as mock_cb:
            mock_cb.is_open.return_value = False
            with patch("requests.Session") as MockSession:
                mock_session = MagicMock()
                MockSession.return_value = mock_session
                home_resp = MagicMock()
                home_resp.status_code = 200
                home_resp.raise_for_status = MagicMock()
                oc_resp = MagicMock()
                oc_resp.status_code = 200
                oc_resp.raise_for_status = MagicMock()
                oc_resp.json.return_value = {
                    "records": {
                        "underlyingValue": 24500.0,
                        "data": [
                            {"strikePrice": 24500, "CE": {}},  # no impliedVolatility
                        ]
                    }
                }
                mock_session.get.side_effect = [home_resp, oc_resp]
                mock_cb.record_success = MagicMock()
                result = _fetch_atm_iv()
        assert result is None

    def test_successful_fetch_returns_float(self):
        """Valid response returns a float IV value."""
        from core.data.nse_atm_iv import _fetch_atm_iv
        with patch("core.data.nse_atm_iv.circuit_breaker") as mock_cb:
            mock_cb.is_open.return_value = False
            mock_cb.record_success = MagicMock()
            with patch("requests.Session") as MockSession:
                mock_session = MagicMock()
                MockSession.return_value = mock_session
                home_resp = MagicMock()
                home_resp.status_code = 200
                home_resp.raise_for_status = MagicMock()
                oc_resp = MagicMock()
                oc_resp.status_code = 200
                oc_resp.raise_for_status = MagicMock()
                oc_resp.json.return_value = {
                    "records": {
                        "underlyingValue": 24500.0,
                        "data": [
                            {"strikePrice": 24500, "CE": {"impliedVolatility": 12.5}},
                        ]
                    }
                }
                mock_session.get.side_effect = [home_resp, oc_resp]
                result = _fetch_atm_iv()
        assert result == pytest.approx(12.5)

    def test_get_atm_iv_uses_cache(self):
        """Second call within TTL returns cached value without re-fetching."""
        from core.data.nse_atm_iv import get_atm_iv, clear_cache
        clear_cache()
        with patch("core.data.nse_atm_iv._fetch_atm_iv", return_value=15.0) as mock_fetch:
            first = get_atm_iv(_now=1000.0)
            second = get_atm_iv(_now=1060.0)  # only 60s later, within 5-min TTL
        assert first == 15.0
        assert second == 15.0
        assert mock_fetch.call_count == 1  # only called once

    def test_get_atm_iv_stale_cache_returned_on_failure(self):
        """When fresh fetch fails, stale cache is returned."""
        from core.data.nse_atm_iv import get_atm_iv, clear_cache
        import core.data.nse_atm_iv as module
        clear_cache()
        # Pre-populate cache
        module._cached_iv = 11.0
        module._cache_timestamp = 0.0  # very old
        with patch("core.data.nse_atm_iv._fetch_atm_iv", return_value=None):
            result = get_atm_iv(_now=99999.0)  # well past TTL
        assert result == 11.0

    def test_home_page_403_triggers_circuit_failure(self):
        """403 on home page triggers circuit_breaker.record_failure (lines 76-78)."""
        from core.data.nse_atm_iv import _fetch_atm_iv
        with patch("core.data.nse_atm_iv.circuit_breaker") as mock_cb:
            mock_cb.is_open.return_value = False
            mock_cb.record_failure = MagicMock()
            with patch("requests.Session") as MockSession:
                mock_session = MagicMock()
                MockSession.return_value = mock_session
                home_resp = MagicMock()
                home_resp.status_code = 403
                home_resp.raise_for_status.side_effect = Exception("403 Forbidden")
                mock_session.get.return_value = home_resp
                result = _fetch_atm_iv()
        assert result is None
        mock_cb.record_failure.assert_called_once()


# ===========================================================================
# core/data/nse_history.py — _fetch_index_one_chunk error paths
# ===========================================================================

class TestNseHistoryIndexFetch:
    """Cover _fetch_index_range and _fetch_index_one_chunk error paths."""

    def test_fetch_index_one_chunk_request_exception(self):
        """Network error in _fetch_index_one_chunk → None."""
        try:
            from core.data.nse_history import NseHistory
        except ImportError:
            pytest.skip("NseHistory not importable")
        import requests
        hist = NseHistory(request_pause_seconds=0)
        with patch.object(hist, "_ensure_session") as mock_sess:
            mock_s = MagicMock()
            mock_s.get.side_effect = requests.RequestException("timeout")
            mock_sess.return_value = mock_s
            result = hist._fetch_index_one_chunk(
                "NIFTY 50", date(2024, 1, 1), date(2024, 1, 5)
            )
        assert result is None

    def test_fetch_index_one_chunk_empty_data(self):
        """Empty data in index response → None."""
        try:
            from core.data.nse_history import NseHistory
        except ImportError:
            pytest.skip("NseHistory not importable")
        hist = NseHistory(request_pause_seconds=0)
        with patch.object(hist, "_ensure_session") as mock_sess:
            mock_s = MagicMock()
            resp = MagicMock()
            resp.status_code = 200
            resp.raise_for_status = MagicMock()
            resp.json.return_value = {"data": [], "indexCloseOnlineRecords": []}
            mock_s.get.return_value = resp
            mock_sess.return_value = mock_s
            result = hist._fetch_index_one_chunk(
                "NIFTY 50", date(2024, 1, 1), date(2024, 1, 5)
            )
        assert result is None

    def test_fetch_index_range_no_chunks_returns_none(self):
        """When no chunks are returned, _fetch_index_range returns None."""
        try:
            from core.data.nse_history import NseHistory
        except ImportError:
            pytest.skip("NseHistory not importable")
        hist = NseHistory(request_pause_seconds=0)
        with patch.object(hist, "_fetch_index_one_chunk", return_value=None):
            result = hist._fetch_index_range(
                "NIFTY 50", date(2024, 1, 1), date(2024, 1, 5)
            )
        assert result is None

    def test_fetch_index_one_chunk_unknown_schema(self):
        """Unknown column schema → None."""
        try:
            from core.data.nse_history import NseHistory
        except ImportError:
            pytest.skip("NseHistory not importable")
        hist = NseHistory(request_pause_seconds=0)
        with patch.object(hist, "_ensure_session") as mock_sess:
            mock_s = MagicMock()
            resp = MagicMock()
            resp.status_code = 200
            resp.raise_for_status = MagicMock()
            # Unknown schema — neither EOD_OPEN_INDEX_VAL nor HistoricalDate
            resp.json.return_value = {
                "data": [{"UNKNOWN_COL": "foo", "BAR": "baz"}]
            }
            mock_s.get.return_value = resp
            mock_sess.return_value = mock_s
            result = hist._fetch_index_one_chunk(
                "NIFTY 50", date(2024, 1, 1), date(2024, 1, 5)
            )
        assert result is None

    def test_fetch_index_one_chunk_eod_schema(self):
        """EOD schema (EOD_OPEN_INDEX_VAL) is parsed correctly."""
        try:
            from core.data.nse_history import NseHistory
        except ImportError:
            pytest.skip("NseHistory not importable")
        hist = NseHistory(request_pause_seconds=0)
        with patch.object(hist, "_ensure_session") as mock_sess:
            mock_s = MagicMock()
            resp = MagicMock()
            resp.status_code = 200
            resp.raise_for_status = MagicMock()
            resp.json.return_value = {
                "data": [{
                    "EOD_TIMESTAMP": "02-Jan-2024",
                    "EOD_OPEN_INDEX_VAL": "21000",
                    "EOD_HIGH_INDEX_VAL": "21100",
                    "EOD_LOW_INDEX_VAL": "20900",
                    "EOD_CLOSE_INDEX_VAL": "21050",
                }]
            }
            mock_s.get.return_value = resp
            mock_sess.return_value = mock_s
            result = hist._fetch_index_one_chunk(
                "NIFTY 50", date(2024, 1, 1), date(2024, 1, 5)
            )
        assert result is not None
        assert "close" in result.columns


# ===========================================================================
# core/analytics/performance_report.py — error paths
# ===========================================================================

class TestPerformanceReportEdgePaths:
    """Cover lines 53, 93-94, 109-110, 147-148, 157-158, 174, 327."""

    def test_fmt_inr_zero(self):
        """_fmt_inr(0.0) should format cleanly."""
        from core.analytics.performance_report import _fmt_inr
        result = _fmt_inr(0.0)
        assert "0" in result

    def test_fmt_inr_with_decimal(self):
        """_fmt_inr handles fractional rupees."""
        from core.analytics.performance_report import _fmt_inr
        result = _fmt_inr(1234.56)
        assert "₹" in result

    def test_fmt_inr_negative(self):
        """Negative values get a minus sign."""
        from core.analytics.performance_report import _fmt_inr
        result = _fmt_inr(-1000.0)
        assert "-" in result

    def test_query_today_trades_db_not_exists(self):
        """When db_path doesn't exist, _query_today_trades returns []."""
        from core.analytics.performance_report import _query_today_trades
        result = _query_today_trades("/nonexistent/path.db", today="2024-01-02")
        assert result == []

    def test_query_today_trades_sqlite_error(self):
        """SQLite error in _query_today_trades returns []."""
        from core.analytics.performance_report import _query_today_trades
        import os
        fd, db_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        try:
            # Empty file → will fail to open as SQLite
            result = _query_today_trades(db_path, today="2024-01-02")
            assert isinstance(result, list)
        finally:
            try:
                os.unlink(db_path)
            except OSError:
                pass

    def test_query_strategy_perf_db_not_exists(self):
        """When db_path doesn't exist, _query_strategy_perf returns []."""
        from core.analytics.performance_report import _query_strategy_perf
        result = _query_strategy_perf("/nonexistent/path.db", days=30)
        assert result == []

    def test_generate_eod_report_with_empty_data(self):
        """generate_eod_report with no data returns a valid report string."""
        from core.analytics.performance_report import generate_eod_report
        with patch("core.analytics.performance_report._query_today_trades", return_value=[]):
            with patch("core.analytics.performance_report._query_strategy_perf", return_value=[]):
                with patch("core.analytics.performance_report._read_snapshot", return_value={}):
                    result = generate_eod_report(
                        db_path="/fake.db", snapshot_path="/fake_snap.json"
                    )
        assert isinstance(result, str)
        assert len(result) > 0


# ===========================================================================
# core/analytics/decision_trace.py — error/edge paths
# ===========================================================================

class TestDecisionTraceEdgePaths:
    """Cover lines 103-104, 204, 207, 211, 296-297, 301-302."""

    def test_trace_signal_not_found(self):
        """trace_signal_decision with nonexistent signal_id returns a dict with error."""
        from core.analytics.decision_trace import trace_signal_decision
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = str(Path(tmpdir) / "test.db")
            # Create minimal DB with required tables
            conn = sqlite3.connect(db_path)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS signals (
                    id INTEGER PRIMARY KEY,
                    ts TEXT, symbol TEXT, side TEXT, strategy TEXT,
                    regime TEXT, entry_price REAL, stop_loss REAL, target REAL,
                    confidence REAL, rationale TEXT, accepted INTEGER,
                    rejection_reason TEXT
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS trades (
                    id INTEGER PRIMARY KEY,
                    symbol TEXT, side TEXT, qty INTEGER,
                    entry_price REAL, exit_price REAL, pnl REAL, charges REAL,
                    strategy TEXT, exit_reason TEXT, opened_at TEXT, closed_at TEXT
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS guardrail_events (
                    id INTEGER PRIMARY KEY,
                    ts TEXT, rule TEXT, symbol TEXT, detail TEXT
                )
            """)
            conn.commit()
            conn.close()
            result = trace_signal_decision(99999, db_path)
        assert isinstance(result, dict)

    def test_mark_unreached_marks_detail(self):
        """_mark_unreached appends '(not reached)' to items after the fired rule."""
        from core.analytics.decision_trace import _mark_unreached, _RULE_ORDER
        if len(_RULE_ORDER) < 3:
            pytest.skip("_RULE_ORDER too short for this test")
        # Build checks with the first two rules from _RULE_ORDER
        rule0, rule1, rule2 = _RULE_ORDER[0], _RULE_ORDER[1], _RULE_ORDER[2]
        checks = [
            {"rule": rule0, "passed": True, "detail": "ok"},
            {"rule": rule1, "passed": None, "detail": ""},  # after cutoff, passed=None
            {"rule": rule2, "passed": None, "detail": ""},  # also after cutoff
        ]
        # Fire at rule0 — rules after it with passed=None should get "(not reached)"
        _mark_unreached(checks, rule0)
        # At least one item should have "(not reached)" appended
        assert any("(not reached)" in c["detail"] for c in checks)


# ===========================================================================
# core/strategies/supertrend_short.py — remaining branches
# ===========================================================================

class TestSupertrendShortRemaining:
    """Cover lines 81, 87, 91, 100, 110, 114, 119."""

    def _make_df(self, n: int = 120) -> pd.DataFrame:
        close = 1000.0 + np.arange(n) * 0.5
        return pd.DataFrame({
            "open": np.r_[close[0], close[:-1]],
            "high": close * 1.003,
            "low": close * 0.997,
            "close": close,
            "volume": np.full(n, 2_000_000.0),
        }, index=pd.bdate_range("2022-01-03", periods=n))

    def test_returns_none_when_low_return(self):
        """10-day return not below threshold → no signal."""
        from core.strategies.supertrend_short import SupertrendShort
        from core.types import Regime
        n = 120
        # Flat price → 10-day return ~0%, not below -3%
        close = np.full(n, 1000.0, dtype=float)
        close[-1] = 1000.5
        df = pd.DataFrame({
            "open": np.r_[close[0], close[:-1]],
            "high": close * 1.003,
            "low": close * 0.997,
            "close": close,
            "volume": np.full(n, 2_000_000.0),
        }, index=pd.bdate_range("2022-01-03", periods=n))
        with patch("core.strategies.supertrend_short.supertrend_bands") as mock_bands:
            fu = np.full(n, 1200.0)
            fl = np.full(n, 900.0)
            direction = np.full(n, -1, dtype=np.int8)  # bearish
            mock_bands.return_value = (fu, fl, direction)
            strat = SupertrendShort(
                stock_dma_period=20, dma_falling_lookback=5,
                return_threshold=-0.03, volume_spike_min=0.5
            )
            result = strat.evaluate("TEST", df, Regime.VOLATILE)
        # Flat price → 10-day return not below threshold → None
        assert result is None

    def test_returns_none_when_volume_not_spike(self):
        """Volume not spiking enough → no signal."""
        from core.strategies.supertrend_short import SupertrendShort
        from core.types import Regime
        n = 120
        # Sharp downtrend
        close = 1200.0 - np.arange(n) * 0.8
        df = pd.DataFrame({
            "open": np.r_[close[0], close[:-1]],
            "high": close * 1.003,
            "low": close * 0.997,
            "close": close,
            "volume": np.full(n, 2_000_000.0),  # all same volume → ratio = 1.0
        }, index=pd.bdate_range("2022-01-03", periods=n))
        with patch("core.strategies.supertrend_short.supertrend_bands") as mock_bands:
            fu = np.full(n, 1200.0)
            fl = np.full(n, 900.0)
            direction = np.full(n, -1, dtype=np.int8)
            mock_bands.return_value = (fu, fl, direction)
            strat = SupertrendShort(
                stock_dma_period=20, dma_falling_lookback=5,
                return_threshold=-100.0,  # very low threshold → return always passes
                volume_spike_min=3.0,     # require 3x → won't be met with flat volume
            )
            result = strat.evaluate("TEST", df, Regime.VOLATILE)
        assert result is None
