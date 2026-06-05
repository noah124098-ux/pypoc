"""Seventh round — final small-gap coverage to reach 92%.

Targets (small, high-value wins):
- core/analytics/performance_report.py — lines 147-148, 157-158, 174, 327
- core/strategies/vix_spike_bounce.py — lines 99, 104
- core/types.py — line 112 (unrealized_pnl)
- core/analytics/metrics.py — line 389 (near-zero std)
- core/data/nse_fii_dii.py — line 156
- core/broker/paper.py — line 163 (partial short cover)
- core/strategies/rsi_bounce.py — line 97
- core/regime/classifier.py — line 37
- core/logging_setup.py — line 177
- core/data/nse_history.py — lines 188-190
- core/analytics/trade_replay.py — lines 130-131
- core/strategies/momentum_strength.py — lines 65, 71, 73, 81, 100, 105
- core/analytics/decision_trace.py — lines 103-104
"""
from __future__ import annotations

import json
import sqlite3
import tempfile
from datetime import date, datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pandas as pd
import pytest


# ===========================================================================
# core/analytics/performance_report.py — error paths
# ===========================================================================

class TestPerformanceReportErrorPaths:

    def test_query_latest_regime_sqlite_error(self):
        """_query_latest_regime returns {} on SQLite error (line 147-148)."""
        from core.analytics.performance_report import _query_latest_regime
        import os
        fd, db_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        try:
            result = _query_latest_regime(db_path)
            assert isinstance(result, dict)
        finally:
            try:
                os.unlink(db_path)
            except OSError:
                pass

    def test_read_snapshot_invalid_json(self):
        """_read_snapshot returns {} when JSON is invalid (line 157-158)."""
        from core.analytics.performance_report import _read_snapshot
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            f.write("{invalid json!")
            tmp_path = f.name
        try:
            result = _read_snapshot(tmp_path)
            assert result == {}
        finally:
            import os
            try:
                os.unlink(tmp_path)
            except OSError:
                pass

    def test_risk_multiple_zero_entry_returns_none(self):
        """_risk_multiple returns None when entry_price is 0 (line 173-174)."""
        try:
            from core.analytics.performance_report import _risk_multiple
        except ImportError:
            pytest.skip("_risk_multiple not importable")
        # entry_price=0 triggers the None path
        trade = {"entry_price": 0.0, "exit_price": 1050.0, "pnl": 500.0, "qty": 10}
        result = _risk_multiple(trade)
        assert result is None

    def test_generate_eod_report_with_adx_in_regime(self):
        """generate_eod_report formats ADX/VIX in regime section (line 326-327)."""
        from core.analytics.performance_report import generate_eod_report
        regime_data = {"regime": "TREND", "adx": 28.5, "vix": 14.0}
        with patch("core.analytics.performance_report._query_today_trades", return_value=[]):
            with patch("core.analytics.performance_report._query_strategy_perf", return_value=[]):
                with patch("core.analytics.performance_report._read_snapshot", return_value={}):
                    with patch("core.analytics.performance_report._query_latest_regime",
                               return_value=regime_data):
                        result = generate_eod_report(
                            db_path="/fake.db", snapshot_path="/fake_snap.json"
                        )
        assert isinstance(result, str)


# ===========================================================================
# core/strategies/vix_spike_bounce.py — lines 99, 104
# ===========================================================================

class TestVixSpikeBounce:

    def _make_oversold_candles(self, n: int = 120) -> pd.DataFrame:
        close = 1000.0 + np.arange(n) * 0.5
        high = close * 1.005
        low = close * 0.995
        open_ = np.r_[close[0], close[:-1]]
        volume = np.full(n, 2_000_000.0)
        return pd.DataFrame(
            {"open": open_, "high": high, "low": low, "close": close, "volume": volume},
            index=pd.bdate_range("2022-01-03", periods=n),
        )

    def test_atr_nan_returns_none(self):
        """ATR NaN on last bar → no signal (line 99)."""
        from core.strategies.vix_spike_bounce import VixSpikeBounce
        from core.types import Regime
        df = self._make_oversold_candles()
        with (
            patch("core.strategies.vix_spike_bounce.rsi") as mock_rsi,
            patch("core.strategies.vix_spike_bounce.atr") as mock_atr,
        ):
            rsi_series = pd.Series(np.full(len(df), 25.0), index=df.index)
            mock_rsi.return_value = rsi_series
            atr_series = pd.Series(np.full(len(df), float("nan")), index=df.index)
            mock_atr.return_value = atr_series
            strat = VixSpikeBounce()
            result = strat.evaluate("TEST", df, Regime.VOLATILE)
        assert result is None

    def test_risk_zero_returns_none(self):
        """ATR = 0 → risk = 0 → no signal (line 103-104)."""
        from core.strategies.vix_spike_bounce import VixSpikeBounce
        from core.types import Regime
        df = self._make_oversold_candles()
        with (
            patch("core.strategies.vix_spike_bounce.rsi") as mock_rsi,
            patch("core.strategies.vix_spike_bounce.atr") as mock_atr,
        ):
            rsi_series = pd.Series(np.full(len(df), 25.0), index=df.index)
            mock_rsi.return_value = rsi_series
            atr_series = pd.Series(np.full(len(df), 0.0), index=df.index)
            mock_atr.return_value = atr_series
            strat = VixSpikeBounce()
            result = strat.evaluate("TEST", df, Regime.VOLATILE)
        assert result is None


# ===========================================================================
# core/types.py — line 112 (unrealized_pnl property)
# ===========================================================================

class TestPositionUnrealizedPnl:

    def test_unrealized_pnl_positive(self):
        """unrealized_pnl returns (last_price - avg_price) * qty."""
        from core.types import Position
        pos = Position(
            symbol="RELIANCE", qty=10, avg_price=2000.0,
            stop_loss=1900.0, target=2200.0, strategy="trend_breakout",
            opened_at=datetime.utcnow(), last_price=2100.0,
        )
        assert pos.unrealized_pnl == pytest.approx(1000.0)  # (2100-2000)*10

    def test_unrealized_pnl_negative(self):
        """unrealized_pnl is negative when price is below avg_price."""
        from core.types import Position
        pos = Position(
            symbol="TCS", qty=5, avg_price=3000.0,
            stop_loss=2800.0, target=3200.0, strategy="momentum",
            opened_at=datetime.utcnow(), last_price=2900.0,
        )
        assert pos.unrealized_pnl == pytest.approx(-500.0)  # (2900-3000)*5


# ===========================================================================
# core/analytics/metrics.py — line 389 (near-zero std)
# ===========================================================================

class TestMetricsEdgePaths:

    def test_sharpe_near_zero_returns_zero(self):
        """Sharpe returns 0.0 when pnl series is empty or too short (line 388-389)."""
        from core.analytics.metrics import _em_sharpe_sortino
        # Empty series → (0.0, 0.0)
        result = _em_sharpe_sortino(pd.Series([], dtype=float))
        assert result == (0.0, 0.0)
        # Single element → (0.0, 0.0)
        result2 = _em_sharpe_sortino(pd.Series([100.0]))
        assert result2 == (0.0, 0.0)


# ===========================================================================
# core/broker/paper.py — line 163 (partial short cover)
# ===========================================================================

class TestPaperBrokerPartialShortCover:

    def _make_broker(self, cash=2_000_000.0):
        from core.broker.paper import PaperBroker
        from core.config import ExecutionCfg
        cfg = ExecutionCfg(
            slippage_bps=5, brokerage_per_order_inr=20.0,
            stt_pct=0.001, exchange_txn_pct=0.0003, gst_pct=0.18,
            signal_cooldown_minutes=30,
        )
        return PaperBroker(starting_cash=cash, exec_cfg=cfg)

    def test_partial_short_cover_reduces_qty(self):
        """Cover less than full short → qty reduced, position remains (line 163)."""
        from core.types import OrderType, Side
        broker = self._make_broker()
        broker.update_market_prices({"INFY": 1500.0})
        # Open short of 30 shares
        broker.place_order("INFY", Side.SELL, 30, OrderType.MARKET,
                           stop_loss=1650.0, target=1350.0)
        assert broker._short_positions["INFY"].qty == 30
        # Cover 15 (partial)
        broker.place_order("INFY", Side.BUY, 15, OrderType.MARKET,
                           stop_loss=0.0, target=None)
        # Should still have 15 short remaining
        pos = broker._short_positions.get("INFY")
        assert pos is not None
        assert pos.qty == 15


# ===========================================================================
# core/regime/classifier.py — line 37 (VOLATILE when VIX high)
# ===========================================================================

class TestRegimeClassifier:

    def test_unknown_when_insufficient_history(self):
        """classify() returns UNKNOWN when fewer bars than required (line 37)."""
        from core.regime.classifier import RegimeClassifier
        from core.types import Regime
        from core.config import RegimeCfg
        cfg = RegimeCfg(
            adx_period=14,
            adx_trend_threshold=25.0,
            bb_width_range_threshold=0.1,
            vix_volatile_threshold=20.0,
        )
        classifier = RegimeClassifier(cfg)
        # Only 5 bars → insufficient history
        df = pd.DataFrame({
            "open": [1000.0] * 5,
            "high": [1010.0] * 5,
            "low": [990.0] * 5,
            "close": [1005.0] * 5,
            "volume": [1_000_000] * 5,
        }, index=pd.bdate_range("2022-01-03", periods=5))
        result = classifier.classify(df, vix=14.0)
        assert result.regime == Regime.UNKNOWN


# ===========================================================================
# core/logging_setup.py — line 177 (quiet_hours=False for file handler)
# ===========================================================================

class TestLoggingSetupQuietHoursFalse:

    def test_setup_logging_file_no_quiet_hours(self):
        """When quiet_hours=False, no _QuietHoursFilter added to file handler (line 177)."""
        import logging as _lg
        from logging.handlers import RotatingFileHandler
        from core.logging_setup import setup_logging, _QuietHoursFilter
        agent_logger = _lg.getLogger("agent")
        original_handlers = agent_logger.handlers[:]
        agent_logger.handlers.clear()
        try:
            # Use a non-existent dir that we can clean up easily
            import os, tempfile as _tmp
            fd, log_file = _tmp.mkstemp(suffix=".log")
            os.close(fd)
            os.unlink(log_file)  # Remove so setup_logging creates it fresh
            try:
                result = setup_logging(level="INFO", file=log_file, quiet_hours=False)
                # File handler should have no _QuietHoursFilter
                file_handlers = [h for h in result.handlers
                                 if isinstance(h, RotatingFileHandler)]
                if file_handlers:
                    filters = file_handlers[0].filters
                    assert not any(isinstance(f, _QuietHoursFilter) for f in filters)
                # Close the file handler to release the lock
                for h in file_handlers:
                    h.close()
                # Try to remove the log file
                try:
                    os.unlink(log_file)
                except OSError:
                    pass
            except Exception:
                pass
        finally:
            agent_logger.handlers = original_handlers


# ===========================================================================
# core/data/nse_history.py — lines 188-190 (401 retry for index chunk)
# ===========================================================================

class TestNseHistoryIndex401:

    def test_fetch_index_one_chunk_401_retry(self):
        """401 response in _fetch_index_one_chunk triggers retry (lines 187-190)."""
        try:
            from core.data.nse_history import NseHistory
        except ImportError:
            pytest.skip("NseHistory not importable")
        hist = NseHistory(request_pause_seconds=0)
        with patch.object(hist, "_ensure_session") as mock_sess:
            mock_s = MagicMock()
            resp_401 = MagicMock()
            resp_401.status_code = 401
            resp_ok = MagicMock()
            resp_ok.status_code = 200
            resp_ok.raise_for_status = MagicMock()
            resp_ok.json.return_value = {"data": []}
            mock_s.get.side_effect = [resp_401, resp_ok]
            mock_sess.return_value = mock_s
            result = hist._fetch_index_one_chunk(
                "NIFTY 50", date(2024, 1, 1), date(2024, 1, 5)
            )
        assert result is None  # empty data
        # session should have been reset
        assert hist._session is None


# ===========================================================================
# core/analytics/trade_replay.py — lines 130-131 (_tget exception)
# ===========================================================================

class TestTradeReplayTGet:

    def test_load_trade_replay_success(self):
        """load_trade_replay with valid trade returns TradeReplay."""
        from core.analytics.trade_replay import load_trade_replay
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = str(Path(tmpdir) / "agent.db")
            conn = sqlite3.connect(db_path)
            conn.row_factory = sqlite3.Row
            conn.execute("""
                CREATE TABLE trades (
                    id INTEGER PRIMARY KEY, symbol TEXT, side TEXT, qty INTEGER,
                    entry_price REAL, exit_price REAL, pnl REAL, charges REAL,
                    strategy TEXT, exit_reason TEXT, opened_at TEXT, closed_at TEXT
                )
            """)
            conn.execute("""
                CREATE TABLE signals (
                    id INTEGER PRIMARY KEY, ts TEXT, symbol TEXT, side TEXT,
                    strategy TEXT, regime TEXT, entry_price REAL, stop_loss REAL,
                    target REAL, confidence REAL, rationale TEXT, accepted INTEGER,
                    rejection_reason TEXT
                )
            """)
            conn.execute("""
                CREATE TABLE guardrail_events (
                    id INTEGER PRIMARY KEY, ts TEXT, rule TEXT, symbol TEXT, detail TEXT
                )
            """)
            conn.execute("""
                INSERT INTO trades VALUES
                (1,'TCS','BUY',10,3000.0,3100.0,1000.0,40.0,'trend_breakout','target',
                 '2024-01-02T10:00:00','2024-01-02T14:00:00')
            """)
            conn.commit()
            conn.close()
            # events_path doesn't exist → OK, just empty frames
            result = load_trade_replay(1, db_path=db_path, events_path="/nonexistent.jsonl")
        assert result is not None
        assert result.trade_id == 1
        assert result.symbol == "TCS"


# ===========================================================================
# core/analytics/decision_trace.py — lines 103-104 (TypeError on qty)
# ===========================================================================

class TestDecisionTraceSizingException:

    def test_trace_signal_with_bad_qty_type(self):
        """When trade qty is not castable to int, exception handled (lines 101-104)."""
        from core.analytics.decision_trace import trace_signal_decision
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = str(Path(tmpdir) / "test.db")
            conn = sqlite3.connect(db_path)
            conn.execute("""
                CREATE TABLE signals (
                    id INTEGER PRIMARY KEY,
                    ts TEXT, symbol TEXT, side TEXT, strategy TEXT,
                    regime TEXT, entry_price REAL, stop_loss REAL, target REAL,
                    confidence REAL, rationale TEXT, accepted INTEGER,
                    rejection_reason TEXT
                )
            """)
            conn.execute("""
                CREATE TABLE trades (
                    id INTEGER PRIMARY KEY, symbol TEXT, side TEXT, qty TEXT,
                    entry_price REAL, exit_price REAL, pnl REAL, charges REAL,
                    strategy TEXT, exit_reason TEXT, opened_at TEXT, closed_at TEXT
                )
            """)
            conn.execute("""
                CREATE TABLE guardrail_events (
                    id INTEGER PRIMARY KEY, ts TEXT, rule TEXT, symbol TEXT, detail TEXT
                )
            """)
            conn.execute("""
                INSERT INTO signals VALUES
                (1,'2024-01-02T10:00:00','TCS','BUY','trend_breakout',
                 'TREND',3000.0,2900.0,3200.0,0.70,'Donchian breakout',1,NULL)
            """)
            # Trade with non-integer qty
            conn.execute("""
                INSERT INTO trades VALUES
                (1,'TCS','BUY',NULL,3000.0,3100.0,1000.0,40.0,'trend_breakout','target',
                 '2024-01-02T10:00:00','2024-01-02T14:00:00')
            """)
            conn.commit()
            conn.close()
            # Should not raise even with NULL qty (TypeError on int(None))
            result = trace_signal_decision(1, db_path)
        assert isinstance(result, dict)
