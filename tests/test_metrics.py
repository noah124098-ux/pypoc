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


# ===========================================================================
# Extended metrics engine tests
# ===========================================================================

from core.analytics.metrics import (
    ExtendedMetrics,
    compute_extended_metrics,
    compute_strategy_attribution,
    compute_regime_attribution,
    compute_monthly_pnl,
)


def _ext_trade(
    pnl: float,
    strategy: str = "trend_breakout",
    days_offset: int = 0,
) -> TradeRecord:
    base = datetime(2024, 1, 2)
    return TradeRecord(
        symbol="NIFTY",
        side=Side.SELL,
        qty=1,
        entry_price=100.0,
        exit_price=100.0 + pnl,
        pnl=pnl,
        charges=max(abs(pnl) * 0.01, 1.0),
        strategy=strategy,
        opened_at=base + timedelta(days=days_offset),
        closed_at=base + timedelta(days=days_offset + 1),
        exit_reason="target" if pnl > 0 else "stop_loss",
    )


# --- Edge cases: empty / single trade ---

def test_em_empty_list_returns_zero_metrics():
    m = compute_extended_metrics([])
    assert isinstance(m, ExtendedMetrics)
    assert m.n_trades == 0
    assert m.sharpe == 0.0
    assert m.max_drawdown_pct == 0.0
    assert m.win_rate_pct == 0.0
    assert m.profit_factor == 0.0


def test_em_single_winning_trade():
    m = compute_extended_metrics([_ext_trade(200.0, days_offset=0)])
    assert m.n_trades == 1
    assert m.win_rate_pct == 100.0
    assert m.total_pnl == pytest.approx(200.0)
    assert m.consecutive_wins_max == 1
    assert m.consecutive_losses_max == 0
    assert m.profit_factor == float("inf")
    assert m.largest_win_inr == pytest.approx(200.0)
    assert m.largest_loss_inr == 0.0


def test_em_single_losing_trade():
    m = compute_extended_metrics([_ext_trade(-150.0, days_offset=0)])
    assert m.n_trades == 1
    assert m.win_rate_pct == 0.0
    assert m.total_pnl == pytest.approx(-150.0)
    assert m.consecutive_losses_max == 1
    assert m.profit_factor == 0.0
    assert m.largest_loss_inr == pytest.approx(-150.0)
    assert m.largest_win_inr == 0.0


# --- All wins ---

def test_em_all_wins():
    trades = [_ext_trade(100.0, days_offset=i) for i in range(10)]
    m = compute_extended_metrics(trades)
    assert m.win_rate_pct == 100.0
    assert m.profit_factor == float("inf")
    assert m.total_pnl == pytest.approx(1000.0)
    assert m.consecutive_wins_max == 10
    assert m.consecutive_losses_max == 0
    assert m.payoff_ratio == float("inf")  # avg_loss == 0


# --- All losses ---

def test_em_all_losses():
    trades = [_ext_trade(-80.0, days_offset=i) for i in range(5)]
    m = compute_extended_metrics(trades)
    assert m.win_rate_pct == 0.0
    assert m.profit_factor == 0.0
    assert m.total_pnl == pytest.approx(-400.0)
    assert m.consecutive_losses_max == 5
    assert m.consecutive_wins_max == 0


# --- Sharpe: zero variance (all returns equal) ---

def test_em_sharpe_zero_variance():
    # All trades same pnl -> after resample the daily series is constant -> std ~ 0
    trades = [_ext_trade(50.0, days_offset=i) for i in range(60)]
    m = compute_extended_metrics(trades)
    # With near-zero variance Sharpe should be 0 (not NaN, not error)
    assert isinstance(m.sharpe, float)
    assert not np.isnan(m.sharpe)


# --- Calmar: zero drawdown ---

def test_em_calmar_zero_drawdown():
    # Strictly increasing equity -> drawdown never occurs -> calmar 0
    trades = [_ext_trade(float(50 * (i + 1)), days_offset=i) for i in range(10)]
    m = compute_extended_metrics(trades)
    # max_dd_inr should be 0 since cumulative P&L only goes up
    assert m.calmar_ratio == 0.0 or np.isinf(m.calmar_ratio)


# --- Win/loss ratio and profit factor ---

def test_em_win_loss_ratio():
    trades = (
        [_ext_trade(100.0, days_offset=i) for i in range(6)]
        + [_ext_trade(-50.0, days_offset=6 + i) for i in range(2)]
    )
    m = compute_extended_metrics(trades)
    assert m.win_rate_pct == pytest.approx(75.0)
    assert m.win_loss_ratio == pytest.approx(3.0)  # 6 wins / 2 losses
    assert m.profit_factor == pytest.approx(600.0 / 100.0)


# --- Consecutive streaks ---

def test_em_consecutive_streaks():
    pnls = [100, 100, -50, 100, 100, 100, -50, -50, -50]
    trades = [_ext_trade(p, days_offset=i) for i, p in enumerate(pnls)]
    m = compute_extended_metrics(trades)
    assert m.consecutive_wins_max == 3
    assert m.consecutive_losses_max == 3


# --- Trade frequency ---

def test_em_trade_frequency():
    trades = [_ext_trade(10.0, days_offset=i * 10) for i in range(5)]
    m = compute_extended_metrics(trades)
    # 5 trades over 40 days = 0.125 per day
    assert m.trade_frequency_per_day == pytest.approx(5 / 40.0, rel=0.05)


# --- Strategy attribution ---

def test_em_strategy_attribution_splits_correctly():
    trades = (
        [_ext_trade(100.0, strategy="trend_breakout", days_offset=i) for i in range(4)]
        + [_ext_trade(-30.0, strategy="rsi_mean_reversion", days_offset=4 + i) for i in range(3)]
    )
    attr = compute_strategy_attribution(trades)
    assert "trend_breakout" in attr
    assert "rsi_mean_reversion" in attr
    assert attr["trend_breakout"].n_trades == 4
    assert attr["rsi_mean_reversion"].n_trades == 3
    assert attr["trend_breakout"].win_rate_pct == 100.0
    assert attr["rsi_mean_reversion"].win_rate_pct == 0.0


# --- Regime attribution ---

def test_em_regime_attribution_maps_strategies():
    trades = (
        [_ext_trade(100.0, strategy="trend_breakout", days_offset=i) for i in range(3)]
        + [_ext_trade(50.0, strategy="rsi_mean_reversion", days_offset=3 + i) for i in range(2)]
        + [_ext_trade(-20.0, strategy="bb_squeeze", days_offset=5 + i) for i in range(2)]
    )
    attr = compute_regime_attribution(trades)
    assert "TREND" in attr
    assert "RANGE" in attr
    assert "VOLATILE" in attr
    assert attr["TREND"].n_trades == 3
    assert attr["RANGE"].n_trades == 2
    assert attr["VOLATILE"].n_trades == 2


def test_em_regime_attribution_unknown_strategy():
    trades = [_ext_trade(10.0, strategy="my_custom_strat", days_offset=i) for i in range(3)]
    attr = compute_regime_attribution(trades)
    assert "UNKNOWN" in attr
    assert attr["UNKNOWN"].n_trades == 3


# --- Monthly P&L ---

def test_em_compute_monthly_pnl_empty():
    df = compute_monthly_pnl([])
    assert isinstance(df, pd.DataFrame)
    assert len(df) == 0
    assert set(df.columns) == {"pnl", "n_trades", "win_rate"}


def test_em_compute_monthly_pnl_groups_by_month():
    # Jan 2024: 3 trades (2 wins, 1 loss)
    # Feb 2024: 2 trades (both wins)
    jan_trades = [
        TradeRecord("X", Side.SELL, 1, 100, 200, 100.0, 1.0, "t",
                    datetime(2024, 1, 2), datetime(2024, 1, 3), "target"),
        TradeRecord("X", Side.SELL, 1, 100, 150, 50.0, 1.0, "t",
                    datetime(2024, 1, 10), datetime(2024, 1, 11), "target"),
        TradeRecord("X", Side.SELL, 1, 100, 80, -20.0, 1.0, "t",
                    datetime(2024, 1, 20), datetime(2024, 1, 21), "stop_loss"),
    ]
    feb_trades = [
        TradeRecord("X", Side.SELL, 1, 100, 120, 20.0, 1.0, "t",
                    datetime(2024, 2, 5), datetime(2024, 2, 6), "target"),
        TradeRecord("X", Side.SELL, 1, 100, 130, 30.0, 1.0, "t",
                    datetime(2024, 2, 15), datetime(2024, 2, 16), "target"),
    ]
    df = compute_monthly_pnl(jan_trades + feb_trades)
    assert "2024-01" in df.index
    assert "2024-02" in df.index
    assert df.loc["2024-01", "n_trades"] == 3
    assert df.loc["2024-01", "pnl"] == pytest.approx(130.0)
    assert df.loc["2024-01", "win_rate"] == pytest.approx(200.0 / 3.0, rel=0.01)
    assert df.loc["2024-02", "n_trades"] == 2
    assert df.loc["2024-02", "win_rate"] == pytest.approx(100.0)


# --- ExtendedMetrics.to_dict ---

def test_em_to_dict_is_json_serialisable():
    import json
    trades = [_ext_trade(100.0, days_offset=i) for i in range(5)]
    m = compute_extended_metrics(trades)
    d = m.to_dict()
    assert isinstance(d, dict)
    # Replace inf with None for JSON compatibility check
    cleaned = {k: (None if (isinstance(v, float) and np.isinf(v)) else v) for k, v in d.items()}
    serialised = json.dumps(cleaned)
    assert "n_trades" in serialised


# --- R multiples ---

def test_em_r_multiples_sensible():
    trades = (
        [_ext_trade(200.0, days_offset=i) for i in range(3)]   # wins
        + [_ext_trade(-100.0, days_offset=3 + i) for i in range(3)]  # losses
    )
    m = compute_extended_metrics(trades)
    # avg_win_r should be positive, avg_loss_r should be negative
    assert m.avg_win_r > 0
    assert m.avg_loss_r < 0
    # payoff: avg_win=200, avg_loss=-100 → ratio=2
    assert m.payoff_ratio == pytest.approx(2.0, rel=0.05)
