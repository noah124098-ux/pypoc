"""Tests for config hot-reload functionality.

Covers:
- reload_settings() returns a valid Settings object
- Orchestrator._last_config_reload initialises to 0.0
- tick_lifecycle() performs a config reload after the 60-second window
- Config reload updates only the four safe risk fields on self.s.risk
- A new Guardrails instance is created after reload
- Reload errors are swallowed (fail-open) and _last_config_reload is still advanced
- reload_config command via _process_command_queue applies changes immediately
"""
from __future__ import annotations

import json
import time
import uuid
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch, call

import pytest

from core.config import (
    BacktestGateCfg, CapitalCfg, DataCfg, ExecutionCfg, LLMCfg, LoggingCfg,
    MarketCfg, NotificationsCfg, PersistenceCfg, RegimeCfg, RiskCfg, Settings,
    UniverseCfg, reload_settings, load_settings,
)
from core.execution.orchestrator import Orchestrator


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _settings(per_trade_risk_pct: float = 1.0) -> Settings:
    return Settings(
        mode="paper",
        timezone="Asia/Kolkata",
        capital=CapitalCfg(initial_inr=500_000, currency="INR"),
        market=MarketCfg(
            exchange="NSE", segment="EQ",
            trading_start="09:15", trading_end="15:30",
            no_new_entries_after="14:45", intraday_squareoff_at="15:15",
        ),
        universe=UniverseCfg(source="nifty50", symbols=["RELIANCE"]),
        risk=RiskCfg(
            per_trade_risk_pct=per_trade_risk_pct,
            max_position_pct=20.0,
            max_open_positions=5,
            daily_loss_circuit_pct=3.0,
            drawdown_circuit_pct=10.0,
            liquidity_max_pct_of_adv=10.0,
            max_spread_pct=0.3,
            black_swan_nifty_move_pct=2.0,
            black_swan_vix_jump_pct=20.0,
            stale_tick_seconds=10,
        ),
        regime=RegimeCfg(
            adx_period=14, adx_trend_threshold=20.0,
            bb_width_range_threshold=0.04, vix_volatile_threshold=20.0,
        ),
        strategies={"trend_breakout": {"enabled": False}},
        execution=ExecutionCfg(
            slippage_bps=5, brokerage_per_order_inr=20,
            stt_pct=0.025, exchange_txn_pct=0.00345, gst_pct=18.0,
            signal_cooldown_minutes=30,
        ),
        backtest_gate=BacktestGateCfg(
            min_sharpe=1.2, max_drawdown_pct=15.0, min_win_rate=45.0,
            min_profit_factor=1.5, min_trades=100, walk_forward_years=3,
        ),
        data=DataCfg(primary_feed="angelone", reconnect_max_attempts=5, reconnect_backoff_seconds=2),
        persistence=PersistenceCfg(sqlite_path="data/agent.db", tick_parquet_dir="data/ticks"),
        llm=LLMCfg(
            eod_reviewer_model="claude-opus-4-7",
            news_scorer_model="claude-haiku-4-5-20251001",
            enable_eod_review=False, auto_apply_within_safe_bounds=False,
        ),
        notifications=NotificationsCfg(
            telegram_enabled=False, email_enabled=False,
            dashboard_enabled=False, dashboard_port=8501,
        ),
        logging=LoggingCfg(level="INFO", file="logs/agent.log", json_log_file="logs/events.jsonl"),
    )


def _make_orchestrator(tmp_path: Path, settings: Settings | None = None) -> Orchestrator:
    s = settings or _settings()
    broker = MagicMock()
    broker.equity.return_value = 500_000.0
    broker.cash.return_value = 500_000.0
    broker.get_positions.return_value = []
    broker.realized_pnl = 0.0

    feed = MagicMock()
    feed.last_tick_age_seconds.return_value = 0.5
    feed.is_connected.return_value = True

    store = MagicMock()
    store.load_daily_state.return_value = None  # take fresh circuit-baseline path
    events = MagicMock()

    with patch("core.execution.orchestrator.write_snapshot"), \
         patch("core.execution.orchestrator.get_vix", return_value=None), \
         patch("core.execution.orchestrator.get_nifty_pcr", return_value=None):
        orch = Orchestrator(settings=s, feed=feed, broker=broker, store=store, events=events)

    orch.broker = broker
    orch.store = store
    return orch


# ---------------------------------------------------------------------------
# 1. reload_settings() returns a valid Settings object
# ---------------------------------------------------------------------------

def test_reload_settings_returns_settings_object():
    """reload_settings() must return a fully-populated Settings instance."""
    s = reload_settings("config/default.yaml")
    assert isinstance(s, Settings)
    assert s.risk.per_trade_risk_pct > 0
    assert s.risk.max_open_positions > 0


def test_reload_settings_matches_load_settings():
    """reload_settings and load_settings must produce identical results."""
    s1 = load_settings("config/default.yaml")
    s2 = reload_settings("config/default.yaml")
    assert s1.risk.per_trade_risk_pct == s2.risk.per_trade_risk_pct
    assert s1.risk.max_open_positions == s2.risk.max_open_positions
    assert s1.risk.daily_loss_circuit_pct == s2.risk.daily_loss_circuit_pct
    assert s1.risk.drawdown_circuit_pct == s2.risk.drawdown_circuit_pct


def test_reload_settings_raises_on_missing_file():
    """reload_settings() must raise when the YAML file does not exist."""
    with pytest.raises(Exception):
        reload_settings("config/nonexistent_totally_missing.yaml")


# ---------------------------------------------------------------------------
# 2. Orchestrator initialises _last_config_reload to 0.0
# ---------------------------------------------------------------------------

def test_orchestrator_has_last_config_reload_attr(tmp_path):
    orch = _make_orchestrator(tmp_path)
    assert hasattr(orch, "_last_config_reload")
    assert orch._last_config_reload == 0.0


# ---------------------------------------------------------------------------
# 3. tick_lifecycle calls reload_settings after the 60-second window
# ---------------------------------------------------------------------------

def test_tick_lifecycle_reloads_config_after_60s(tmp_path):
    """With _last_config_reload far in the past, tick_lifecycle must reload."""
    orch = _make_orchestrator(tmp_path)
    # Force the reload window to be open
    orch._last_config_reload = time.time() - 120.0

    original_guardrails = orch.guardrails

    with patch("core.execution.orchestrator.write_snapshot"), \
         patch("core.execution.orchestrator.get_vix", return_value=None), \
         patch("core.execution.orchestrator.reload_settings", return_value=_settings()) as mock_reload, \
         patch("core.execution.orchestrator.get_nifty_pcr", return_value=None):
        orch.tick_lifecycle()

    mock_reload.assert_called_once()
    # _last_config_reload must have been updated to (approximately) now
    assert time.time() - orch._last_config_reload < 5.0


def test_tick_lifecycle_skips_reload_within_60s(tmp_path):
    """When _last_config_reload is recent, tick_lifecycle must NOT reload."""
    orch = _make_orchestrator(tmp_path)
    orch._last_config_reload = time.time()  # just reloaded

    with patch("core.execution.orchestrator.write_snapshot"), \
         patch("core.execution.orchestrator.get_vix", return_value=None), \
         patch("core.execution.orchestrator.reload_settings") as mock_reload, \
         patch("core.execution.orchestrator.get_nifty_pcr", return_value=None):
        orch.tick_lifecycle()

    mock_reload.assert_not_called()


# ---------------------------------------------------------------------------
# 4. Config reload updates only the four safe risk fields
# ---------------------------------------------------------------------------

def test_config_reload_updates_risk_fields(tmp_path):
    """After a reload, per_trade_risk_pct and the three circuit fields are updated."""
    orch = _make_orchestrator(tmp_path, settings=_settings(per_trade_risk_pct=1.0))
    orch._last_config_reload = time.time() - 120.0

    new_s = _settings(per_trade_risk_pct=2.0)
    new_s.risk.max_open_positions = 3
    new_s.risk.daily_loss_circuit_pct = 5.0
    new_s.risk.drawdown_circuit_pct = 8.0

    with patch("core.execution.orchestrator.write_snapshot"), \
         patch("core.execution.orchestrator.get_vix", return_value=None), \
         patch("core.execution.orchestrator.reload_settings", return_value=new_s), \
         patch("core.execution.orchestrator.get_nifty_pcr", return_value=None):
        orch.tick_lifecycle()

    assert orch.s.risk.per_trade_risk_pct == 2.0
    assert orch.s.risk.max_open_positions == 3
    assert orch.s.risk.daily_loss_circuit_pct == 5.0
    assert orch.s.risk.drawdown_circuit_pct == 8.0


def test_config_reload_does_not_change_universe(tmp_path):
    """Universe is NOT reloaded — the orchestrator list of symbols is unchanged."""
    original_settings = _settings()
    orch = _make_orchestrator(tmp_path, settings=original_settings)
    original_symbols = list(orch.symbols)
    orch._last_config_reload = time.time() - 120.0

    with patch("core.execution.orchestrator.write_snapshot"), \
         patch("core.execution.orchestrator.get_vix", return_value=None), \
         patch("core.execution.orchestrator.reload_settings", return_value=_settings()), \
         patch("core.execution.orchestrator.get_nifty_pcr", return_value=None):
        orch.tick_lifecycle()

    # symbols list must be unchanged
    assert orch.symbols == original_symbols


# ---------------------------------------------------------------------------
# 5. A new Guardrails instance is created after reload
# ---------------------------------------------------------------------------

def test_config_reload_creates_new_guardrails(tmp_path):
    """After reload, self.guardrails must be a fresh instance."""
    orch = _make_orchestrator(tmp_path)
    orch._last_config_reload = time.time() - 120.0
    old_guardrails_id = id(orch.guardrails)

    with patch("core.execution.orchestrator.write_snapshot"), \
         patch("core.execution.orchestrator.get_vix", return_value=None), \
         patch("core.execution.orchestrator.reload_settings", return_value=_settings()), \
         patch("core.execution.orchestrator.get_nifty_pcr", return_value=None):
        orch.tick_lifecycle()

    assert id(orch.guardrails) != old_guardrails_id


# ---------------------------------------------------------------------------
# 6. Reload errors are swallowed (fail-open) and timer is still advanced
# ---------------------------------------------------------------------------

def test_config_reload_failure_is_swallowed(tmp_path):
    """If reload_settings raises, tick_lifecycle must not propagate the error."""
    orch = _make_orchestrator(tmp_path)
    orch._last_config_reload = time.time() - 120.0

    with patch("core.execution.orchestrator.write_snapshot"), \
         patch("core.execution.orchestrator.get_vix", return_value=None), \
         patch("core.execution.orchestrator.reload_settings", side_effect=OSError("disk read error")), \
         patch("core.execution.orchestrator.get_nifty_pcr", return_value=None):
        orch.tick_lifecycle()  # must not raise

    # Timer must still be advanced so we don't hammer a broken file on every tick
    assert time.time() - orch._last_config_reload < 5.0


# ---------------------------------------------------------------------------
# 7. reload_config command via _process_command_queue
# ---------------------------------------------------------------------------

def test_command_queue_reload_config(tmp_path, monkeypatch):
    """A pending reload_config command must apply risk param changes immediately."""
    import core.command_queue as cq

    queue_file = tmp_path / "commands.jsonl"
    monkeypatch.setattr(cq, "QUEUE_PATH", queue_file)

    cmd_id = str(uuid.uuid4())
    cmd = {
        "id": cmd_id,
        "type": "reload_config",
        "params": {},
        "requested_at": datetime.utcnow().isoformat(),
        "status": "pending",
        "result": None,
    }
    queue_file.write_text(json.dumps(cmd) + "\n")

    orch = _make_orchestrator(tmp_path, settings=_settings(per_trade_risk_pct=1.0))

    new_s = _settings(per_trade_risk_pct=1.5)
    with patch("core.execution.orchestrator.reload_settings", return_value=new_s):
        orch._process_command_queue()

    # Risk params should be updated
    assert orch.s.risk.per_trade_risk_pct == 1.5

    # Command should be marked done
    lines = queue_file.read_text().splitlines()
    d = json.loads(lines[0])
    assert d["status"] == "done"
    assert "reloaded" in d["result"]


def test_command_queue_reload_config_failure_marks_rejected(tmp_path, monkeypatch):
    """If reload_settings raises inside the command handler, command is rejected."""
    import core.command_queue as cq

    queue_file = tmp_path / "commands.jsonl"
    monkeypatch.setattr(cq, "QUEUE_PATH", queue_file)

    cmd_id = str(uuid.uuid4())
    cmd = {
        "id": cmd_id,
        "type": "reload_config",
        "params": {},
        "requested_at": datetime.utcnow().isoformat(),
        "status": "pending",
        "result": None,
    }
    queue_file.write_text(json.dumps(cmd) + "\n")

    orch = _make_orchestrator(tmp_path)

    with patch("core.execution.orchestrator.reload_settings", side_effect=ValueError("bad yaml")):
        orch._process_command_queue()

    lines = queue_file.read_text().splitlines()
    d = json.loads(lines[0])
    assert d["status"] == "rejected"
    assert "bad yaml" in d["result"]
