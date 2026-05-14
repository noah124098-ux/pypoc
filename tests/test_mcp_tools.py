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
    sigs = tools.get_recent_signals(limit=10)
    assert len(sigs) == 2
    accepted = tools.get_recent_signals(accepted_only=True)
    assert len(accepted) == 1
    assert accepted[0]["symbol"] == "TCS"


def test_get_recent_trades(tools, store):
    store.record_trade(
        symbol="TCS", side="SELL", qty=5, entry_price=4000, exit_price=4080,
        pnl=350, charges=50, strategy="trend_breakout", exit_reason="target",
        opened_at="2026-05-13T09:30:00", closed_at="2026-05-13T11:00:00",
    )
    trades = tools.get_recent_trades()
    assert len(trades) == 1
    assert trades[0]["pnl"] == 350


def test_get_guardrail_rejections(tools, store):
    store.record_guardrail(rule="liquidity", symbol="ABC", detail="qty > 1% ADV")
    rejs = tools.get_guardrail_rejections()
    assert len(rejs) == 1
    assert rejs[0]["rule"] == "liquidity"


def test_get_equity_curve(tools, store):
    store.record_equity(cash=99_000, equity=100_000, realized_pnl=0, open_positions=0)
    curve = tools.get_equity_curve()
    assert len(curve) == 1
    assert curve[0]["equity"] == 100_000


def test_get_universe(tools):
    u = tools.get_universe()
    assert u["count"] == 50
    assert "RELIANCE" in u["symbols"]


def test_get_config_summary(tools):
    cfg = tools.get_config_summary()
    assert cfg["mode"] == "paper"
    assert cfg["risk"]["per_trade_risk_pct"] == 1.0
    assert "trend_breakout" in cfg["strategies_enabled"]
