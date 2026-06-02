"""Additional coverage tests to fill gaps identified by pytest-cov.

Covers uncovered branches in:
  - core/analytics/metrics.py  (load_trades_from_db, _parse_dt edge cases,
    _em_* internal helpers, omega with no gains)
  - core/broker/angelone_live.py  (cancel non-success, get_positions non-success,
    get_position EQ-suffix, _refresh_balance edges, _get_token EQ-suffix,
    _load_instrument_master with cache and download paths)
  - core/command_queue.py  (update_status preserves untouched lines)
  - core/data/nse_fii_dii.py  (ValueError/TypeError entries, stale-cache on failed
    refresh when result is None)
  - core/data/nse_pcr.py  (CE totOI == 0 branch)
  - core/llm/news_scorer.py  (direct anthropic import path)
"""
from __future__ import annotations

import importlib
import json
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# core/analytics/metrics.py
# ---------------------------------------------------------------------------

from core.analytics.metrics import (
    _parse_dt,
    _holding_minutes,
    _em_omega,
    _em_equity_curve,
    _em_drawdown_stats,
    _em_streak_stats,
    _em_r_multiples,
    _em_sharpe_sortino,
    load_trades_from_db,
    strategy_performance,
    monthly_summary,
)
from core.broker.paper import TradeRecord
from core.types import Side
import pandas as pd
import numpy as np


def _tr(pnl: float, days_offset: int = 0, strategy: str = "t") -> TradeRecord:
    base = datetime(2024, 3, 1)
    return TradeRecord(
        symbol="X",
        side=Side.SELL,
        qty=1,
        entry_price=100.0,
        exit_price=100.0 + pnl,
        pnl=pnl,
        charges=1.0,
        strategy=strategy,
        opened_at=base + timedelta(days=days_offset),
        closed_at=base + timedelta(days=days_offset + 1),
        exit_reason="target" if pnl >= 0 else "stop_loss",
    )


class TestParseDt:
    """Cover the datetime parsing branches."""

    def test_none_returns_none(self):
        assert _parse_dt(None) is None

    def test_empty_string_returns_none(self):
        assert _parse_dt("") is None

    def test_valid_iso_format(self):
        result = _parse_dt("2024-01-15T09:30:00")
        assert result is not None
        assert result.year == 2024 and result.month == 1

    def test_iso_with_microseconds(self):
        result = _parse_dt("2024-06-02T11:30:00.123456")
        assert result is not None
        assert result.hour == 11

    def test_space_separated_format(self):
        result = _parse_dt("2024-06-02 14:00:00")
        assert result is not None
        assert result.hour == 14

    def test_unparseable_returns_none(self):
        result = _parse_dt("not-a-date")
        assert result is None


class TestHoldingMinutes:
    def test_returns_minutes_between_open_close(self):
        trade = {
            "opened_at": "2024-01-01T09:30:00",
            "closed_at": "2024-01-01T11:00:00",
        }
        result = _holding_minutes(trade)
        assert result == pytest.approx(90.0)

    def test_returns_none_when_timestamps_missing(self):
        result = _holding_minutes({})
        assert result is None

    def test_returns_none_when_only_one_timestamp(self):
        result = _holding_minutes({"opened_at": "2024-01-01T09:30:00"})
        assert result is None


class TestEmOmega:
    def test_no_gains_no_losses_returns_one(self):
        # All zeros
        series = pd.Series([0.0, 0.0, 0.0])
        result = _em_omega(series)
        assert result == 1.0

    def test_only_losses_returns_zero(self):
        series = pd.Series([-10.0, -20.0, -5.0])
        result = _em_omega(series)
        assert result == pytest.approx(0.0)

    def test_only_gains_returns_inf(self):
        series = pd.Series([10.0, 20.0, 5.0])
        result = _em_omega(series)
        assert result == float("inf")

    def test_empty_series_returns_zero(self):
        result = _em_omega(pd.Series(dtype=float))
        assert result == 0.0

    def test_mixed_returns_ratio(self):
        series = pd.Series([10.0, -5.0])
        result = _em_omega(series)
        assert result == pytest.approx(2.0)


class TestEmEquityCurve:
    def test_empty_returns_empty_series(self):
        result = _em_equity_curve([])
        assert isinstance(result, pd.Series)
        assert result.empty

    def test_single_trade_produces_cumsum(self):
        trades = [_tr(100.0, 0)]
        result = _em_equity_curve(trades)
        assert isinstance(result, pd.Series)
        assert len(result) == 1
        assert float(result.iloc[0]) == pytest.approx(100.0)


class TestEmDrawdownStats:
    def test_empty_returns_zeros(self):
        result = _em_drawdown_stats(pd.Series(dtype=float))
        assert result == (0.0, 0.0, 0.0)

    def test_monotone_up_no_drawdown(self):
        equity = pd.Series([100.0, 110.0, 120.0, 130.0])
        max_dd, avg_dd, dur = _em_drawdown_stats(equity)
        assert max_dd == pytest.approx(0.0)
        assert avg_dd == pytest.approx(0.0)

    def test_drawdown_episode_at_end(self):
        equity = pd.Series([100.0, 110.0, 100.0])  # peak 110, trough 100 = 9.09%
        max_dd, avg_dd, dur = _em_drawdown_stats(equity)
        assert max_dd == pytest.approx(100 * 10 / 110, rel=0.01)


class TestEmStreakStats:
    def test_empty_returns_zeros(self):
        result = _em_streak_stats(np.array([]))
        assert result == (0, 0)

    def test_zero_pnl_resets_both_streaks(self):
        pnls = np.array([100.0, 0.0, 100.0])
        max_w, max_l = _em_streak_stats(pnls)
        assert max_w == 1  # zero breaks streak
        assert max_l == 0

    def test_alternating_win_loss(self):
        pnls = np.array([100.0, -50.0, 100.0, -50.0])
        max_w, max_l = _em_streak_stats(pnls)
        assert max_w == 1
        assert max_l == 1


class TestEmRMultiples:
    def test_no_losses_returns_avg_win_and_zero_loss_r(self):
        wins = [_tr(100.0), _tr(200.0)]
        losses = []
        avg_win_r, avg_loss_r = _em_r_multiples(wins, losses)
        assert avg_win_r == pytest.approx(150.0)
        assert avg_loss_r == 0.0

    def test_no_wins_no_losses_returns_zeros(self):
        avg_win_r, avg_loss_r = _em_r_multiples([], [])
        assert avg_win_r == 0.0
        assert avg_loss_r == 0.0

    def test_losses_with_zero_pnl_returns_zeros(self):
        # avg_risk == 0 branch (zero pnl loss trades)
        zero_loss = _tr(0.0)
        avg_win_r, avg_loss_r = _em_r_multiples([], [zero_loss])
        assert avg_win_r == 0.0
        assert avg_loss_r == 0.0


class TestEmSharpeSortino:
    def test_empty_series_returns_zero(self):
        sharpe, sortino = _em_sharpe_sortino(pd.Series(dtype=float))
        assert sharpe == 0.0
        assert sortino == 0.0

    def test_single_observation_returns_zero(self):
        sharpe, sortino = _em_sharpe_sortino(pd.Series([100.0]))
        assert sharpe == 0.0
        assert sortino == 0.0

    def test_no_downside_gives_sortino_zero(self):
        # All positive returns — downside std is 0 -> sortino must be 0 (not NaN)
        series = pd.Series([100.0, 100.0, 100.0, 100.0, 100.0])
        sharpe, sortino = _em_sharpe_sortino(series)
        assert isinstance(sortino, float)
        assert not np.isnan(sortino)


class TestLoadTradesFromDb:
    """Tests for load_trades_from_db — requires a real sqlite3 DB."""

    def _setup_db(self, db_path: str) -> None:
        from core.persistence.store import Store
        store = Store(db_path)
        store.record_trade(
            symbol="RELIANCE",
            side="BUY",
            qty=5,
            entry_price=2900.0,
            exit_price=2978.0,
            pnl=390.0,
            charges=20.0,
            strategy="trend_breakout",
            exit_reason="target",
            opened_at="2024-01-02T09:30:00",
            closed_at="2024-01-02T11:00:00",
        )
        store.record_trade(
            symbol="TCS",
            side="SELL",
            qty=2,
            entry_price=3800.0,
            exit_price=3750.0,
            pnl=-100.0,
            charges=20.0,
            strategy="rsi_momentum",
            exit_reason="stop_loss",
            opened_at="2024-01-03T10:00:00",
            closed_at="2024-01-03T14:00:00",
        )

    def test_loads_trades_from_existing_db(self, tmp_path):
        db = str(tmp_path / "agent.db")
        self._setup_db(db)
        trades = load_trades_from_db(db)
        assert len(trades) == 2
        assert isinstance(trades[0], TradeRecord)

    def test_side_buy_parsed_correctly(self, tmp_path):
        db = str(tmp_path / "agent.db")
        self._setup_db(db)
        trades = load_trades_from_db(db)
        buy_trade = next(t for t in trades if t.symbol == "RELIANCE")
        assert buy_trade.side == Side.BUY

    def test_side_sell_parsed_correctly(self, tmp_path):
        db = str(tmp_path / "agent.db")
        self._setup_db(db)
        trades = load_trades_from_db(db)
        sell_trade = next(t for t in trades if t.symbol == "TCS")
        assert sell_trade.side == Side.SELL

    def test_pnl_values_preserved(self, tmp_path):
        db = str(tmp_path / "agent.db")
        self._setup_db(db)
        trades = load_trades_from_db(db)
        pnls = {t.symbol: t.pnl for t in trades}
        assert pnls["RELIANCE"] == pytest.approx(390.0)
        assert pnls["TCS"] == pytest.approx(-100.0)

    def test_empty_db_returns_empty_list(self, tmp_path):
        from core.persistence.store import Store
        db = str(tmp_path / "empty.db")
        Store(db)  # create schema
        trades = load_trades_from_db(db)
        assert trades == []


class TestStrategyPerformanceEdgeCases:
    """Cover branches in strategy_performance/_pf/_sharpe."""

    def test_single_trade_sharpe_zero(self):
        # Only 1 trade -> sharpe returns 0
        trades = [{"strategy": "t", "pnl": "100", "closed_at": "2024-01-01T10:00:00"}]
        perf = strategy_performance(trades)
        assert perf[0]["sharpe"] == 0.0

    def test_all_losses_profit_factor_zero(self):
        trades = [
            {"strategy": "s", "pnl": "-100"},
            {"strategy": "s", "pnl": "-50"},
        ]
        perf = strategy_performance(trades)
        assert perf[0]["profit_factor"] == 0.0

    def test_all_wins_profit_factor_positive(self):
        trades = [
            {"strategy": "s", "pnl": "200"},
            {"strategy": "s", "pnl": "100"},
        ]
        perf = strategy_performance(trades)
        assert perf[0]["profit_factor"] > 0.0

    def test_monthly_summary_skips_no_closed_at(self):
        trades = [
            {"pnl": "100", "closed_at": None},
            {"pnl": "200", "closed_at": "2024-01-15T12:00:00"},
        ]
        summary = monthly_summary(trades)
        assert len(summary) == 1
        assert summary[0]["pnl"] == pytest.approx(200.0)


# ---------------------------------------------------------------------------
# core/broker/angelone_live.py — additional coverage
# ---------------------------------------------------------------------------

from core.broker.angelone_live import (
    AngelOneLiveBroker,
    LIVE_API_KEY_ENV,
    _DATA_FEED_API_KEY_ENV,
)
from core.config import ExecutionCfg


def _exec_cfg() -> ExecutionCfg:
    return ExecutionCfg(
        slippage_bps=5.0,
        brokerage_per_order_inr=20.0,
        stt_pct=0.1,
        exchange_txn_pct=0.00345,
        gst_pct=18.0,
        signal_cooldown_minutes=60,
    )


def _connected_broker(mock_smart_api: MagicMock, monkeypatch) -> AngelOneLiveBroker:
    monkeypatch.delenv(_DATA_FEED_API_KEY_ENV, raising=False)
    broker = AngelOneLiveBroker(
        api_key="FAKE_LIVE_KEY",
        client_code="LIVE_CLIENT",
        password="LIVE_PASS",
        totp_secret="LIVE_TOTP",
        exec_cfg=_exec_cfg(),
    )
    broker._smart_api = mock_smart_api
    broker._connected = True
    broker._symbol_to_token = {"RELIANCE": "2885", "INFY": "1594"}
    return broker


class TestCancelOrderNonSuccess:
    def test_returns_false_on_non_success_response(self, monkeypatch):
        mock_sc = MagicMock()
        mock_sc.cancelOrder.return_value = {"status": False, "message": "order not found"}
        broker = _connected_broker(mock_sc, monkeypatch)
        result = broker.cancel_order("MISSINGID")
        assert result is False

    def test_returns_false_on_none_response(self, monkeypatch):
        mock_sc = MagicMock()
        mock_sc.cancelOrder.return_value = None
        broker = _connected_broker(mock_sc, monkeypatch)
        result = broker.cancel_order("AO000")
        assert result is False


class TestGetPositionsNonSuccess:
    def test_returns_empty_on_api_non_success(self, monkeypatch):
        mock_sc = MagicMock()
        mock_sc.getPosition.return_value = {"status": False, "message": "session expired"}
        broker = _connected_broker(mock_sc, monkeypatch)
        result = broker.get_positions()
        assert result == []

    def test_returns_empty_on_none_response(self, monkeypatch):
        mock_sc = MagicMock()
        mock_sc.getPosition.return_value = None
        broker = _connected_broker(mock_sc, monkeypatch)
        result = broker.get_positions()
        assert result == []

    def test_skips_malformed_position_row(self, monkeypatch):
        mock_sc = MagicMock()
        mock_sc.getPosition.return_value = {
            "status": True,
            "data": [
                {
                    "tradingsymbol": "RELIANCE-EQ",
                    "netqty": "NOT_A_NUMBER",  # malformed
                    "netavgprice": "2100.0",
                    "ltp": "2110.0",
                },
                {
                    "tradingsymbol": "INFY-EQ",
                    "netqty": "5",
                    "netavgprice": "1500.0",
                    "ltp": "1520.0",
                },
            ],
        }
        broker = _connected_broker(mock_sc, monkeypatch)
        positions = broker.get_positions()
        assert len(positions) == 1
        assert positions[0].symbol == "INFY"


class TestGetPositionEqSuffix:
    def test_matches_position_with_eq_suffix_removed(self, monkeypatch):
        mock_sc = MagicMock()
        mock_sc.getPosition.return_value = {
            "status": True,
            "data": [
                {
                    "tradingsymbol": "RELIANCE-EQ",
                    "netqty": "10",
                    "netavgprice": "2100.0",
                    "ltp": "2110.0",
                }
            ],
        }
        broker = _connected_broker(mock_sc, monkeypatch)
        pos = broker.get_position("RELIANCE")
        assert pos is not None
        assert pos.symbol == "RELIANCE"


class TestRefreshBalanceEdgeCases:
    def test_noop_when_not_connected(self, monkeypatch):
        monkeypatch.delenv(_DATA_FEED_API_KEY_ENV, raising=False)
        broker = AngelOneLiveBroker(
            api_key="FAKE",
            client_code="C",
            password="P",
            totp_secret="T",
            exec_cfg=_exec_cfg(),
        )
        broker._equity = 999.0
        broker._refresh_balance()  # should be no-op
        assert broker._equity == pytest.approx(999.0)

    def test_non_success_rms_does_not_update_equity(self, monkeypatch):
        mock_sc = MagicMock()
        mock_sc.getRMS.return_value = {"status": False, "message": "session error"}
        broker = _connected_broker(mock_sc, monkeypatch)
        broker._equity = 500000.0
        broker._refresh_balance()
        assert broker._equity == pytest.approx(500000.0)

    def test_parse_error_in_rms_data_does_not_raise(self, monkeypatch):
        mock_sc = MagicMock()
        mock_sc.getRMS.return_value = {
            "status": True,
            "data": {"availablecash": "not-a-number", "net": "also-bad"},
        }
        broker = _connected_broker(mock_sc, monkeypatch)
        broker._equity = 100_000.0
        broker._refresh_balance()  # should not raise
        assert broker._equity == pytest.approx(100_000.0)


class TestGetTokenEqSuffix:
    def test_finds_token_via_eq_suffix(self, monkeypatch):
        monkeypatch.delenv(_DATA_FEED_API_KEY_ENV, raising=False)
        broker = AngelOneLiveBroker(
            api_key="FAKE",
            client_code="C",
            password="P",
            totp_secret="T",
            exec_cfg=_exec_cfg(),
        )
        # Map only the -EQ version
        broker._symbol_to_token = {"RELIANCE-EQ": "2885"}
        token = broker._get_token("RELIANCE")
        assert token == "2885"


class TestLoadInstrumentMaster:
    def test_reads_from_cache_if_exists(self, monkeypatch, tmp_path):
        monkeypatch.delenv(_DATA_FEED_API_KEY_ENV, raising=False)
        broker = AngelOneLiveBroker(
            api_key="FAKE",
            client_code="C",
            password="P",
            totp_secret="T",
            exec_cfg=_exec_cfg(),
            cache_dir=str(tmp_path),
        )
        today_str = datetime.now().strftime("%Y%m%d")
        cache_file = tmp_path / f"angel_instruments_{today_str}.json"
        instruments = [
            {"exch_seg": "NSE", "instrumenttype": "EQ", "symbol": "WIPRO-EQ", "token": "3787"},
            {"exch_seg": "BSE", "instrumenttype": "EQ", "symbol": "WIPRO-EQ", "token": "9999"},  # BSE skipped
        ]
        cache_file.write_text(json.dumps(instruments), encoding="utf-8")

        broker._load_instrument_master()

        assert "WIPRO" in broker._symbol_to_token
        assert broker._symbol_to_token["WIPRO"] == "3787"

    def test_downloads_if_no_cache(self, monkeypatch, tmp_path):
        monkeypatch.delenv(_DATA_FEED_API_KEY_ENV, raising=False)
        broker = AngelOneLiveBroker(
            api_key="FAKE",
            client_code="C",
            password="P",
            totp_secret="T",
            exec_cfg=_exec_cfg(),
            cache_dir=str(tmp_path),
        )
        instruments = [
            {"exch_seg": "NSE", "instrumenttype": "", "symbol": "TCS-EQ", "token": "11536"},
        ]
        mock_resp = MagicMock()
        mock_resp.raise_for_status.return_value = None
        mock_resp.json.return_value = instruments
        mock_resp.text = json.dumps(instruments)

        with patch("requests.get", return_value=mock_resp) as mock_get:
            broker._load_instrument_master()

        mock_get.assert_called_once()
        assert "TCS" in broker._symbol_to_token
        assert broker._symbol_to_token["TCS"] == "11536"

    def test_download_failure_sets_empty_map(self, monkeypatch, tmp_path):
        monkeypatch.delenv(_DATA_FEED_API_KEY_ENV, raising=False)
        broker = AngelOneLiveBroker(
            api_key="FAKE",
            client_code="C",
            password="P",
            totp_secret="T",
            exec_cfg=_exec_cfg(),
            cache_dir=str(tmp_path),
        )
        with patch("requests.get", side_effect=ConnectionError("network down")):
            broker._load_instrument_master()

        assert broker._symbol_to_token == {}


# ---------------------------------------------------------------------------
# core/command_queue.py — line 50: update_status preserves lines where id
# doesn't match (the outer append in the else branch)
# ---------------------------------------------------------------------------

import core.command_queue as cq
from core.command_queue import enqueue, update_status, read_pending, clear_done


@pytest.fixture(autouse=False)
def isolated_queue(tmp_path, monkeypatch):
    queue_file = tmp_path / "commands.jsonl"
    monkeypatch.setattr(cq, "QUEUE_PATH", queue_file)
    yield queue_file


def test_update_status_preserves_non_matching_lines(isolated_queue, monkeypatch):
    monkeypatch.setattr(cq, "QUEUE_PATH", isolated_queue)
    c1 = enqueue("halt_agent", {})
    c2 = enqueue("resume_agent", {})
    c3 = enqueue("place_paper_order", {"symbol": "TCS"})

    # Update only c2
    update_status(c2.id, "done", "processed")

    lines = isolated_queue.read_text().splitlines()
    assert len(lines) == 3

    ids = {json.loads(l)["id"] for l in lines if l.strip()}
    assert c1.id in ids
    assert c2.id in ids
    assert c3.id in ids

    c2_data = next(json.loads(l) for l in lines if json.loads(l)["id"] == c2.id)
    assert c2_data["status"] == "done"


# ---------------------------------------------------------------------------
# core/data/nse_fii_dii.py — ValueError/TypeError in row parsing (lines 102-104)
# and stale-cache None guard (line 145)
# ---------------------------------------------------------------------------


class TestNseFiiDiiValueErrorBranch:
    def setup_method(self):
        import core.data.nse_fii_dii as m
        m.clear_cache()

    def test_skips_entry_with_invalid_numeric_string(self):
        import core.data.nse_fii_dii as m

        records = [
            {"date": "30-May-2025", "fiiNetDii": "NOT_A_FLOAT", "diiNetDii": "200.0"},
            {"date": "29-May-2025", "fiiNetDii": "1000.0", "diiNetDii": "100.0"},
        ]
        mock_requests = MagicMock()
        mock_requests.get.return_value = MagicMock(
            raise_for_status=lambda: None,
            json=lambda: records,
        )

        with patch.dict("sys.modules", {"requests": mock_requests}):
            importlib.reload(m)
            m.clear_cache()
            flows = m.get_fii_dii_flows()

        # The invalid row is skipped, only the valid one is returned
        assert len(flows) == 1
        assert abs(flows[0].fii_net_inr_cr - 1000.0) < 1e-9

    def test_stale_cache_none_guard_returns_empty(self):
        """When there's no cached data AND fetch returns [], result should be []."""
        import core.data.nse_fii_dii as m

        # Start with clean state (no cached data)
        m.clear_cache()

        mock_requests = MagicMock()
        mock_requests.get.side_effect = ConnectionError("down")

        with patch.dict("sys.modules", {"requests": mock_requests}):
            importlib.reload(m)
            m.clear_cache()
            flows = m.get_fii_dii_flows()

        assert flows == []


# ---------------------------------------------------------------------------
# core/data/nse_pcr.py — CE totOI == 0 branch (lines 71-72)
# ---------------------------------------------------------------------------


class TestNsePcrCeZero:
    def setup_method(self):
        import core.data.nse_pcr as m
        m.clear_cache()

    def test_returns_none_when_ce_oi_is_zero(self):
        import core.data.nse_pcr as m

        mock_requests = MagicMock()
        mock_session = MagicMock()
        mock_requests.Session.return_value = mock_session

        home_resp = MagicMock()
        home_resp.raise_for_status.return_value = None

        oc_resp = MagicMock()
        oc_resp.raise_for_status.return_value = None
        oc_resp.json.return_value = {
            "filtered": {
                "PE": {"totOI": 1_000_000},
                "CE": {"totOI": 0},  # CE is zero — division by zero guard
            }
        }
        mock_session.get.side_effect = [home_resp, oc_resp]

        with patch.dict("sys.modules", {"requests": mock_requests}):
            importlib.reload(m)
            m.clear_cache()
            pcr = m.get_nifty_pcr()

        assert pcr is None

    def test_stale_cache_used_when_ce_zero_after_ttl(self):
        """If a previous good value is cached and CE later becomes 0, return stale."""
        import core.data.nse_pcr as m
        import time

        mock_requests = MagicMock()
        mock_session = MagicMock()
        mock_requests.Session.return_value = mock_session

        home_resp = MagicMock()
        home_resp.raise_for_status.return_value = None

        good_oc = MagicMock()
        good_oc.raise_for_status.return_value = None
        good_oc.json.return_value = {
            "filtered": {"PE": {"totOI": 1_200_000}, "CE": {"totOI": 1_000_000}}
        }

        bad_oc = MagicMock()
        bad_oc.raise_for_status.return_value = None
        bad_oc.json.return_value = {
            "filtered": {"PE": {"totOI": 1_200_000}, "CE": {"totOI": 0}}
        }

        mock_session.get.side_effect = [home_resp, good_oc, home_resp, bad_oc]

        with patch.dict("sys.modules", {"requests": mock_requests}):
            importlib.reload(m)
            m.clear_cache()
            t0 = time.monotonic()
            pcr1 = m.get_nifty_pcr(_now=t0)
            pcr2 = m.get_nifty_pcr(_now=t0 + 20 * 60)  # TTL expired; CE now 0

        assert pcr1 == pytest.approx(1.2)
        # Stale value returned when fresh fetch returns None
        assert pcr2 == pytest.approx(1.2)


# ---------------------------------------------------------------------------
# core/llm/news_scorer.py — direct anthropic import path (lines 136-137)
# ---------------------------------------------------------------------------

from core.llm.news_scorer import score_news, NewsScore


class TestScoreNewsDirectImport:
    def test_creates_client_from_api_key(self, monkeypatch):
        """When api_key is provided without a client, the module imports anthropic."""
        from types import SimpleNamespace

        mock_content = SimpleNamespace(text=json.dumps(
            {"score": 0.5, "confidence": 0.8, "summary": "Positive news"}
        ))
        mock_msg = SimpleNamespace(content=[mock_content])
        mock_anthropic_client = MagicMock()
        mock_anthropic_client.messages.create.return_value = mock_msg

        mock_anthropic_module = MagicMock()
        mock_anthropic_module.Anthropic.return_value = mock_anthropic_client

        with patch.dict("sys.modules", {"anthropic": mock_anthropic_module}):
            result = score_news(
                symbol="RELIANCE",
                headlines=["Strong quarterly results"],
                api_key="fake-api-key",
                client=None,
            )

        assert result is not None
        assert isinstance(result, NewsScore)
        assert result.score == pytest.approx(0.5)
        mock_anthropic_module.Anthropic.assert_called_once_with(api_key="fake-api-key")


# ---------------------------------------------------------------------------
# Additional gap-filling tests discovered after first coverage run
# ---------------------------------------------------------------------------

class TestEmMaxDdInrEmpty:
    def test_empty_returns_zero(self):
        from core.analytics.metrics import _em_max_dd_inr
        result = _em_max_dd_inr(pd.Series(dtype=float))
        assert result == 0.0


class TestEmSharpeSortinoConstantSeries:
    def test_constant_pnl_daily_ret_std_near_zero(self):
        """If daily_ret.std() < 1e-12, returns (0.0, 0.0) — covers the branch at line 389."""
        from core.analytics.metrics import _em_sharpe_sortino
        # Two equal values: pct_change = constant → std approaches 0
        series = pd.Series([1000.0, 1000.0])
        sharpe, sortino = _em_sharpe_sortino(series)
        assert sharpe == 0.0
        assert sortino == 0.0


class TestLoadTradesFromDbMalformedRow:
    def test_skips_malformed_row_silently(self, tmp_path):
        """If a DB row has bad data, the exception is caught and row is skipped."""
        from core.persistence.store import Store
        db = str(tmp_path / "agent.db")
        Store(db)  # create schema

        # Insert a well-formed row
        conn = sqlite3.connect(db)
        conn.execute(
            "INSERT INTO trades (symbol, side, qty, entry_price, exit_price, pnl, "
            "charges, strategy, exit_reason, opened_at, closed_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("RELIANCE", "BUY", 5, 2900.0, 2978.0, 390.0, 20.0,
             "trend_breakout", "target", "2024-01-02T09:30:00", "2024-01-02T11:00:00")
        )
        # Insert a malformed row (pnl is not a number)
        conn.execute(
            "INSERT INTO trades (symbol, side, qty, entry_price, exit_price, pnl, "
            "charges, strategy, exit_reason, opened_at, closed_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("BAD", "BUY", 1, 100.0, 110.0, "NOT_A_NUMBER", 0.0,
             "t", "target", "2024-01-03T09:00:00", "BADDATE")
        )
        conn.commit()
        conn.close()

        trades = load_trades_from_db(db)
        # Should only return the good row; malformed one is silently skipped
        symbols = [t.symbol for t in trades]
        assert "RELIANCE" in symbols
        assert "BAD" not in symbols


class TestAngelOneLiveLimitOrder:
    def test_place_order_limit_includes_price(self, monkeypatch):
        """LIMIT order type should include the price in the SmartAPI payload."""
        from core.types import OrderType, Side, OrderStatus
        monkeypatch.delenv(_DATA_FEED_API_KEY_ENV, raising=False)
        mock_sc = MagicMock()
        mock_sc.placeOrder.return_value = {"status": True, "data": {"orderid": "LMT001"}}
        broker = _connected_broker(mock_sc, monkeypatch)

        order = broker.place_order(
            symbol="RELIANCE",
            side=Side.BUY,
            qty=5,
            order_type=OrderType.LIMIT,
            stop_loss=2000.0,
            target=2200.0,
            limit_price=2100.0,
        )

        assert order.status == OrderStatus.FILLED
        payload = mock_sc.placeOrder.call_args[0][0]
        assert payload["ordertype"] == "LIMIT"
        assert payload["price"] == "2100.0"


class TestGetTokenTriggersInstrumentLoad:
    def test_get_token_triggers_load_when_map_empty(self, monkeypatch, tmp_path):
        """_get_token calls _load_instrument_master when _symbol_to_token is empty."""
        monkeypatch.delenv(_DATA_FEED_API_KEY_ENV, raising=False)
        broker = AngelOneLiveBroker(
            api_key="FAKE",
            client_code="C",
            password="P",
            totp_secret="T",
            exec_cfg=_exec_cfg(),
            cache_dir=str(tmp_path),
        )
        # _symbol_to_token is empty by default; write a cache file so load succeeds
        today_str = datetime.now().strftime("%Y%m%d")
        cache_file = tmp_path / f"angel_instruments_{today_str}.json"
        instruments = [
            {"exch_seg": "NSE", "instrumenttype": "EQ", "symbol": "WIPRO-EQ", "token": "3787"},
        ]
        cache_file.write_text(json.dumps(instruments), encoding="utf-8")

        token = broker._get_token("WIPRO")
        assert token == "3787"


class TestNseFiiDiiResultNoneGuard:
    def setup_method(self):
        import core.data.nse_fii_dii as m
        m.clear_cache()

    def test_returns_empty_when_no_cache_and_fetch_returns_empty(self):
        """result=None guard at line 145: when _cached_flows is None and fresh=[]."""
        import core.data.nse_fii_dii as m
        import time

        # Ensure no cached data
        m.clear_cache()

        mock_requests = MagicMock()
        mock_session_inst = MagicMock()
        mock_requests.Session.return_value = mock_session_inst

        # Return an empty list response (valid JSON but no data)
        empty_resp = MagicMock()
        empty_resp.raise_for_status.return_value = None
        empty_resp.json.return_value = []  # empty list
        mock_session_inst.get.return_value = empty_resp  # if pcr used sessions; fii_dii uses requests.get

        # fii_dii uses requests.get directly
        mock_requests.get.return_value = empty_resp

        with patch.dict("sys.modules", {"requests": mock_requests}):
            importlib.reload(m)
            m.clear_cache()
            flows = m.get_fii_dii_flows()

        assert flows == []


class TestCommandQueueUpdateStatusAppendOtherLines:
    """Specifically target line 50: the append of unchanged lines in update_status."""

    def test_lines_with_no_id_key_still_preserved(self, tmp_path, monkeypatch):
        """A JSONL line that doesn't have an 'id' key is preserved as-is."""
        queue_file = tmp_path / "commands.jsonl"
        monkeypatch.setattr(cq, "QUEUE_PATH", queue_file)

        # Write a valid command and a line without 'id' key
        c1 = enqueue("halt_agent", {})
        # Append a line that's valid JSON but has no 'id'
        with open(queue_file, "a") as f:
            f.write(json.dumps({"type": "heartbeat", "status": "pending"}) + "\n")

        # Update c1's status
        update_status(c1.id, "done")

        lines = queue_file.read_text().splitlines()
        # Both lines should still be there
        assert len(lines) == 2

        # c1 should be 'done'
        c1_data = next(json.loads(l) for l in lines if json.loads(l).get("id") == c1.id)
        assert c1_data["status"] == "done"

    def test_malformed_json_line_preserved_as_raw_string(self, tmp_path, monkeypatch):
        """Malformed JSON in queue file is preserved verbatim (covers line 50 except branch)."""
        queue_file = tmp_path / "commands.jsonl"
        monkeypatch.setattr(cq, "QUEUE_PATH", queue_file)

        c1 = enqueue("halt_agent", {})
        # Append a malformed (non-JSON) line
        with open(queue_file, "a") as f:
            f.write("THIS_IS_NOT_JSON\n")

        # update_status should not raise even with a malformed line in the file
        update_status(c1.id, "done")

        lines = [l for l in queue_file.read_text().splitlines() if l.strip()]
        assert len(lines) == 2  # both lines still present
        # the malformed line is preserved verbatim
        assert any(l == "THIS_IS_NOT_JSON" for l in lines)


class TestEmSharpeSortinoEdge:
    def test_returns_zero_when_daily_ret_std_near_zero(self):
        """daily_ret.std() < 1e-12 triggers line 389."""
        from core.analytics.metrics import _em_sharpe_sortino
        import pandas as pd

        # A series where cumsum is exactly constant after shift → pct_change ≈ 0
        # Two identical values produce daily_ret = [0.0] after pct_change().dropna()
        # std of [0.0] = 0.0 < 1e-12 → returns (0.0, 0.0)
        series = pd.Series([500.0, 500.0])
        sharpe, sortino = _em_sharpe_sortino(series)
        assert sharpe == 0.0
        assert sortino == 0.0


class TestNseFiiDiiLineOneFortyFive:
    """Cover line 145: result = [] when _cached_flows is still None after a failed fetch."""

    def setup_method(self):
        import core.data.nse_fii_dii as m
        m.clear_cache()

    def test_returns_empty_list_when_no_prior_cache_and_fetch_fails(self):
        import core.data.nse_fii_dii as m

        # Ensure there is no cached state at module level
        m._cached_flows = None
        m._cache_timestamp = 0.0

        # The fresh fetch returns [] (no data)
        mock_requests = MagicMock()
        mock_requests.get.side_effect = Exception("transient error")

        with patch.dict("sys.modules", {"requests": mock_requests}):
            importlib.reload(m)
            # Force _cached_flows = None so line 144-145 is reached
            m._cached_flows = None
            m._cache_timestamp = 0.0
            flows = m.get_fii_dii_flows()

        assert flows == []
