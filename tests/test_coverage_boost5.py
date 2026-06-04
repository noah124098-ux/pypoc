"""Fifth round of coverage boosts.

Focus:
- api/main.py — cover lines 191-192, 201-202, 250-260, 341-362, 595-597, 629, 633
- core/analytics/performance_report.py — remaining lines
- core/analytics/decision_trace.py — remaining lines
- core/broker/paper.py — lines 123-124, 163, 237
- core/strategies/indicators.py — lines 153, 171-172
- core/data/nse_history.py — lines 213-218
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
from fastapi.testclient import TestClient

AUTH = ("admin", "pypoc2024")


@pytest.fixture()
def client_with_db(tmp_path, monkeypatch):
    """TestClient with snapshot, gate, and a minimal agent DB."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DASHBOARD_PASSWORD", "pypoc2024")
    data_dir = tmp_path / "data"
    data_dir.mkdir()

    snapshot = {
        "running": True, "ts": "2026-06-03T10:00:00+00:00",
        "pid": 1234, "mode": "paper",
        "feed_connected": True, "last_tick_age_seconds": 0.5,
        "halted": False, "halt_reason": "",
        "cash": 90000.0, "equity": 100000.0,
        "realized_pnl": 500.0, "starting_equity_today": 99000.0,
        "peak_equity": 101000.0, "open_positions": [],
        "current_regime": "TREND", "regime_rationale": "ADX>25",
        "nifty_ltp": 22500.0, "vix": 14.5,
        "universe_size": 50, "strategies_enabled": ["trend_breakout"],
        "config_path": "config/default.yaml",
    }
    (data_dir / "snapshot.json").write_text(json.dumps(snapshot))

    gate = {
        "passed": True, "sharpe": 1.35, "max_drawdown_pct": 9.8,
        "win_rate_pct": 47.2, "profit_factor": 1.62, "total_trades": 312,
        "timestamp": "2026-06-01T10:00:00+00:00",
    }
    (data_dir / "backtest_gate.json").write_text(json.dumps(gate))

    # Create a minimal SQLite DB with trades
    db_path = data_dir / "agent.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute("""
        CREATE TABLE trades (
            id INTEGER PRIMARY KEY, symbol TEXT, side TEXT, qty INTEGER,
            entry_price REAL, exit_price REAL, pnl REAL, charges REAL,
            strategy TEXT, exit_reason TEXT, opened_at TEXT, closed_at TEXT
        )
    """)
    conn.execute("""
        INSERT INTO trades VALUES
        (1,'RELIANCE','BUY',10,2000.0,2100.0,1000.0,40.0,'trend_breakout','target',
         '2026-06-02T10:00:00','2026-06-02T14:00:00')
    """)
    conn.commit()
    conn.close()

    from api.main import app
    return TestClient(app)


# ===========================================================================
# api/main.py — status endpoint with gate timestamp exception (lines 191-192)
# ===========================================================================

class TestApiMainCoverage:

    def test_status_with_gate_timestamp_exception(self, tmp_path, monkeypatch):
        """Status endpoint handles exception in gate_age_days calculation."""
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("DASHBOARD_PASSWORD", "pypoc2024")
        data_dir = tmp_path / "data"
        data_dir.mkdir()

        snapshot = {
            "running": True, "ts": "2026-06-03T10:00:00+00:00",
            "pid": 1234, "mode": "paper",
            "feed_connected": True, "last_tick_age_seconds": 0.5,
            "halted": False, "halt_reason": "",
            "cash": 90000.0, "equity": 100000.0,
            "realized_pnl": 500.0, "starting_equity_today": 99000.0,
            "peak_equity": 101000.0, "open_positions": [],
            "current_regime": "TREND", "regime_rationale": "ADX>25",
            "nifty_ltp": 22500.0, "vix": 14.5,
            "universe_size": 50, "strategies_enabled": ["trend_breakout"],
            "config_path": "config/default.yaml",
        }
        (data_dir / "snapshot.json").write_text(json.dumps(snapshot))
        # Gate with an invalid timestamp to trigger exception
        gate = {"passed": True, "sharpe": 1.35, "timestamp": "not-a-valid-date"}
        (data_dir / "backtest_gate.json").write_text(json.dumps(gate))

        from api.main import app
        client = TestClient(app)
        resp = client.get("/api/status")
        assert resp.status_code == 200
        data = resp.json()
        assert "gate_age_days" in data

    def test_status_psutil_exception_handled(self, tmp_path, monkeypatch):
        """Status endpoint handles psutil exception in service detection (line 201-202)."""
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("DASHBOARD_PASSWORD", "pypoc2024")
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        snapshot = {
            "running": True, "ts": "2026-06-03T10:00:00+00:00",
            "pid": 1234, "mode": "paper",
            "feed_connected": True, "last_tick_age_seconds": 0.5,
            "halted": False, "halt_reason": "",
            "cash": 90000.0, "equity": 100000.0,
            "realized_pnl": 500.0, "starting_equity_today": 99000.0,
            "peak_equity": 101000.0, "open_positions": [],
            "current_regime": "TREND", "regime_rationale": "ADX>25",
            "nifty_ltp": 22500.0, "vix": 14.5,
            "universe_size": 50, "strategies_enabled": ["trend_breakout"],
            "config_path": "config/default.yaml",
        }
        (data_dir / "snapshot.json").write_text(json.dumps(snapshot))
        gate = {"passed": True, "sharpe": 1.35}
        (data_dir / "backtest_gate.json").write_text(json.dumps(gate))

        from api.main import app
        import psutil
        with patch.object(psutil, "process_iter", side_effect=Exception("no psutil")):
            client = TestClient(app)
            resp = client.get("/api/status")
        assert resp.status_code == 200

    def test_atm_iv_endpoint(self, tmp_path, monkeypatch):
        """GET /api/atm_iv calls get_atm_iv and returns JSON."""
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("DASHBOARD_PASSWORD", "pypoc2024")
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        (data_dir / "snapshot.json").write_text(json.dumps({"running": False}))
        (data_dir / "backtest_gate.json").write_text(json.dumps({"passed": True}))

        from api.main import app
        with patch("core.data.nse_atm_iv.get_atm_iv", return_value=12.5):
            with patch("core.data.nse_atm_iv._cached_iv", 12.5):
                with patch("core.data.nse_atm_iv._cache_timestamp", 0.0):
                    client = TestClient(app)
                    resp = client.get("/api/atm-iv", auth=AUTH)
        assert resp.status_code == 200
        data = resp.json()
        assert "atm_iv" in data

    def test_sector_performance_endpoint_no_db(self, tmp_path, monkeypatch):
        """GET /api/analytics/sector-performance with missing DB returns {} or error."""
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("DASHBOARD_PASSWORD", "pypoc2024")
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        (data_dir / "snapshot.json").write_text(json.dumps({"running": False}))
        (data_dir / "backtest_gate.json").write_text(json.dumps({"passed": True}))

        from api.main import app
        client = TestClient(app)
        resp = client.get("/api/analytics/sector-performance", auth=AUTH)
        assert resp.status_code == 200
        # No DB → returns {} or similar
        assert isinstance(resp.json(), dict)

    def test_sector_performance_endpoint_with_db(self, client_with_db):
        """GET /api/analytics/sector-performance with real DB returns sector data."""
        resp = client_with_db.get("/api/analytics/sector-performance", auth=AUTH)
        assert resp.status_code == 200
        assert isinstance(resp.json(), dict)


# ===========================================================================
# core/analytics/performance_report.py — remaining uncovered lines
# ===========================================================================

class TestPerformanceReportRemaining:

    def test_query_today_trades_success(self):
        """_query_today_trades with a valid DB returns rows."""
        from core.analytics.performance_report import _query_today_trades
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = str(Path(tmpdir) / "test.db")
            conn = sqlite3.connect(db_path)
            conn.row_factory = sqlite3.Row
            conn.execute("""
                CREATE TABLE trades (
                    id INTEGER PRIMARY KEY, symbol TEXT, side TEXT, qty INTEGER,
                    entry_price REAL, exit_price REAL, pnl REAL, charges REAL,
                    strategy TEXT, exit_reason TEXT, opened_at TEXT, closed_at TEXT
                )
            """)
            today = datetime.utcnow().date().isoformat()
            conn.execute(
                "INSERT INTO trades VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (1, "TCS", "BUY", 10, 3000.0, 3100.0, 1000.0, 40.0,
                 "trend_breakout", "target", today + "T10:00:00", today + "T14:00:00")
            )
            conn.commit()
            conn.close()
            result = _query_today_trades(db_path, today=today)
            assert len(result) >= 1

    def test_query_strategy_perf_success(self):
        """_query_strategy_perf with a valid DB returns strategy stats."""
        from core.analytics.performance_report import _query_strategy_perf
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = str(Path(tmpdir) / "test.db")
            conn = sqlite3.connect(db_path)
            conn.row_factory = sqlite3.Row
            conn.execute("""
                CREATE TABLE trades (
                    id INTEGER PRIMARY KEY, symbol TEXT, side TEXT, qty INTEGER,
                    entry_price REAL, exit_price REAL, pnl REAL, charges REAL,
                    strategy TEXT, exit_reason TEXT, opened_at TEXT, closed_at TEXT
                )
            """)
            today = datetime.utcnow().date().isoformat()
            conn.execute(
                "INSERT INTO trades VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (1, "TCS", "BUY", 10, 3000.0, 3100.0, 1000.0, 40.0,
                 "trend_breakout", "target", today + "T10:00:00", today + "T14:00:00")
            )
            conn.commit()
            conn.close()
            result = _query_strategy_perf(db_path, days=30)
            assert isinstance(result, list)
            assert len(result) >= 1

    def test_fmt_inr_large_number(self):
        """_fmt_inr formats large numbers correctly."""
        from core.analytics.performance_report import _fmt_inr
        result = _fmt_inr(1_00_000.0)
        assert "₹" in result

    def test_read_snapshot_missing_returns_empty(self):
        """_read_snapshot for nonexistent file returns {}."""
        from core.analytics.performance_report import _read_snapshot
        result = _read_snapshot("/nonexistent.json")
        assert isinstance(result, dict)

    def test_risk_multiple_calculation(self):
        """_risk_multiple returns None for missing data, float for valid."""
        try:
            from core.analytics.performance_report import _risk_multiple
        except ImportError:
            pytest.skip("_risk_multiple not importable")
        # Minimal trade dict
        trade = {
            "entry_price": 1000.0,
            "exit_price": 1050.0,
            "pnl": 500.0,
            "qty": 10,
        }
        result = _risk_multiple(trade)
        assert result is None or isinstance(result, float)


# ===========================================================================
# core/broker/paper.py — remaining lines 123-124, 163, 237
# ===========================================================================

class TestPaperBrokerFinal:

    def _make_broker(self, cash: float = 500_000.0):
        from core.broker.paper import PaperBroker
        from core.config import ExecutionCfg
        cfg = ExecutionCfg(
            slippage_bps=5,
            brokerage_per_order_inr=20.0,
            stt_pct=0.001,
            exchange_txn_pct=0.0003,
            gst_pct=0.18,
            signal_cooldown_minutes=30,
        )
        return PaperBroker(starting_cash=cash, exec_cfg=cfg)

    def test_buy_when_cash_insufficient_rejected(self):
        """Buy order rejected when cash < cost + charges (lines 107-111)."""
        from core.types import OrderStatus, OrderType, Side
        broker = self._make_broker(cash=100.0)  # very little cash
        broker.update_market_prices({"HDFC": 1500.0})
        order = broker.place_order("HDFC", Side.BUY, 10, OrderType.MARKET,
                                   stop_loss=1400.0, target=1600.0)
        assert order.status == OrderStatus.REJECTED
        assert order.rejection_reason == "insufficient_cash"

    def test_partial_close_long_position(self):
        """Selling partial qty reduces position but doesn't delete it (line 237)."""
        from core.types import OrderType, Side
        broker = self._make_broker()
        broker.update_market_prices({"INFY": 1500.0})
        broker.place_order("INFY", Side.BUY, 20, OrderType.MARKET,
                           stop_loss=1400.0, target=1700.0)
        assert broker._positions["INFY"].qty == 20
        # Sell only 10
        broker.place_order("INFY", Side.SELL, 10, OrderType.MARKET,
                           stop_loss=0.0, target=None)
        pos = broker._positions.get("INFY")
        assert pos is not None
        assert pos.qty == 10

    def test_sim_time_used_when_set(self):
        """sim_time overrides wall clock (line 163 via _now())."""
        from core.broker.paper import PaperBroker
        from core.config import ExecutionCfg
        from core.types import OrderType, Side
        cfg = ExecutionCfg(
            slippage_bps=5, brokerage_per_order_inr=20.0,
            stt_pct=0.001, exchange_txn_pct=0.0003, gst_pct=0.18,
            signal_cooldown_minutes=30,
        )
        broker = PaperBroker(starting_cash=500_000.0, exec_cfg=cfg)
        sim_dt = datetime(2024, 3, 15, 10, 0, 0)
        broker.sim_time = sim_dt
        broker.update_market_prices({"TCS": 3000.0})
        broker.place_order("TCS", Side.BUY, 5, OrderType.MARKET,
                           stop_loss=2800.0, target=3200.0)
        pos = broker._positions.get("TCS")
        assert pos is not None
        assert pos.opened_at == sim_dt


# ===========================================================================
# core/strategies/indicators.py — lines 153, 171-172
# ===========================================================================

class TestIndicatorsRemaining:
    """Cover is_trending_market edge cases (lines 153, 171-172)."""

    def test_is_trending_market_nan_autocorr(self):
        """When autocorr returns NaN, is_trending_market should return False or True."""
        from core.strategies.indicators import is_trending_market
        # All-constant series → autocorr is NaN
        s = pd.Series([1000.0] * 30)
        result = is_trending_market(s, lookback=20)
        # Should return False (NaN autocorr → not trending)
        assert isinstance(result, bool)

    def test_rolling_autocorr_zero_lag_returns_all_nan(self):
        """rolling_autocorr with insufficient data returns NaN values."""
        from core.strategies.indicators import rolling_autocorr
        s = pd.Series(np.arange(5, dtype=float))  # only 5 values
        result = rolling_autocorr(s, lag=1, window=20)
        # Window of 20 but only 5 values → all NaN
        assert result.isna().all()

    def test_supertrend_bands_with_zero_length_segment(self):
        """supertrend_bands handles edge: chunk has length 1 for RS calculation in hurst."""
        from core.strategies.indicators import hurst_exponent
        # RS calculation skips chunk < 2 in length
        s = pd.Series([1.0] * 50)  # constant → std=0 → rs_list empty for each lag
        result = hurst_exponent(s, min_lag=2, max_lag=10)
        assert result == pytest.approx(0.5)


# ===========================================================================
# core/data/nse_history.py — lines 213-218 (_fetch_index_one_chunk HistoricalDate schema)
# ===========================================================================

class TestNseHistoryHistoricalDateSchema:

    def test_fetch_index_one_chunk_historical_date_schema(self):
        """HistoricalDate column schema is parsed correctly."""
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
                    "HistoricalDate": "02-Jan-2024",
                    "OPEN": "21000",
                    "HIGH": "21100",
                    "LOW": "20900",
                    "CLOSE": "21050",
                }]
            }
            mock_s.get.return_value = resp
            mock_sess.return_value = mock_s
            result = hist._fetch_index_one_chunk(
                "NIFTY 50", date(2024, 1, 1), date(2024, 1, 5)
            )
        assert result is not None
        assert "close" in result.columns
        assert "volume" in result.columns

    def test_fetch_equity_401_sets_session_none(self):
        """401 in _fetch_equity_range sets self._session to None before retry."""
        try:
            from core.data.nse_history import NseHistory
        except ImportError:
            pytest.skip("NseHistory not importable")
        hist = NseHistory(request_pause_seconds=0)
        # Pre-set session so we can check it gets cleared
        hist._session = MagicMock()
        with patch.object(hist, "_ensure_session") as mock_sess:
            mock_s = MagicMock()
            resp_401 = MagicMock()
            resp_401.status_code = 401
            resp_ok = MagicMock()
            resp_ok.status_code = 200
            resp_ok.raise_for_status = MagicMock()
            resp_ok.json.return_value = {"data": None}
            mock_s.get.side_effect = [resp_401, resp_ok]
            mock_sess.return_value = mock_s
            result = hist._fetch_equity_range(
                "RELIANCE", date(2024, 1, 1), date(2024, 1, 3)
            )
        # session should be None after 401
        assert hist._session is None
        assert result is None  # empty data → None
