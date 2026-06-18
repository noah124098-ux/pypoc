"""Extended orchestrator tests — covers branches not hit by test_orchestrator.py.

Target branches:
- _maybe_squareoff_eod: triggers at/after squareoff time, with open positions
- _maybe_squareoff_eod: no positions → early return, no emit
- _check_global_halts: drawdown circuit triggers halt
- _check_global_halts: daily-loss warning (half-threshold) sets flag
- _check_global_halts: drawdown warning (half-threshold) sets flag
- _check_global_halts: already halted → returns immediately
- _check_global_halts: warning resets when loss recovers
- _process_command_queue: resume_agent resumes when pnl is OK
- _process_command_queue: resume_agent rejected when daily-loss circuit still active
- _process_command_queue: update_risk_param updates the value
- _process_command_queue: place_paper_order rejected when no market price
- _process_command_queue: unknown command type rejected
- _on_tick: updates spread_pct_by_symbol when bid/ask/ltp are positive
- _on_tick: no spread update when bid or ask is 0
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch, call

import numpy as np
import pandas as pd
import pytest

from core.config import (
    BacktestGateCfg, CapitalCfg, DataCfg, ExecutionCfg, LLMCfg, LoggingCfg,
    MarketCfg, NotificationsCfg, PersistenceCfg, RegimeCfg, RiskCfg, Settings,
    UniverseCfg,
)
from core.execution.orchestrator import Orchestrator
from core.regime.classifier import RegimeSnapshot
from core.types import Regime, Tick


# ---------------------------------------------------------------------------
# Shared helpers (mirrors test_orchestrator.py helpers without duplication)
# ---------------------------------------------------------------------------

def _settings(squareoff_at: str = "15:15", symbols: list[str] | None = None) -> Settings:
    return Settings(
        mode="paper",
        timezone="Asia/Kolkata",
        capital=CapitalCfg(initial_inr=500_000, currency="INR"),
        market=MarketCfg(
            exchange="NSE", segment="EQ",
            trading_start="09:15", trading_end="15:30",
            no_new_entries_after="14:45",
            intraday_squareoff_at=squareoff_at,
        ),
        universe=UniverseCfg(source="nifty50", symbols=symbols or ["RELIANCE"]),
        risk=RiskCfg(
            per_trade_risk_pct=1.0, max_position_pct=20.0, max_open_positions=5,
            daily_loss_circuit_pct=3.0, drawdown_circuit_pct=10.0,
            liquidity_max_pct_of_adv=10.0, max_spread_pct=0.3,
            black_swan_nifty_move_pct=2.0, black_swan_vix_jump_pct=20.0,
            stale_tick_seconds=10,
        ),
        regime=RegimeCfg(
            adx_period=14, adx_trend_threshold=20.0,
            bb_width_range_threshold=0.04, vix_volatile_threshold=20.0,
        ),
        strategies={
            "trend_breakout": {
                "enabled": True, "regimes": ["TREND"],
                "donchian_period": 20, "atr_period": 14,
                "atr_stop_multiplier": 2.0, "target_r_multiple": 2.5,
            },
        },
        execution=ExecutionCfg(
            slippage_bps=5, brokerage_per_order_inr=20,
            stt_pct=0.025, exchange_txn_pct=0.00345, gst_pct=18.0,
            signal_cooldown_minutes=30,
        ),
        backtest_gate=BacktestGateCfg(
            min_sharpe=1.2, max_drawdown_pct=15.0, min_win_rate=45.0,
            min_profit_factor=1.5, min_trades=100, walk_forward_years=3,
        ),
        data=DataCfg(primary_feed="angelone", reconnect_max_attempts=5,
                     reconnect_backoff_seconds=2),
        persistence=PersistenceCfg(sqlite_path="data/agent.db",
                                   tick_parquet_dir="data/ticks"),
        llm=LLMCfg(
            eod_reviewer_model="claude-opus-4-7",
            news_scorer_model="claude-haiku-4-5-20251001",
            enable_eod_review=False, auto_apply_within_safe_bounds=False,
        ),
        notifications=NotificationsCfg(
            telegram_enabled=False, email_enabled=False,
            dashboard_enabled=False, dashboard_port=8501,
        ),
        logging=LoggingCfg(level="INFO", file="logs/agent.log",
                           json_log_file="logs/events.jsonl"),
    )


def _make_mock_broker(equity: float = 500_000.0) -> MagicMock:
    broker = MagicMock()
    broker.equity.return_value = equity
    broker.cash.return_value = equity
    broker.get_positions.return_value = []
    broker.realized_pnl = 0.0
    broker.trade_log = []
    return broker


def _make_orchestrator(
    tmp_path: Path,
    equity: float = 500_000.0,
    settings: Settings | None = None,
    squareoff_at: str = "15:15",
) -> Orchestrator:
    s = settings or _settings(squareoff_at=squareoff_at)
    broker = _make_mock_broker(equity)
    feed = MagicMock()
    feed.last_tick_age_seconds.return_value = 0.5
    feed.is_connected.return_value = True
    store = MagicMock()
    # Return None so the orchestrator takes the fresh circuit-baseline path
    # (a bare MagicMock is truthy and would corrupt starting_equity_today/peak_equity).
    store.load_daily_state.return_value = None
    events = MagicMock()

    with patch("core.execution.orchestrator.write_snapshot"), \
         patch("core.execution.orchestrator.get_vix", return_value=None), \
         patch("core.execution.orchestrator.get_nifty_pcr", return_value=None):
        orch = Orchestrator(settings=s, feed=feed, broker=broker, store=store,
                            events=events)

    orch.broker = broker
    orch.store = store
    return orch


# ---------------------------------------------------------------------------
# Test: _maybe_squareoff_eod — no positions → no emit, no crash
# ---------------------------------------------------------------------------

def test_squareoff_eod_no_positions_no_emit(tmp_path):
    """EOD squareoff with no open positions should return silently without emitting."""
    orch = _make_orchestrator(tmp_path, squareoff_at="00:01")  # past squareoff
    orch.broker.get_positions.return_value = []

    with patch("core.execution.orchestrator.datetime") as mock_dt:
        mock_dt.now.return_value = datetime(2026, 5, 13, 15, 20)
        mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
        orch._maybe_squareoff_eod()

    orch.events.emit_daily_summary.assert_not_called()


# ---------------------------------------------------------------------------
# Test: _maybe_squareoff_eod — triggers with open positions, emits daily_summary
# ---------------------------------------------------------------------------

def test_squareoff_eod_with_positions_emits_daily_summary(tmp_path):
    """EOD squareoff with open positions should force-close and emit daily_summary."""
    from core.types import Position

    orch = _make_orchestrator(tmp_path, squareoff_at="00:01")  # past squareoff
    orch.starting_equity_today = 500_000.0

    pos = Position(
        symbol="RELIANCE", qty=10, avg_price=2500.0, stop_loss=2450.0,
        target=2600.0, strategy="trend_breakout", opened_at=datetime.utcnow(),
    )
    pos.last_price = 2510.0
    orch.broker.get_positions.return_value = [pos]
    orch.broker.equity.return_value = 500_000.0

    with patch("core.execution.orchestrator.datetime") as mock_dt:
        mock_dt.now.return_value = datetime(2026, 5, 13, 15, 20)
        mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
        orch._maybe_squareoff_eod()

    orch.events.emit_daily_summary.assert_called_once()
    call_kwargs = orch.events.emit_daily_summary.call_args
    _, kwargs = call_kwargs
    assert "equity" in kwargs
    assert "day_pnl_pct" in kwargs


# ---------------------------------------------------------------------------
# Test: _maybe_squareoff_eod — before squareoff time → no action
# ---------------------------------------------------------------------------

def test_squareoff_eod_before_time_no_action(tmp_path):
    """Before the squareoff time, _maybe_squareoff_eod must do nothing."""
    from core.types import Position

    orch = _make_orchestrator(tmp_path, squareoff_at="23:59")  # well in the future
    pos = Position(
        symbol="RELIANCE", qty=10, avg_price=2500.0, stop_loss=2450.0,
        target=2600.0, strategy="trend_breakout", opened_at=datetime.utcnow(),
    )
    orch.broker.get_positions.return_value = [pos]

    # Call without mocking datetime — current time is before 23:59
    orch._maybe_squareoff_eod()

    orch.events.emit_daily_summary.assert_not_called()


# ---------------------------------------------------------------------------
# Test: _check_global_halts — drawdown circuit triggers halt
# ---------------------------------------------------------------------------

def test_check_global_halts_drawdown_circuit(tmp_path):
    """When drawdown exceeds drawdown_circuit_pct, halted must become True."""
    starting = 500_000.0
    # 10% circuit; set peak high and equity low
    peak = 600_000.0
    equity = 500_000.0  # drawdown = (600k - 500k) / 600k = 16.7% > 10%

    orch = _make_orchestrator(tmp_path, equity=equity)
    orch.starting_equity_today = starting
    orch.peak_equity = peak
    orch.broker.equity.return_value = equity

    assert orch.halted is False
    orch._check_global_halts()

    assert orch.halted is True
    assert "drawdown circuit" in orch.halt_reason


# ---------------------------------------------------------------------------
# Test: _check_global_halts — daily-loss warning (half-threshold flag)
# ---------------------------------------------------------------------------

def test_check_global_halts_daily_loss_warning(tmp_path):
    """At half the daily-loss circuit threshold, daily_loss_warning must be set True."""
    starting = 500_000.0
    # circuit = 3%, warning at 1.5%; drop by 2% → should trigger warning but not halt
    equity = starting * (1 - 0.02)  # -2% is past warning but not past circuit

    orch = _make_orchestrator(tmp_path, equity=equity)
    orch.starting_equity_today = starting
    orch.peak_equity = starting
    orch.broker.equity.return_value = equity

    assert orch.daily_loss_warning is False
    orch._check_global_halts()

    assert orch.daily_loss_warning is True
    assert orch.halted is False  # not yet halted — warning only


# ---------------------------------------------------------------------------
# Test: _check_global_halts — drawdown warning (half-threshold flag)
# ---------------------------------------------------------------------------

def test_check_global_halts_drawdown_warning(tmp_path):
    """At half the drawdown circuit, drawdown_warning must be set True without halt."""
    starting = 500_000.0
    peak = 550_000.0
    # circuit = 10%, warning at 5%; drawdown = (550k - 520k) / 550k ≈ 5.45% → warning
    equity = 520_000.0

    orch = _make_orchestrator(tmp_path, equity=equity)
    orch.starting_equity_today = starting
    orch.peak_equity = peak
    orch.broker.equity.return_value = equity

    assert orch.drawdown_warning is False
    orch._check_global_halts()

    assert orch.drawdown_warning is True
    assert orch.halted is False


# ---------------------------------------------------------------------------
# Test: _check_global_halts — already halted → returns immediately
# ---------------------------------------------------------------------------

def test_check_global_halts_already_halted_no_change(tmp_path):
    """If already halted, _check_global_halts must return without changing halt_reason."""
    orch = _make_orchestrator(tmp_path)
    orch.halted = True
    orch.halt_reason = "original reason"
    orch.starting_equity_today = 500_000.0
    # equity low enough that a new halt would be triggered if not guarded
    orch.broker.equity.return_value = 400_000.0

    orch._check_global_halts()

    assert orch.halt_reason == "original reason"  # unchanged


# ---------------------------------------------------------------------------
# Test: _check_global_halts — warning resets when daily loss recovers
# ---------------------------------------------------------------------------

def test_check_global_halts_daily_loss_warning_resets(tmp_path):
    """When equity recovers above the warning threshold, daily_loss_warning resets."""
    starting = 500_000.0
    orch = _make_orchestrator(tmp_path, equity=starting)
    orch.starting_equity_today = starting
    orch.peak_equity = starting
    orch.daily_loss_warning = True  # pre-set warning flag
    # equity at 100% of starting → no loss → warning should clear
    orch.broker.equity.return_value = starting

    orch._check_global_halts()

    assert orch.daily_loss_warning is False


# ---------------------------------------------------------------------------
# Test: _process_command_queue — resume_agent succeeds when daily-loss is OK
# ---------------------------------------------------------------------------

def test_command_queue_resume_agent_succeeds(tmp_path, monkeypatch):
    """resume_agent command should clear halted state when pnl is acceptable."""
    import core.command_queue as cq

    queue_file = tmp_path / "commands.jsonl"
    monkeypatch.setattr(cq, "QUEUE_PATH", queue_file)

    cmd_id = str(uuid.uuid4())
    cmd = {
        "id": cmd_id,
        "type": "resume_agent",
        "params": {},
        "requested_at": datetime.utcnow().isoformat(),
        "status": "pending",
        "result": None,
    }
    queue_file.write_text(json.dumps(cmd) + "\n")

    orch = _make_orchestrator(tmp_path)
    orch.halted = True
    orch.halt_reason = "manual"
    # equity == starting → 0% loss, well above the -3% circuit
    orch.starting_equity_today = orch.broker.equity()

    orch._process_command_queue()

    assert orch.halted is False
    assert orch.halt_reason == ""


# ---------------------------------------------------------------------------
# Test: _process_command_queue — resume_agent rejected when circuit still active
# ---------------------------------------------------------------------------

def test_command_queue_resume_rejected_circuit_active(tmp_path, monkeypatch):
    """resume_agent must be rejected when the daily-loss circuit is still triggered."""
    import core.command_queue as cq

    queue_file = tmp_path / "commands.jsonl"
    monkeypatch.setattr(cq, "QUEUE_PATH", queue_file)

    cmd_id = str(uuid.uuid4())
    cmd = {
        "id": cmd_id,
        "type": "resume_agent",
        "params": {},
        "requested_at": datetime.utcnow().isoformat(),
        "status": "pending",
        "result": None,
    }
    queue_file.write_text(json.dumps(cmd) + "\n")

    starting = 500_000.0
    orch = _make_orchestrator(tmp_path, equity=starting * 0.96)  # -4% loss
    orch.halted = True
    orch.halt_reason = "daily loss circuit hit"
    orch.starting_equity_today = starting

    orch._process_command_queue()

    # Should still be halted
    assert orch.halted is True


# ---------------------------------------------------------------------------
# Test: _process_command_queue — update_risk_param updates value
# ---------------------------------------------------------------------------

def test_command_queue_update_risk_param(tmp_path, monkeypatch):
    """update_risk_param command should set the named risk parameter."""
    import core.command_queue as cq

    queue_file = tmp_path / "commands.jsonl"
    monkeypatch.setattr(cq, "QUEUE_PATH", queue_file)

    cmd_id = str(uuid.uuid4())
    cmd = {
        "id": cmd_id,
        "type": "update_risk_param",
        "params": {"param": "per_trade_risk_pct", "value": "2.5"},
        "requested_at": datetime.utcnow().isoformat(),
        "status": "pending",
        "result": None,
    }
    queue_file.write_text(json.dumps(cmd) + "\n")

    orch = _make_orchestrator(tmp_path)
    assert orch.s.risk.per_trade_risk_pct == 1.0

    orch._process_command_queue()

    assert orch.s.risk.per_trade_risk_pct == 2.5


# ---------------------------------------------------------------------------
# Test: _process_command_queue — place_paper_order rejected when no market price
# ---------------------------------------------------------------------------

def test_command_queue_place_paper_order_no_price(tmp_path, monkeypatch):
    """place_paper_order should be rejected when no LTP is available for the symbol."""
    import core.command_queue as cq

    queue_file = tmp_path / "commands.jsonl"
    monkeypatch.setattr(cq, "QUEUE_PATH", queue_file)

    cmd_id = str(uuid.uuid4())
    cmd = {
        "id": cmd_id,
        "type": "place_paper_order",
        "params": {"symbol": "INFY", "side": "BUY", "qty": "5"},
        "requested_at": datetime.utcnow().isoformat(),
        "status": "pending",
        "result": None,
    }
    queue_file.write_text(json.dumps(cmd) + "\n")

    orch = _make_orchestrator(tmp_path)
    # Broker has no price for INFY
    orch.broker._latest_prices = {}

    # Should not raise; command should be rejected gracefully
    orch._process_command_queue()


# ---------------------------------------------------------------------------
# Test: _process_command_queue — unknown command type is rejected
# ---------------------------------------------------------------------------

def test_command_queue_unknown_type_rejected(tmp_path, monkeypatch):
    """An unrecognised command type must be rejected, not processed."""
    import core.command_queue as cq

    queue_file = tmp_path / "commands.jsonl"
    monkeypatch.setattr(cq, "QUEUE_PATH", queue_file)

    cmd_id = str(uuid.uuid4())
    cmd = {
        "id": cmd_id,
        "type": "launch_missiles",
        "params": {},
        "requested_at": datetime.utcnow().isoformat(),
        "status": "pending",
        "result": None,
    }
    queue_file.write_text(json.dumps(cmd) + "\n")

    orch = _make_orchestrator(tmp_path)
    # Must not raise and must not modify agent state
    original_halted = orch.halted
    orch._process_command_queue()
    assert orch.halted == original_halted


# ---------------------------------------------------------------------------
# Test: _on_tick — spread is computed when bid/ask/ltp are all positive
# ---------------------------------------------------------------------------

def test_on_tick_computes_spread(tmp_path):
    """_on_tick must update spread_pct_by_symbol when bid, ask, and ltp are positive."""
    orch = _make_orchestrator(tmp_path)
    tick = Tick(
        symbol="RELIANCE",
        ltp=2500.0,
        bid=2499.0,
        ask=2501.0,
        volume=1000,
        ts=datetime.utcnow(),
    )
    orch._on_tick(tick)

    spread = orch.spread_pct_by_symbol.get("RELIANCE")
    assert spread is not None
    expected = (2501.0 - 2499.0) / 2500.0 * 100.0
    assert abs(spread - expected) < 1e-9


# ---------------------------------------------------------------------------
# Test: _on_tick — no spread update when bid or ask is 0
# ---------------------------------------------------------------------------

def test_on_tick_no_spread_when_bid_zero(tmp_path):
    """When bid == 0, spread should not be written (bad quote data)."""
    orch = _make_orchestrator(tmp_path)
    tick = Tick(
        symbol="INFY",
        ltp=1500.0,
        bid=0.0,   # bad data
        ask=1501.0,
        volume=500,
        ts=datetime.utcnow(),
    )
    orch._on_tick(tick)
    assert "INFY" not in orch.spread_pct_by_symbol


def test_on_tick_no_spread_when_ask_zero(tmp_path):
    """When ask == 0, spread should not be written (bad quote data)."""
    orch = _make_orchestrator(tmp_path)
    tick = Tick(
        symbol="TCS",
        ltp=3400.0,
        bid=3399.0,
        ask=0.0,    # bad data
        volume=200,
        ts=datetime.utcnow(),
    )
    orch._on_tick(tick)
    assert "TCS" not in orch.spread_pct_by_symbol
