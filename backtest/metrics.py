"""Performance metrics for the backtest gate.

All metrics computed from a list of TradeRecord + an equity series. No external deps
beyond pandas/numpy so the gate is reproducible across machines.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

import numpy as np
import pandas as pd

from core.broker.paper import TradeRecord


@dataclass
class BacktestMetrics:
    n_trades: int
    win_rate_pct: float
    profit_factor: float
    avg_win: float
    avg_loss: float
    total_pnl: float
    sharpe: float
    sortino: float
    max_drawdown_pct: float
    cagr_pct: float
    expectancy: float
    starting_equity: float
    ending_equity: float
    period_days: int

    def to_dict(self) -> dict:
        return {k: getattr(self, k) for k in self.__dataclass_fields__}


def compute_metrics(
    *,
    trades: Iterable[TradeRecord],
    equity_curve: pd.Series,
    starting_equity: float,
    period_days: int,
    risk_free_rate_annual: float = 0.07,  # Indian risk-free ~7% (G-Sec proxy)
    trading_days_per_year: int = 252,
) -> BacktestMetrics:
    trades = list(trades)
    n = len(trades)
    pnls = np.array([t.pnl for t in trades], dtype=float) if trades else np.array([])
    wins = pnls[pnls > 0]
    losses = pnls[pnls < 0]

    win_rate = (len(wins) / n * 100.0) if n else 0.0
    avg_win = float(wins.mean()) if len(wins) else 0.0
    avg_loss = float(losses.mean()) if len(losses) else 0.0
    total_pnl = float(pnls.sum()) if n else 0.0

    gross_profit = float(wins.sum()) if len(wins) else 0.0
    gross_loss = float(-losses.sum()) if len(losses) else 0.0
    profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else float("inf") if gross_profit > 0 else 0.0
    expectancy = (total_pnl / n) if n else 0.0

    if equity_curve.empty:
        return BacktestMetrics(
            n_trades=n, win_rate_pct=win_rate, profit_factor=profit_factor,
            avg_win=avg_win, avg_loss=avg_loss, total_pnl=total_pnl,
            sharpe=0.0, sortino=0.0, max_drawdown_pct=0.0,
            cagr_pct=0.0, expectancy=expectancy,
            starting_equity=starting_equity, ending_equity=starting_equity,
            period_days=period_days,
        )

    ending = float(equity_curve.iloc[-1])
    daily_returns = equity_curve.pct_change().dropna()

    rf_daily = (1 + risk_free_rate_annual) ** (1 / trading_days_per_year) - 1
    excess = daily_returns - rf_daily
    std = excess.std()
    sharpe = float(excess.mean() / std * np.sqrt(trading_days_per_year)) if std > 0 else 0.0

    downside = excess[excess < 0]
    dstd = downside.std()
    sortino = float(excess.mean() / dstd * np.sqrt(trading_days_per_year)) if dstd and dstd > 0 else 0.0

    running_peak = equity_curve.cummax()
    drawdowns = (running_peak - equity_curve) / running_peak
    max_dd_pct = float(drawdowns.max() * 100.0) if not drawdowns.empty else 0.0

    years = max(period_days / 365.25, 1e-9)
    cagr = ((ending / starting_equity) ** (1 / years) - 1) * 100.0 if starting_equity > 0 else 0.0

    return BacktestMetrics(
        n_trades=n,
        win_rate_pct=win_rate,
        profit_factor=profit_factor,
        avg_win=avg_win,
        avg_loss=avg_loss,
        total_pnl=total_pnl,
        sharpe=sharpe,
        sortino=sortino,
        max_drawdown_pct=max_dd_pct,
        cagr_pct=float(cagr),
        expectancy=expectancy,
        starting_equity=starting_equity,
        ending_equity=ending,
        period_days=period_days,
    )
