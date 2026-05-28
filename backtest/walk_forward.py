"""Walk-forward harness.

The honest way to validate a strategy: simulate as if we were running it
month-by-month, never seeing future data. We slice [start, end] into
contiguous windows of `window_months` each and run a backtest per window,
then aggregate.

This is NOT parameter optimization (which would tune on in-sample and test
on out-of-sample). It's a robustness check: does the strategy hold up
across different market regimes within the historical window?
"""
from __future__ import annotations

import logging
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

import pandas as pd

from backtest.engine import BacktestEngine, BacktestResult
from backtest.metrics import BacktestMetrics, compute_metrics
from core.config import Settings

log = logging.getLogger("backtest.wf")


@dataclass
class WalkForwardWindow:
    start: datetime
    end: datetime
    result: BacktestResult
    metrics: BacktestMetrics


@dataclass
class WalkForwardReport:
    windows: list[WalkForwardWindow]
    aggregate_metrics: BacktestMetrics
    starting_equity: float

    @property
    def n_windows(self) -> int:
        return len(self.windows)


def run_walk_forward(
    *,
    settings: Settings,
    symbol_history: dict[str, pd.DataFrame],
    nifty_history: pd.DataFrame,
    starting_equity: float,
    start_date: datetime,
    end_date: datetime,
    window_months: int = 12,
) -> WalkForwardReport:
    """Run sequential, non-overlapping windows of `window_months` each.

    Each window starts with the original starting equity (so windows are
    independent). Aggregate metrics are computed by concatenating equity
    curves and trade lists.
    """
    if window_months < 1:
        raise ValueError("window_months must be >= 1")

    # Build window date ranges first, then run in parallel.
    cursor = pd.Timestamp(start_date)
    end_ts = pd.Timestamp(end_date)
    date_ranges: list[tuple[pd.Timestamp, pd.Timestamp]] = []
    while cursor < end_ts:
        win_end = min(cursor + pd.DateOffset(months=window_months), end_ts)
        date_ranges.append((cursor, win_end))
        cursor = win_end

    n_workers = min(len(date_ranges), 4)
    futures_map = {}
    windows_map: dict[int, WalkForwardWindow] = {}

    with ProcessPoolExecutor(max_workers=n_workers) as pool:
        for i, (ws, we) in enumerate(date_ranges):
            f = pool.submit(
                _run_window,
                settings, symbol_history, nifty_history,
                starting_equity, ws.to_pydatetime(), we.to_pydatetime(),
                (we - ws).days,
            )
            futures_map[f] = i

        for f in as_completed(futures_map):
            i = futures_map[f]
            ws, we = date_ranges[i]
            try:
                windows_map[i] = f.result()
            except ValueError as e:
                log.warning("Skipping window %s..%s: %s", ws.date(), we.date(), e)

    windows = [windows_map[i] for i in sorted(windows_map)]
    aggregate = _aggregate(windows, starting_equity)
    return WalkForwardReport(
        windows=windows,
        aggregate_metrics=aggregate,
        starting_equity=starting_equity,
    )


def _run_window(
    settings: Settings,
    symbol_history: dict[str, pd.DataFrame],
    nifty_history: pd.DataFrame,
    starting_equity: float,
    start: datetime,
    end: datetime,
    period_days: int,
) -> WalkForwardWindow:
    """Run a single backtest window. Executed in a worker process."""
    engine = BacktestEngine(settings)
    result = engine.run(
        symbol_history=symbol_history,
        nifty_history=nifty_history,
        starting_equity=starting_equity,
        start_date=start,
        end_date=end,
    )
    m = compute_metrics(
        trades=result.trades,
        equity_curve=result.equity_curve,
        starting_equity=starting_equity,
        period_days=period_days,
    )
    return WalkForwardWindow(start=start, end=end, result=result, metrics=m)


def _aggregate(windows: list[WalkForwardWindow], starting_equity: float) -> BacktestMetrics:
    if not windows:
        return compute_metrics(
            trades=[], equity_curve=pd.Series(dtype=float),
            starting_equity=starting_equity, period_days=0,
        )
    all_trades = [t for w in windows for t in w.result.trades]
    # Stitch equity curves, normalizing each window to start at the previous window's ending equity.
    pieces: list[pd.Series] = []
    running = starting_equity
    for w in windows:
        ec = w.result.equity_curve
        if ec.empty:
            continue
        scale = running / ec.iloc[0]
        scaled = ec * scale
        pieces.append(scaled)
        running = float(scaled.iloc[-1])
    combined = pd.concat(pieces).sort_index() if pieces else pd.Series(dtype=float)
    period_days = sum((w.end - w.start).days for w in windows)
    return compute_metrics(
        trades=all_trades,
        equity_curve=combined,
        starting_equity=starting_equity,
        period_days=period_days,
    )
