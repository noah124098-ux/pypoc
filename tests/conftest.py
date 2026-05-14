"""Shared fixtures for the test suite."""
from __future__ import annotations

from datetime import datetime
from typing import Any

import pytest

from core.config import ExecutionCfg, MarketCfg, RiskCfg
from core.risk.guardrails import Guardrails, MarketContext, PortfolioState
from core.types import Regime, Side, Signal


@pytest.fixture
def risk_cfg() -> RiskCfg:
    return RiskCfg(
        per_trade_risk_pct=1.0,
        max_position_pct=20.0,
        max_open_positions=5,
        daily_loss_circuit_pct=3.0,
        drawdown_circuit_pct=10.0,
        liquidity_max_pct_of_adv=1.0,
        max_spread_pct=0.3,
        black_swan_nifty_move_pct=2.0,
        black_swan_vix_jump_pct=20.0,
        stale_tick_seconds=10,
    )


@pytest.fixture
def market_cfg() -> MarketCfg:
    return MarketCfg(
        exchange="NSE",
        segment="EQ",
        trading_start="09:15",
        trading_end="15:30",
        no_new_entries_after="14:45",
        intraday_squareoff_at="15:15",
    )


@pytest.fixture
def execution_cfg() -> ExecutionCfg:
    return ExecutionCfg(
        slippage_bps=5,
        brokerage_per_order_inr=20,
        stt_pct=0.025,
        exchange_txn_pct=0.00345,
        gst_pct=18.0,
        signal_cooldown_minutes=30,
    )


@pytest.fixture
def guardrails(risk_cfg, market_cfg, execution_cfg) -> Guardrails:
    return Guardrails(risk_cfg, market_cfg, execution_cfg)


@pytest.fixture
def base_signal() -> Signal:
    return Signal(
        symbol="RELIANCE",
        side=Side.BUY,
        strategy="trend_breakout",
        regime=Regime.TREND,
        entry_price=1300.0,
        stop_loss=1280.0,
        target=1360.0,
        confidence=0.6,
        rationale="test",
        ts=datetime(2026, 5, 13, 10, 30),
    )


@pytest.fixture
def market_ctx() -> MarketContext:
    return MarketContext(
        now=datetime(2026, 5, 13, 10, 30),
        nifty_ltp=22000.0,
        nifty_change_pct_15m=0.1,
        vix=14.0,
        vix_change_pct_15m=0.5,
        last_tick_age_seconds=1.0,
        avg_daily_volumes={"RELIANCE": 10_000_000},
        spread_pct_by_symbol={"RELIANCE": 0.05},
    )


@pytest.fixture
def portfolio() -> PortfolioState:
    return PortfolioState(
        equity=100_000.0,
        starting_equity_today=100_000.0,
        peak_equity=100_000.0,
        open_positions=[],
        realized_pnl_today=0.0,
        last_exit_by_symbol={},
        halted=False,
    )
