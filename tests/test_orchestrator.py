"""Integration tests for core/execution/orchestrator.py.

Covers:
- tick_lifecycle: equity snapshot + peak_equity update
- _check_global_halts: daily-loss circuit triggers halt
- _process_command_queue: halt_agent command from file
- _nifty_market_filter: below 200-DMA blocks all buys
- _on_candle_close: economic blackout blocks BUY signals
"""
from __future__ import annotations

import json
import uuid
from datetime import date, datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch, PropertyMock

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
from core.types import Regime


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _settings(symbols: list[str] | None = None) -> Settings:
    return Settings(
        mode="paper",
        timezone="Asia/Kolkata",
        capital=CapitalCfg(initial_inr=500_000, currency="INR"),
        market=MarketCfg(
            exchange="NSE", segment="EQ",
            trading_start="09:15", trading_end="15:30",
            no_new_entries_after="14:45", intraday_squareoff_at="15:15",
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
            "mean_reversion": {"enabled": False},
            "volatility_compression": {"enabled": False},
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


def _make_mock_broker(equity: float = 500_000.0) -> MagicMock:
    broker = MagicMock()
    broker.equity.return_value = equity
    broker.cash.return_value = equity
    broker.get_positions.return_value = []
    broker.realized_pnl = 0.0
    return broker


def _make_mock_feed() -> MagicMock:
    feed = MagicMock()
    feed.last_tick_age_seconds.return_value = 0.5
    feed.is_connected.return_value = True
    return feed


def _make_mock_store() -> MagicMock:
    return MagicMock()


def _make_mock_events(tmp_path: Path) -> MagicMock:
    events = MagicMock()
    return events


def _make_orchestrator(
    tmp_path: Path,
    equity: float = 500_000.0,
    settings: Settings | None = None,
) -> Orchestrator:
    """Build an Orchestrator with all external deps mocked out."""
    s = settings or _settings()
    broker = _make_mock_broker(equity)
    feed = _make_mock_feed()
    store = _make_mock_store()
    events = _make_mock_events(tmp_path)

    # Patch snapshot write + VIX fetch so no filesystem / network I/O occurs
    with patch("core.execution.orchestrator.write_snapshot"), \
         patch("core.execution.orchestrator.get_vix", return_value=None), \
         patch("core.execution.orchestrator.get_nifty_pcr", return_value=None):
        orch = Orchestrator(settings=s, feed=feed, broker=broker, store=store, events=events)

    orch.broker = broker  # keep reference for assertions
    orch.store = store
    return orch


# ---------------------------------------------------------------------------
# Test 1: tick_lifecycle records equity and updates peak_equity
# ---------------------------------------------------------------------------

def test_orchestrator_tick_lifecycle_updates_equity(tmp_path):
    """tick_lifecycle should call store.record_equity and update peak_equity."""
    initial = 500_000.0
    new_high = 520_000.0  # above initial so peak must update

    orch = _make_orchestrator(tmp_path, equity=initial)
    # Simulate equity rising between calls
    orch.broker.equity.return_value = new_high
    orch.broker.cash.return_value = new_high
    orch.peak_equity = initial  # set known starting peak

    with patch("core.execution.orchestrator.write_snapshot"), \
         patch("core.execution.orchestrator.get_vix", return_value=None), \
         patch("core.execution.orchestrator.get_nifty_pcr", return_value=None):
        orch.tick_lifecycle()

    # store.record_equity must have been called
    orch.store.record_equity.assert_called_once()
    call_kwargs = orch.store.record_equity.call_args

    # Check equity value was passed
    args, kwargs = call_kwargs
    combined = {**dict(zip(["cash", "equity", "realized_pnl", "open_positions"], args)), **kwargs}
    assert combined.get("equity", None) == new_high or new_high in (args + tuple(kwargs.values()))

    # peak_equity must be updated to the new high
    assert orch.peak_equity == new_high


# ---------------------------------------------------------------------------
# Test 2: _check_global_halts triggers halt on daily-loss circuit
# ---------------------------------------------------------------------------

def test_orchestrator_halts_on_daily_loss_circuit(tmp_path):
    """When equity drops more than daily_loss_circuit_pct, halted becomes True."""
    starting = 500_000.0
    # 3% loss threshold; drop equity by 4%
    loss_equity = starting * (1 - 0.04)

    orch = _make_orchestrator(tmp_path, equity=starting)
    orch.starting_equity_today = starting
    orch.peak_equity = starting
    orch.broker.equity.return_value = loss_equity

    assert orch.halted is False

    orch._check_global_halts()

    assert orch.halted is True
    assert "daily loss circuit" in orch.halt_reason


# ---------------------------------------------------------------------------
# Test 3: _process_command_queue processes halt_agent from temp file
# ---------------------------------------------------------------------------

def test_orchestrator_command_queue_halt(tmp_path, monkeypatch):
    """A pending halt_agent command in the queue file should set halted=True."""
    import core.command_queue as cq

    queue_file = tmp_path / "commands.jsonl"
    monkeypatch.setattr(cq, "QUEUE_PATH", queue_file)

    # Write a pending halt_agent command directly to the queue file
    cmd_id = str(uuid.uuid4())
    cmd = {
        "id": cmd_id,
        "type": "halt_agent",
        "params": {"reason": "test manual halt"},
        "requested_at": datetime.utcnow().isoformat(),
        "status": "pending",
        "result": None,
    }
    queue_file.write_text(json.dumps(cmd) + "\n")

    orch = _make_orchestrator(tmp_path)
    assert orch.halted is False

    orch._process_command_queue()

    assert orch.halted is True
    assert orch.halt_reason == "test manual halt"


# ---------------------------------------------------------------------------
# Test 4: _nifty_market_filter blocks all buys when price is below 200-DMA
# ---------------------------------------------------------------------------

def test_orchestrator_nifty_filter_blocks_buy_in_decline(tmp_path):
    """When Nifty closes are consistently below 200-DMA, allow_any must be False."""
    orch = _make_orchestrator(tmp_path)

    # Build a 210-row OHLC DataFrame where the current price is far BELOW the 200-DMA.
    # We start high then crash so the 200-DMA sits well above recent closes.
    n = 210
    high_phase = np.linspace(25000, 25000, 160)   # stable high for 160 bars → DMA anchors here
    low_phase  = np.linspace(22000, 19000, 50)     # then crash for 50 bars
    close = np.concatenate([high_phase, low_phase])
    idx = pd.date_range("2023-01-01", periods=n, freq="B")
    df = pd.DataFrame({
        "open":  close,
        "high":  close * 1.002,
        "low":   close * 0.998,
        "close": close,
        "volume": np.ones(n) * 1_000_000,
    }, index=idx)

    orch.nifty_ohlc_daily = df

    # Patch external calls that _nifty_market_filter makes
    with patch("core.execution.orchestrator.get_nifty_pcr", return_value=None), \
         patch("core.execution.orchestrator.get_institutional_sentiment", return_value=None,
               create=True):
        allow_trend, allow_range, allow_any = orch._nifty_market_filter()

    # Below 200-DMA → allow_any must be False, trend/range also blocked
    assert allow_any is False
    assert allow_trend is False


# ---------------------------------------------------------------------------
# Test 5: _on_candle_close blocks BUY during economic blackout
# ---------------------------------------------------------------------------

def test_orchestrator_blackout_blocks_buy(tmp_path):
    """Signals emitted on an RBI MPC blackout day must not reach _handle_signal."""
    from core.data.economic_calendar import RBI_MPC_DATES
    from core.types import Candle, Side, Regime

    orch = _make_orchestrator(tmp_path)

    # Pick a known RBI MPC date and make sure datetime.now() returns it
    rbi_date = datetime.fromisoformat(RBI_MPC_DATES[0])  # e.g. 2024-02-08

    # Build a candle and 30-bar history so _on_candle_close doesn't early-exit
    candle = Candle(
        symbol="RELIANCE", interval="5m",
        open=2500.0, high=2510.0, low=2490.0, close=2505.0, volume=50_000,
        ts=rbi_date,
    )

    n = 35
    close_vals = np.linspace(2400, 2500, n)
    history = [
        Candle(
            symbol="RELIANCE", interval="5m",
            open=v, high=v * 1.002, low=v * 0.998, close=v, volume=10_000,
            ts=rbi_date - timedelta(minutes=5 * (n - i)),
        )
        for i, v in enumerate(close_vals)
    ]

    # Set up orchestrator state
    orch.current_regime = RegimeSnapshot(Regime.TREND, 30.0, 0.05, 15.0, "test")
    orch.nifty_ohlc_daily = None  # will return True,True,True from filter

    # Mock aggregator.history to return our synthetic history
    orch.aggregator = MagicMock()
    orch.aggregator.history.return_value = history

    # Mock strategy to emit a BUY signal so we can check it gets blocked
    from core.types import Signal
    mock_signal = Signal(
        symbol="RELIANCE",
        side=Side.BUY,
        strategy="trend_breakout",
        regime=Regime.TREND,
        entry_price=2505.0,
        stop_loss=2480.0,
        target=2560.0,
        confidence=0.7,
        rationale="test breakout",
        ts=rbi_date,
    )
    mock_strat = MagicMock()
    mock_strat.supports.return_value = True
    mock_strat.evaluate.return_value = mock_signal
    mock_strat.name = "trend_breakout"
    orch.strategies = [mock_strat]

    # Spy on _handle_signal to confirm it is NOT called during blackout
    orch._handle_signal = MagicMock()

    with patch("core.execution.orchestrator.datetime") as mock_dt, \
         patch("core.execution.orchestrator.get_nifty_pcr", return_value=None), \
         patch("core.execution.orchestrator.get_institutional_sentiment",
               return_value=None, create=True):
        # Make datetime.now() return the RBI MPC date
        mock_dt.now.return_value = rbi_date
        # is_blackout_day must still work with a real datetime
        mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)

        orch._on_candle_close(candle)

    # _handle_signal must NOT have been called — blackout blocked the BUY
    orch._handle_signal.assert_not_called()
