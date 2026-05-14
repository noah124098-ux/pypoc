"""Tests for backtest metrics. Use synthetic equity curves so results are deterministic."""
from __future__ import annotations

from datetime import datetime, timedelta

import numpy as np
import pandas as pd
import pytest

from backtest.metrics import compute_metrics
from core.broker.paper import TradeRecord
from core.types import Side


def _trade(pnl: float) -> TradeRecord:
    return TradeRecord(
        symbol="X", side=Side.SELL, qty=1, entry_price=100, exit_price=100 + pnl,
        pnl=pnl, charges=0, strategy="t",
        opened_at=datetime(2026, 1, 1), closed_at=datetime(2026, 1, 2),
        exit_reason="manual",
    )


def test_empty_inputs_safe():
    m = compute_metrics(
        trades=[], equity_curve=pd.Series(dtype=float),
        starting_equity=100_000, period_days=30,
    )
    assert m.n_trades == 0
    assert m.win_rate_pct == 0
    assert m.sharpe == 0
    assert m.max_drawdown_pct == 0


def test_win_rate_and_profit_factor():
    trades = [_trade(100), _trade(200), _trade(-50), _trade(-50)]
    eq = pd.Series([100_000, 100_100, 100_300, 100_250, 100_200],
                   index=pd.date_range("2026-01-01", periods=5, freq="D"))
    m = compute_metrics(trades=trades, equity_curve=eq,
                        starting_equity=100_000, period_days=4)
    assert m.n_trades == 4
    assert m.win_rate_pct == 50.0
    # gross profit 300 / gross loss 100 = 3.0
    assert m.profit_factor == pytest.approx(3.0)
    assert m.expectancy == pytest.approx(50.0)


def test_max_drawdown():
    eq = pd.Series([100, 110, 105, 90, 95, 100],
                   index=pd.date_range("2026-01-01", periods=6, freq="D"))
    m = compute_metrics(trades=[], equity_curve=eq,
                        starting_equity=100, period_days=5)
    # Peak = 110, trough = 90, dd = (110-90)/110 = 18.18%
    assert m.max_drawdown_pct == pytest.approx(18.18, abs=0.1)


def test_positive_sharpe_for_steady_uptrend():
    eq = pd.Series(np.linspace(100_000, 110_000, 252),
                   index=pd.date_range("2026-01-01", periods=252, freq="B"))
    m = compute_metrics(trades=[], equity_curve=eq,
                        starting_equity=100_000, period_days=365)
    assert m.sharpe > 0
    assert m.cagr_pct == pytest.approx(10.0, abs=2.0)


def test_negative_sharpe_for_downtrend():
    eq = pd.Series(np.linspace(100_000, 90_000, 252),
                   index=pd.date_range("2026-01-01", periods=252, freq="B"))
    m = compute_metrics(trades=[], equity_curve=eq,
                        starting_equity=100_000, period_days=365)
    assert m.sharpe < 0


def test_profit_factor_inf_when_no_losses():
    trades = [_trade(100), _trade(50)]
    eq = pd.Series([100, 200],
                   index=pd.date_range("2026-01-01", periods=2, freq="D"))
    m = compute_metrics(trades=trades, equity_curve=eq,
                        starting_equity=100, period_days=1)
    assert m.profit_factor == float("inf")
