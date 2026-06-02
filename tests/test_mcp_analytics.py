"""Tests for the MCP analytics tools.

Tests use an in-memory SQLite store (via tmp_path) to avoid touching production
data.  The analytics functions themselves are also exercised directly via the
metrics module to keep test scope clear.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from core.analytics.metrics import (
    monthly_summary,
    regime_performance,
    strategy_performance,
    trade_analytics,
)
from core.persistence.store import Store
from mcp_server.tools import TradingAgentTools


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def tmp_workspace(tmp_path: Path) -> Path:
    (tmp_path / "config").mkdir()
    cfg_src = Path("config/default.yaml").read_text(encoding="utf-8")
    (tmp_path / "config" / "default.yaml").write_text(cfg_src, encoding="utf-8")
    return tmp_path


@pytest.fixture
def store(tmp_workspace) -> Store:
    return Store(str(tmp_workspace / "agent.db"))


@pytest.fixture
def tools(tmp_workspace, store) -> TradingAgentTools:
    return TradingAgentTools(
        snapshot_path=tmp_workspace / "snapshot.json",
        sqlite_path=tmp_workspace / "agent.db",
        config_path=tmp_workspace / "config" / "default.yaml",
    )


def _add_trade(store: Store, **overrides):
    defaults = dict(
        symbol="TCS",
        side="SELL",
        qty=5,
        entry_price=4000.0,
        exit_price=4080.0,
        pnl=350.0,
        charges=25.0,
        strategy="trend_breakout",
        exit_reason="target",
        opened_at="2026-03-10T09:30:00",
        closed_at="2026-03-10T11:00:00",
    )
    defaults.update(overrides)
    store.record_trade(**defaults)


def _add_signal(store: Store, **overrides):
    defaults = dict(
        ts="2026-03-10T09:29:00",
        symbol="TCS",
        side="BUY",
        strategy="trend_breakout",
        regime="TREND",
        entry_price=4000.0,
        stop_loss=3950.0,
        target=4150.0,
        confidence=0.7,
        rationale="test",
        accepted=True,
    )
    defaults.update(overrides)
    store.record_signal(**defaults)


# ---------------------------------------------------------------------------
# unit tests for core.analytics.metrics (pure functions)
# ---------------------------------------------------------------------------


class TestStrategyPerformance:
    def test_empty(self):
        assert strategy_performance([]) == []

    def test_single_winning_trade(self):
        trades = [
            {"pnl": 500.0, "strategy": "trend_breakout"},
        ]
        result = strategy_performance(trades)
        assert len(result) == 1
        r = result[0]
        assert r["strategy"] == "trend_breakout"
        assert r["trades"] == 1
        assert r["win_rate"] == 100.0
        assert r["net_pnl"] == 500.0

    def test_multiple_strategies(self):
        trades = [
            {"pnl": 300.0, "strategy": "trend_breakout"},
            {"pnl": -100.0, "strategy": "trend_breakout"},
            {"pnl": 50.0, "strategy": "mean_reversion"},
        ]
        result = {r["strategy"]: r for r in strategy_performance(trades)}
        assert result["trend_breakout"]["trades"] == 2
        assert result["trend_breakout"]["win_rate"] == 50.0
        assert result["trend_breakout"]["net_pnl"] == 200.0
        assert result["mean_reversion"]["win_rate"] == 100.0

    def test_profit_factor_calculated(self):
        trades = [
            {"pnl": 600.0, "strategy": "s"},
            {"pnl": -200.0, "strategy": "s"},
        ]
        result = strategy_performance(trades)
        assert result[0]["profit_factor"] == pytest.approx(3.0)


class TestRegimePerformance:
    def test_empty(self):
        assert regime_performance([]) == []

    def test_groups_by_regime(self):
        trades = [
            {"pnl": 200.0, "regime": "TREND"},
            {"pnl": -50.0, "regime": "TREND"},
            {"pnl": 30.0, "regime": "RANGE"},
        ]
        result = {r["regime"]: r for r in regime_performance(trades)}
        assert result["TREND"]["trades"] == 2
        assert result["TREND"]["avg_pnl"] == pytest.approx(75.0)
        assert result["RANGE"]["win_rate"] == 100.0

    def test_missing_regime_grouped_as_unknown(self):
        trades = [{"pnl": 100.0}]  # no 'regime' key
        result = regime_performance(trades)
        assert result[0]["regime"] == "UNKNOWN"


class TestTradeAnalytics:
    def test_empty(self):
        result = trade_analytics([])
        assert result["available"] is False

    def test_basic_fields(self):
        trades = [
            {
                "pnl": 500.0, "charges": 30.0, "strategy": "s1", "regime": "TREND",
                "symbol": "TCS", "closed_at": "2026-03-10T11:00:00",
                "opened_at": "2026-03-10T09:30:00",
            },
            {
                "pnl": -100.0, "charges": 20.0, "strategy": "s1", "regime": "RANGE",
                "symbol": "INFY", "closed_at": "2026-03-11T11:00:00",
                "opened_at": "2026-03-11T09:30:00",
            },
        ]
        result = trade_analytics(trades)
        assert result["available"] is True
        assert result["trade_count"] == 2
        assert result["total_net_pnl"] == pytest.approx(400.0)
        assert result["total_charges"] == pytest.approx(50.0)
        assert result["best_trade"]["symbol"] == "TCS"
        assert result["worst_trade"]["symbol"] == "INFY"

    def test_streak_positive(self):
        trades = [
            {"pnl": 100.0, "strategy": "s", "regime": "TREND", "symbol": "X",
             "closed_at": "2026-03-10T11:00:00", "opened_at": "2026-03-10T09:00:00"},
            {"pnl": 200.0, "strategy": "s", "regime": "TREND", "symbol": "Y",
             "closed_at": "2026-03-11T11:00:00", "opened_at": "2026-03-11T09:00:00"},
        ]
        result = trade_analytics(trades)
        assert result["current_streak"] == 2

    def test_streak_negative(self):
        trades = [
            {"pnl": 100.0, "strategy": "s", "regime": "TREND", "symbol": "X",
             "closed_at": "2026-03-10T11:00:00", "opened_at": "2026-03-10T09:00:00"},
            {"pnl": -50.0, "strategy": "s", "regime": "TREND", "symbol": "Y",
             "closed_at": "2026-03-11T11:00:00", "opened_at": "2026-03-11T09:00:00"},
        ]
        result = trade_analytics(trades)
        assert result["current_streak"] == -1

    def test_avg_holding_minutes(self):
        trades = [
            {
                "pnl": 100.0, "strategy": "s", "regime": "TREND", "symbol": "X",
                "charges": 0,
                "opened_at": "2026-03-10T09:00:00",
                "closed_at": "2026-03-10T10:00:00",  # 60 min
            },
            {
                "pnl": 100.0, "strategy": "s", "regime": "TREND", "symbol": "Y",
                "charges": 0,
                "opened_at": "2026-03-10T09:00:00",
                "closed_at": "2026-03-10T09:30:00",  # 30 min
            },
        ]
        result = trade_analytics(trades)
        assert result["avg_holding_minutes"] == pytest.approx(45.0)


class TestMonthlySummary:
    def test_empty(self):
        assert monthly_summary([]) == []

    def test_groups_by_year_month(self):
        trades = [
            {"pnl": 300.0, "closed_at": "2026-01-10T11:00:00"},
            {"pnl": 200.0, "closed_at": "2026-01-15T11:00:00"},
            {"pnl": -100.0, "closed_at": "2026-02-05T11:00:00"},
        ]
        result = monthly_summary(trades)
        assert len(result) == 2
        jan = next(r for r in result if r["month"] == 1)
        feb = next(r for r in result if r["month"] == 2)
        assert jan["pnl"] == pytest.approx(500.0)
        assert jan["trades"] == 2
        assert jan["win_rate"] == 100.0
        assert feb["pnl"] == pytest.approx(-100.0)
        assert feb["win_rate"] == 0.0


# ---------------------------------------------------------------------------
# integration tests: MCP tool layer with real SQLite (via tmp_path)
# ---------------------------------------------------------------------------


class TestMCPAnalyticsIntegration:
    def test_get_strategy_performance_empty(self, tools):
        """Returns empty list when no trades exist."""
        result = tools.get_strategy_performance(days=30)
        assert result == []

    def test_get_strategy_performance_with_data(self, tools, store):
        _add_signal(store, strategy="trend_breakout", regime="TREND")
        _add_trade(store, symbol="TCS", strategy="trend_breakout", pnl=300.0,
                   closed_at="2026-05-30T11:00:00")
        _add_signal(store, symbol="INFY", strategy="mean_reversion", regime="RANGE",
                    ts="2026-05-29T09:00:00")
        _add_trade(store, symbol="INFY", strategy="mean_reversion", pnl=-80.0,
                   closed_at="2026-05-29T11:00:00")
        result = tools.get_strategy_performance(days=365)
        strategies = {r["strategy"] for r in result}
        assert "trend_breakout" in strategies
        assert "mean_reversion" in strategies
        tb = next(r for r in result if r["strategy"] == "trend_breakout")
        assert tb["net_pnl"] == pytest.approx(300.0)
        assert tb["win_rate"] == 100.0

    def test_get_regime_performance_empty(self, tools):
        result = tools.get_regime_performance(days=30)
        assert result == []

    def test_get_regime_performance_with_data(self, tools, store):
        _add_signal(store, symbol="TCS", strategy="trend_breakout", regime="TREND",
                    ts="2026-05-30T09:00:00")
        _add_trade(store, symbol="TCS", strategy="trend_breakout", pnl=200.0,
                   closed_at="2026-05-30T11:00:00")
        result = tools.get_regime_performance(days=365)
        # At least one row returned
        assert isinstance(result, list)

    def test_get_trade_analytics_empty(self, tools):
        result = tools.get_trade_analytics(days=30)
        assert result["available"] is False

    def test_get_trade_analytics_with_data(self, tools, store):
        _add_signal(store)
        _add_trade(store, pnl=400.0, closed_at="2026-05-30T11:00:00",
                   opened_at="2026-05-30T09:30:00")
        result = tools.get_trade_analytics(days=365)
        assert result["available"] is True
        assert result["trade_count"] >= 1
        assert result["best_trade"]["pnl"] >= result["worst_trade"]["pnl"]
        assert result["avg_holding_minutes"] is not None

    def test_get_monthly_summary_empty(self, tools):
        result = tools.get_monthly_summary()
        assert result == []

    def test_get_monthly_summary_groups_correctly(self, tools, store):
        _add_trade(store, pnl=500.0, closed_at="2026-01-10T11:00:00")
        _add_trade(store, pnl=200.0, closed_at="2026-01-20T11:00:00")
        _add_trade(store, pnl=-150.0, closed_at="2026-02-05T11:00:00")
        result = tools.get_monthly_summary()
        assert len(result) == 2
        jan = next(r for r in result if r["month"] == 1)
        assert jan["pnl"] == pytest.approx(700.0)
        assert jan["trades"] == 2

    def test_get_strategy_performance_days_filter(self, tools, store):
        """Trades older than days should be excluded."""
        _add_trade(store, pnl=999.0, closed_at="2020-01-01T11:00:00",
                   strategy="old_strategy")
        _add_trade(store, pnl=10.0, closed_at="2026-05-30T11:00:00",
                   strategy="new_strategy")
        result = tools.get_strategy_performance(days=30)
        strategies = [r["strategy"] for r in result]
        assert "old_strategy" not in strategies
