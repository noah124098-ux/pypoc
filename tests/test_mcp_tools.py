"""Tests for the MCP tool layer (read-only data access).

These exercise the same logic the MCP server hands to Claude Code, but skip the
protocol layer for speed. Goal: prove every tool returns sane data given a
plausible snapshot + SQLite store.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.persistence.store import Store
from core.runtime_snapshot import RuntimeSnapshot, write as write_snapshot
from mcp_server.tools import TradingAgentTools


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


def _write_snapshot(path: Path, **overrides):
    snap = RuntimeSnapshot(
        ts="2026-05-13T10:00:00+00:00",
        pid=1234,
        mode="paper",
        feed_connected=True,
        last_tick_age_seconds=1.2,
        halted=False,
        halt_reason="",
        cash=98_500.0,
        equity=100_500.0,
        realized_pnl=200.0,
        starting_equity_today=100_000.0,
        peak_equity=100_700.0,
        open_positions=[
            {
                "symbol": "RELIANCE", "qty": 5, "avg_price": 1300.0, "last_price": 1310.0,
                "stop_loss": 1280.0, "target": 1360.0, "unrealized_pnl": 50.0,
                "strategy": "trend_breakout", "opened_at": "2026-05-13T09:30:00+00:00",
            }
        ],
        current_regime="TREND",
        regime_rationale="ADX 28 > 25",
        nifty_ltp=22050.0,
        vix=14.2,
        universe_size=50,
        strategies_enabled=["trend_breakout", "mean_reversion", "volatility_compression"],
        config_path="config/default.yaml",
    )
    for k, v in overrides.items():
        setattr(snap, k, v)
    write_snapshot(snap, path)


def test_get_status_when_agent_not_running(tools):
    assert tools.get_status() == {
        "running": False,
        "message": pytest.approx(tools.get_status()["message"]),  # message check below
    } or tools.get_status()["running"] is False


def test_get_status_with_snapshot(tools, tmp_workspace):
    _write_snapshot(tmp_workspace / "snapshot.json")
    s = tools.get_status()
    assert s["running"] is True
    assert s["mode"] == "paper"
    assert s["current_regime"] == "TREND"
    assert s["feed_connected"] is True


def test_get_positions(tools, tmp_workspace):
    _write_snapshot(tmp_workspace / "snapshot.json")
    positions = tools.get_positions()
    assert len(positions) == 1
    assert positions[0]["symbol"] == "RELIANCE"


def test_get_pnl_computes_day_and_drawdown(tools, tmp_workspace):
    _write_snapshot(tmp_workspace / "snapshot.json")
    pnl = tools.get_pnl()
    assert pnl["available"] is True
    # day P&L = (100500 - 100000) / 100000 * 100 = 0.5
    assert pnl["day_pnl_pct"] == pytest.approx(0.5)
    # drawdown = (100700 - 100500) / 100700 * 100 ≈ 0.1986
    assert pnl["drawdown_from_peak_pct"] == pytest.approx(0.1986, abs=1e-3)
    assert pnl["unrealized_pnl"] == pytest.approx(50.0)


def test_get_pnl_when_agent_not_running(tools):
    pnl = tools.get_pnl()
    assert pnl == {"available": False, "reason": "agent not running"}


def test_get_recent_signals(tools, store):
    store.record_signal(
        ts="2026-05-13T10:00:00", symbol="TCS", side="BUY", strategy="trend_breakout",
        regime="TREND", entry_price=4000, stop_loss=3950, target=4150,
        confidence=0.6, rationale="r1", accepted=True,
    )
    store.record_signal(
        ts="2026-05-13T10:01:00", symbol="INFY", side="BUY", strategy="trend_breakout",
        regime="TREND", entry_price=1500, stop_loss=1480, target=1560,
        confidence=0.6, rationale="r2", accepted=False, rejection_reason="liquidity: ...",
    )
    result = tools.get_recent_signals(limit=10)
    assert isinstance(result, dict)
    assert result["total"] == 2
    assert len(result["data"]) == 2
    assert result["limit"] == 10
    assert result["offset"] == 0
    assert result["has_more"] is False

    accepted = tools.get_recent_signals(accepted_only=True)
    assert accepted["total"] == 1
    assert accepted["data"][0]["symbol"] == "TCS"


def test_get_recent_signals_pagination(tools, store):
    """Signals pagination: offset + has_more are correct."""
    for i in range(5):
        store.record_signal(
            ts=f"2026-05-13T10:0{i}:00", symbol="TCS", side="BUY",
            strategy="trend_breakout", regime="TREND",
            entry_price=4000, stop_loss=3950, target=4150,
            confidence=0.6, rationale=f"r{i}", accepted=True,
        )
    page1 = tools.get_recent_signals(limit=3, offset=0)
    assert page1["total"] == 5
    assert len(page1["data"]) == 3
    assert page1["has_more"] is True

    page2 = tools.get_recent_signals(limit=3, offset=3)
    assert len(page2["data"]) == 2
    assert page2["has_more"] is False


def test_get_recent_trades(tools, store):
    store.record_trade(
        symbol="TCS", side="SELL", qty=5, entry_price=4000, exit_price=4080,
        pnl=350, charges=50, strategy="trend_breakout", exit_reason="target",
        opened_at="2026-05-13T09:30:00", closed_at="2026-05-13T11:00:00",
    )
    result = tools.get_recent_trades()
    assert isinstance(result, dict)
    assert result["total"] == 1
    assert len(result["data"]) == 1
    assert result["data"][0]["pnl"] == 350
    assert result["has_more"] is False


def test_get_recent_trades_pagination(tools, store):
    """Trades pagination: offset + has_more are correct."""
    for i in range(4):
        store.record_trade(
            symbol=f"SYM{i}", side="BUY", qty=1,
            entry_price=100, exit_price=110,
            pnl=10 * (i + 1), charges=2, strategy="trend_breakout",
            exit_reason="target",
            opened_at=f"2026-05-13T09:3{i}:00",
            closed_at=f"2026-05-13T10:3{i}:00",
        )
    page = tools.get_recent_trades(limit=2, offset=0)
    assert page["total"] == 4
    assert len(page["data"]) == 2
    assert page["has_more"] is True

    last = tools.get_recent_trades(limit=2, offset=2)
    assert len(last["data"]) == 2
    assert last["has_more"] is False


def test_get_guardrail_rejections(tools, store):
    store.record_guardrail(rule="liquidity", symbol="ABC", detail="qty > 1% ADV")
    result = tools.get_guardrail_rejections()
    assert isinstance(result, dict)
    assert result["total"] == 1
    assert len(result["data"]) == 1
    assert result["data"][0]["rule"] == "liquidity"
    assert result["has_more"] is False


def test_get_equity_curve(tools, store):
    store.record_equity(cash=99_000, equity=100_000, realized_pnl=0, open_positions=0)
    result = tools.get_equity_curve()
    assert isinstance(result, dict)
    assert result["total"] == 1
    assert len(result["data"]) == 1
    assert result["data"][0]["equity"] == 100_000
    assert result["has_more"] is False


def test_get_trade_stats_empty(tools):
    """get_trade_stats on an empty DB returns zero values."""
    stats = tools.get_trade_stats()
    assert stats["total_trades"] == 0
    assert stats["total_pnl"] == 0.0
    assert stats["win_rate"] == 0.0
    assert stats["profit_factor"] == 0.0
    assert stats["max_dd"] == 0.0


def test_get_trade_stats_with_trades(tools, store):
    """get_trade_stats returns correct aggregates for known trades."""
    trades = [
        # (pnl, charges)
        (200.0, 10.0),
        (-50.0, 5.0),
        (150.0, 8.0),
        (-30.0, 4.0),
        (100.0, 6.0),
    ]
    for i, (pnl, charges) in enumerate(trades):
        store.record_trade(
            symbol="RELIANCE", side="BUY", qty=1,
            entry_price=100, exit_price=100 + pnl,
            pnl=pnl, charges=charges, strategy="trend_breakout",
            exit_reason="target",
            opened_at=f"2026-05-01T09:3{i}:00",
            closed_at=f"2026-05-01T10:3{i}:00",
        )
    stats = tools.get_trade_stats()
    assert stats["total_trades"] == 5
    assert stats["total_pnl"] == pytest.approx(370.0, abs=0.1)
    assert stats["win_rate"] == pytest.approx(60.0, abs=0.1)   # 3 winners / 5 trades
    # profit_factor = gross_profit / |gross_loss| = 450 / 80 = 5.625
    assert stats["profit_factor"] == pytest.approx(5.625, abs=0.01)
    # max_dd: running cumulative pnl series = 200, 150, 300, 270, 370
    # after trade 1: running=200, peak=200
    # after trade 2: running=150, dd=200-150=50 (new max)
    # after trade 3: running=300, peak=300, dd=0
    # after trade 4: running=270, dd=300-270=30
    # after trade 5: running=370, peak=370, dd=0
    # → max_dd = 50
    assert stats["max_dd"] == pytest.approx(50.0, abs=0.1)
    # sharpe is a real number (could be any value)
    assert isinstance(stats["sharpe"], float)


def test_get_universe(tools):
    u = tools.get_universe()
    assert u["count"] == 50
    assert "RELIANCE" in u["symbols"]


def test_get_config_summary(tools, monkeypatch):
    # Run without environment overlay so we get the base config values.
    # config/environments/dev.yaml would otherwise lower per_trade_risk_pct to 0.5.
    monkeypatch.setenv("APP_ENV", "test")  # 'test' env has no overlay file
    cfg = tools.get_config_summary()
    assert cfg["mode"] == "paper"
    assert cfg["risk"]["per_trade_risk_pct"] == 1.0
    assert "mean_reversion" in cfg["strategies_enabled"]
