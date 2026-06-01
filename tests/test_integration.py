"""End-to-end integration tests for the pypoc paper-trading pipeline.

These tests exercise full slices of the system without touching the network:
  - test_paper_trade_full_cycle        : BacktestEngine on synthetic OHLC (no bhavcopy network call)
  - test_regime_classifier_all_regimes : RegimeClassifier across known market conditions
  - test_guardrails_block_overlimit    : Guardrails rejects trade when max_open_positions is full
  - test_charges_round_trip            : Charge accounting sanity (STT only on SELL, totals sane)
  - test_command_queue_round_trip      : Command lifecycle pending → done via queue helpers
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

import core.command_queue as cq
from core.broker.charges import compute_charges
from core.broker.paper import PaperBroker
from core.command_queue import enqueue, read_pending, update_status
from core.config import (
    BacktestGateCfg,
    CapitalCfg,
    DataCfg,
    ExecutionCfg,
    LLMCfg,
    LoggingCfg,
    MarketCfg,
    NotificationsCfg,
    PersistenceCfg,
    RegimeCfg,
    RiskCfg,
    Settings,
    UniverseCfg,
)
from core.regime.classifier import RegimeClassifier
from core.risk.guardrails import Guardrails, GuardrailDecision, MarketContext, PortfolioState
from core.types import OrderType, Position, Regime, Side, Signal
from backtest.engine import BacktestEngine


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _make_settings(symbols: list[str], initial_capital: float = 500_000) -> Settings:
    """Minimal Settings object suitable for a backtest run."""
    return Settings(
        mode="paper",
        timezone="Asia/Kolkata",
        capital=CapitalCfg(initial_inr=initial_capital, currency="INR"),
        market=MarketCfg(
            exchange="NSE", segment="EQ",
            trading_start="09:15", trading_end="15:30",
            no_new_entries_after="14:45", intraday_squareoff_at="15:15",
        ),
        universe=UniverseCfg(source="nifty50", symbols=symbols),
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
        data=DataCfg(
            primary_feed="angelone", reconnect_max_attempts=5,
            reconnect_backoff_seconds=2,
        ),
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
        logging=LoggingCfg(level="INFO", file="logs/agent.log",
                           json_log_file="logs/events.jsonl"),
    )


def _synthetic_ohlc(
    start_price: float, n: int, drift: float = 0.002, vol: float = 0.012, seed: int = 0
) -> pd.DataFrame:
    """Generate a deterministic OHLCV series with a consistent upward drift."""
    rng = np.random.default_rng(seed)
    rets = rng.normal(loc=drift, scale=vol, size=n)
    close = start_price * np.exp(np.cumsum(rets))
    high = close * (1 + rng.uniform(0.001, vol, size=n))
    low = close * (1 - rng.uniform(0.001, vol, size=n))
    open_ = np.r_[close[0], close[:-1]]
    volume = rng.integers(5_000_000, 15_000_000, size=n)
    idx = pd.date_range("2023-01-01", periods=n, freq="B")
    return pd.DataFrame(
        {"open": open_, "high": high, "low": low, "close": close, "volume": volume},
        index=idx,
    )


@pytest.fixture
def exec_cfg() -> ExecutionCfg:
    return ExecutionCfg(
        slippage_bps=5, brokerage_per_order_inr=20,
        stt_pct=0.025, exchange_txn_pct=0.00345, gst_pct=18.0,
        signal_cooldown_minutes=30,
    )


# ---------------------------------------------------------------------------
# Test 1 — full backtest cycle
# ---------------------------------------------------------------------------

def test_paper_trade_full_cycle():
    """BacktestEngine runs on synthetic data, at least one trade executes, equity moves."""
    # Use a strongly-trending series to maximise the chance of at least one signal.
    # 250 business days ≈ 1 year; start_date leaves 30 bars of warm-up.
    n = 250
    reliance = _synthetic_ohlc(2500.0, n, drift=0.003, vol=0.010, seed=10)
    infy = _synthetic_ohlc(1400.0, n, drift=0.003, vol=0.010, seed=11)
    nifty = _synthetic_ohlc(20_000.0, n, drift=0.003, vol=0.008, seed=12)

    settings = _make_settings(["RELIANCE", "INFY"])
    engine = BacktestEngine(settings)

    start_date = reliance.index[30].to_pydatetime()
    end_date = reliance.index[-1].to_pydatetime()

    result = engine.run(
        symbol_history={"RELIANCE": reliance, "INFY": infy},
        nifty_history=nifty,
        starting_equity=500_000,
        start_date=start_date,
        end_date=end_date,
    )

    # Equity curve must have entries for the whole run window.
    assert not result.equity_curve.empty
    assert len(result.equity_curve) >= 30

    # At least one trade must have executed (strongly-trending uptrend should fire).
    assert len(result.trades) >= 1, (
        f"Expected at least 1 trade; got 0. "
        f"Signals={result.signal_count}, accepted={result.accepted_count}, "
        f"rejected={result.rejected_count}, "
        f"regime_dist={result.regime_distribution}"
    )

    # Equity must have moved from start.
    assert result.ending_equity != result.starting_equity, (
        "Ending equity equals starting equity — no trades affected P&L."
    )

    # Trade log entries must be structurally sound.
    for tr in result.trades:
        assert tr.pnl is not None, "Trade record missing pnl"
        assert tr.charges > 0, f"Trade charges should be > 0, got {tr.charges}"
        assert tr.symbol in ("RELIANCE", "INFY"), f"Unexpected symbol {tr.symbol}"
        assert tr.exit_reason in ("stop_loss", "target", "manual", "eod_squareoff"), (
            f"Unexpected exit_reason: {tr.exit_reason}"
        )


# ---------------------------------------------------------------------------
# Test 2 — regime classifier produces all regimes
# ---------------------------------------------------------------------------

def test_regime_classifier_all_regimes():
    """Classifier correctly identifies TREND and VOLATILE regimes given suitable data."""
    cfg = RegimeCfg(
        adx_period=14,
        adx_trend_threshold=20.0,
        bb_width_range_threshold=0.04,
        vix_volatile_threshold=20.0,
    )
    clf = RegimeClassifier(cfg)

    # --- TREND regime: strongly-trending series makes ADX rise well above threshold ---
    trend_data = _synthetic_ohlc(20_000, 120, drift=0.004, vol=0.005, seed=100)
    regime_snap = clf.classify(trend_data, vix=14.0)
    assert regime_snap.regime == Regime.TREND, (
        f"Expected TREND on uptrending series, got {regime_snap.regime} "
        f"(ADX={regime_snap.adx:.2f}, BB_width={regime_snap.bb_width:.4f})"
    )

    # --- VOLATILE regime: high VIX overrides everything ---
    flat_data = _synthetic_ohlc(20_000, 80, drift=0.0, vol=0.003, seed=101)
    volatile_snap = clf.classify(flat_data, vix=25.0)  # VIX > threshold 20
    assert volatile_snap.regime == Regime.VOLATILE, (
        f"Expected VOLATILE with VIX=25, got {volatile_snap.regime}"
    )

    # --- Both regimes were seen at least once ---
    assert regime_snap.regime == Regime.TREND
    assert volatile_snap.regime == Regime.VOLATILE


# ---------------------------------------------------------------------------
# Test 3 — guardrails block when max_open_positions is already full
# ---------------------------------------------------------------------------

def test_guardrails_block_overlimit(risk_cfg, market_cfg, execution_cfg):
    """Guardrails.check() must reject a new signal when all 5 position slots are occupied."""
    guardrails = Guardrails(risk_cfg, market_cfg, execution_cfg)

    # Fill all max_open_positions slots.
    full_positions = [
        Position(
            symbol=f"SYM{i}", qty=10, avg_price=500.0, stop_loss=480.0,
            target=550.0, strategy="trend_breakout",
            opened_at=datetime.utcnow(), last_price=510.0,
        )
        for i in range(5)  # risk_cfg.max_open_positions == 5
    ]

    portfolio = PortfolioState(
        equity=100_000.0,
        starting_equity_today=100_000.0,
        peak_equity=100_000.0,
        open_positions=full_positions,
        realized_pnl_today=0.0,
        last_exit_by_symbol={},
        halted=False,
    )

    new_signal = Signal(
        symbol="NEWSTOCK",
        side=Side.BUY,
        strategy="trend_breakout",
        regime=Regime.TREND,
        entry_price=1000.0,
        stop_loss=980.0,
        target=1050.0,
        confidence=0.7,
        rationale="fresh breakout",
        ts=datetime(2024, 6, 15, 10, 30),
    )

    market = MarketContext(
        now=datetime(2024, 6, 15, 10, 30),
        nifty_ltp=22_000.0,
        nifty_change_pct_15m=0.1,
        vix=14.0,
        vix_change_pct_15m=0.5,
        last_tick_age_seconds=1.0,
        avg_daily_volumes={"NEWSTOCK": 5_000_000},
        spread_pct_by_symbol={"NEWSTOCK": 0.05},
    )

    decision: GuardrailDecision = guardrails.check(new_signal, qty=10, portfolio=portfolio, market=market)

    assert not decision.allow, "Expected REJECT when max_open_positions full"
    assert decision.rule == "max_open_positions", (
        f"Expected rule 'max_open_positions', got '{decision.rule}'"
    )


# ---------------------------------------------------------------------------
# Test 4 — charges round-trip
# ---------------------------------------------------------------------------

def test_charges_round_trip(exec_cfg):
    """Charges are > 0, < 1% of trade value, and STT appears only on the SELL side."""
    price = 2500.0
    qty = 20
    trade_value = price * qty  # 50_000 INR

    # BUY charges
    buy_charges = compute_charges(
        side=Side.BUY,
        qty=qty,
        price=price,
        brokerage_per_order_inr=exec_cfg.brokerage_per_order_inr,
        stt_pct=exec_cfg.stt_pct,
        exchange_txn_pct=exec_cfg.exchange_txn_pct,
        gst_pct=exec_cfg.gst_pct,
    )

    # SELL charges
    sell_charges = compute_charges(
        side=Side.SELL,
        qty=qty,
        price=price,
        brokerage_per_order_inr=exec_cfg.brokerage_per_order_inr,
        stt_pct=exec_cfg.stt_pct,
        exchange_txn_pct=exec_cfg.exchange_txn_pct,
        gst_pct=exec_cfg.gst_pct,
    )

    total_round_trip = buy_charges.total + sell_charges.total

    # Both legs must cost something.
    assert buy_charges.total > 0, "BUY charges must be > 0"
    assert sell_charges.total > 0, "SELL charges must be > 0"

    # Round-trip cost must be well below 1% of trade value (sanity check).
    assert total_round_trip < trade_value * 0.01, (
        f"Round-trip charges {total_round_trip:.2f} exceed 1% of trade value {trade_value:.2f}"
    )

    # STT is charged on SELL only (Indian equities intraday delivery rule).
    assert buy_charges.stt == 0.0, f"STT should be 0 on BUY side, got {buy_charges.stt}"
    assert sell_charges.stt > 0.0, f"STT should be > 0 on SELL side, got {sell_charges.stt}"

    # Verify via PaperBroker that placed orders accumulate charges in trade_log.
    broker = PaperBroker(starting_cash=200_000.0, exec_cfg=exec_cfg)
    broker.update_market_prices({"RELIANCE": price})

    broker.place_order(
        symbol="RELIANCE", side=Side.BUY, qty=qty,
        order_type=OrderType.MARKET, stop_loss=price * 0.98, target=price * 1.05,
        strategy="test",
    )
    # Force a target exit by pushing price above target.
    broker.update_market_prices({"RELIANCE": price * 1.06})

    assert len(broker.trade_log) == 1
    record = broker.trade_log[0]
    assert record.charges > 0, "Trade record charges must be > 0"
    assert record.charges < trade_value * 0.01, (
        f"Trade record charges {record.charges:.2f} exceed 1% of trade value"
    )


# ---------------------------------------------------------------------------
# Test 5 — command queue round-trip
# ---------------------------------------------------------------------------

def test_command_queue_round_trip(tmp_path, monkeypatch):
    """Enqueue a halt_agent command and then mark it done — lifecycle is tracked correctly."""
    queue_file = tmp_path / "commands.jsonl"
    monkeypatch.setattr(cq, "QUEUE_PATH", queue_file)

    # 1. Enqueue the command.
    cmd = enqueue("halt_agent", {"reason": "integration_test"})
    assert cmd.status == "pending"
    assert cmd.type == "halt_agent"

    # 2. File must exist and contain exactly one line.
    lines = queue_file.read_text().splitlines()
    assert len(lines) == 1
    parsed = json.loads(lines[0])
    assert parsed["status"] == "pending"
    assert parsed["type"] == "halt_agent"

    # 3. read_pending() returns it.
    pending_before = read_pending()
    assert len(pending_before) == 1
    assert pending_before[0].id == cmd.id

    # 4. Simulate the orchestrator processing the command: mark as done.
    update_status(cmd.id, "done", result="agent halted successfully")

    # 5. read_pending() must now return nothing.
    pending_after = read_pending()
    assert pending_after == [], f"Expected empty pending queue after done, got {pending_after}"

    # 6. The file still records the command with updated status.
    updated_lines = queue_file.read_text().splitlines()
    assert len(updated_lines) == 1
    updated = json.loads(updated_lines[0])
    assert updated["status"] == "done"
    assert updated["result"] == "agent halted successfully"
    assert updated["id"] == cmd.id
