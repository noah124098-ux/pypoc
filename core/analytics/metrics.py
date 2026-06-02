"""Analytics helpers: compute per-strategy / per-regime metrics from the trades table.

Two API layers:
  1. Legacy dict-based helpers (strategy_performance, regime_performance, trade_analytics,
     monthly_summary) — accept list[dict] and are used by mcp_server/tools.py.
  2. Extended metrics engine (compute_extended_metrics, compute_strategy_attribution,
     compute_regime_attribution, compute_monthly_pnl) — accept list[TradeRecord] and
     return rich dataclass / DataFrame results.

Dependencies: stdlib + pandas + numpy only.
"""
from __future__ import annotations

import math
import sqlite3
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional, Union

import numpy as np
import pandas as pd


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _safe_div(num: float, denom: float, default: float = 0.0) -> float:
    return num / denom if denom else default


def _pf(wins_sum: float, losses_sum: float) -> float:
    """Profit factor = gross_profit / gross_loss."""
    return _safe_div(wins_sum, abs(losses_sum)) if losses_sum < 0 else (wins_sum if wins_sum > 0 else 0.0)


def _sharpe(pnls: list[float]) -> float:
    """Daily-ish Sharpe: mean(pnl) / std(pnl) * sqrt(252).  Returns 0 if < 2 trades."""
    if len(pnls) < 2:
        return 0.0
    n = len(pnls)
    mean = sum(pnls) / n
    variance = sum((x - mean) ** 2 for x in pnls) / (n - 1)
    std = math.sqrt(variance) if variance > 0 else 0.0
    return _safe_div(mean, std) * math.sqrt(252) if std else 0.0


def _parse_dt(s: str | None) -> datetime | None:
    if not s:
        return None
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(s[:19], fmt[:len(fmt)])
        except ValueError:
            continue
    return None


def _holding_minutes(trade: dict) -> float | None:
    opened = _parse_dt(trade.get("opened_at"))
    closed = _parse_dt(trade.get("closed_at"))
    if opened and closed:
        return (closed - opened).total_seconds() / 60.0
    return None


# ---------------------------------------------------------------------------
# public API
# ---------------------------------------------------------------------------

def strategy_performance(trades: list[dict]) -> list[dict[str, Any]]:
    """Compute per-strategy metrics from a list of closed trade dicts."""
    groups: dict[str, list[dict]] = defaultdict(list)
    for t in trades:
        groups[t.get("strategy") or "unknown"].append(t)

    result = []
    for strategy, ts in sorted(groups.items()):
        pnls = [float(t["pnl"]) for t in ts]
        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p <= 0]
        result.append(
            {
                "strategy": strategy,
                "trades": len(ts),
                "win_rate": round(_safe_div(len(wins), len(ts)) * 100, 2),
                "profit_factor": round(_pf(sum(wins), sum(losses)), 3),
                "net_pnl": round(sum(pnls), 2),
                "sharpe": round(_sharpe(pnls), 3),
            }
        )
    return result


def regime_performance(trades: list[dict]) -> list[dict[str, Any]]:
    """Compute per-regime metrics.  The regime column is read from the signal
    that triggered the trade; trades without regime are grouped as 'UNKNOWN'."""
    groups: dict[str, list[dict]] = defaultdict(list)
    for t in trades:
        regime = t.get("regime") or "UNKNOWN"
        groups[regime].append(t)

    result = []
    for regime, ts in sorted(groups.items()):
        pnls = [float(t["pnl"]) for t in ts]
        wins = [p for p in pnls if p > 0]
        result.append(
            {
                "regime": regime,
                "trades": len(ts),
                "win_rate": round(_safe_div(len(wins), len(ts)) * 100, 2),
                "avg_pnl": round(_safe_div(sum(pnls), len(ts)), 2),
            }
        )
    return result


def trade_analytics(trades: list[dict]) -> dict[str, Any]:
    """Consolidated analytics for a trade list."""
    if not trades:
        return {"trade_count": 0, "available": False}

    pnls = [float(t["pnl"]) for t in trades]
    charges = [float(t.get("charges") or 0) for t in trades]
    total_pnl = sum(pnls)
    gross_profit = sum(p for p in pnls if p > 0)
    gross_loss = sum(p for p in pnls if p < 0)

    best = max(trades, key=lambda t: float(t["pnl"]))
    worst = min(trades, key=lambda t: float(t["pnl"]))

    # consecutive win/loss streak (current streak at end of time-ordered list)
    sorted_trades = sorted(trades, key=lambda t: t.get("closed_at") or "")
    streak = 0
    if sorted_trades:
        last_sign = 1 if float(sorted_trades[-1]["pnl"]) > 0 else -1
        for t in reversed(sorted_trades):
            sign = 1 if float(t["pnl"]) > 0 else -1
            if sign == last_sign:
                streak += sign
            else:
                break

    # average holding time
    holding_times = [m for t in trades if (m := _holding_minutes(t)) is not None]
    avg_holding_minutes = round(_safe_div(sum(holding_times), len(holding_times)), 1) if holding_times else None

    # per-strategy pnl for best/worst
    strat_pnl: dict[str, float] = defaultdict(float)
    strat_count: dict[str, int] = defaultdict(int)
    regime_pnl: dict[str, float] = defaultdict(float)
    regime_count: dict[str, int] = defaultdict(int)
    for t in trades:
        s = t.get("strategy") or "unknown"
        r = t.get("regime") or "UNKNOWN"
        strat_pnl[s] += float(t["pnl"])
        strat_count[s] += 1
        regime_pnl[r] += float(t["pnl"])
        regime_count[r] += 1

    best_strategy = max(strat_pnl, key=strat_pnl.__getitem__) if strat_pnl else None
    worst_strategy = min(strat_pnl, key=strat_pnl.__getitem__) if strat_pnl else None
    best_regime = max(regime_pnl, key=regime_pnl.__getitem__) if regime_pnl else None
    worst_regime = min(regime_pnl, key=regime_pnl.__getitem__) if regime_pnl else None

    return {
        "available": True,
        "trade_count": len(trades),
        "total_net_pnl": round(total_pnl, 2),
        "gross_profit": round(gross_profit, 2),
        "gross_loss": round(gross_loss, 2),
        "total_charges": round(sum(charges), 2),
        "charges_pct_of_gross": round(
            _safe_div(sum(charges), gross_profit) * 100, 2
        ) if gross_profit else 0.0,
        "best_trade": {
            "symbol": best.get("symbol"),
            "pnl": round(float(best["pnl"]), 2),
            "strategy": best.get("strategy"),
            "closed_at": best.get("closed_at"),
        },
        "worst_trade": {
            "symbol": worst.get("symbol"),
            "pnl": round(float(worst["pnl"]), 2),
            "strategy": worst.get("strategy"),
            "closed_at": worst.get("closed_at"),
        },
        "current_streak": streak,
        "avg_holding_minutes": avg_holding_minutes,
        "best_strategy": best_strategy,
        "best_strategy_pnl": round(strat_pnl[best_strategy], 2) if best_strategy else None,
        "worst_strategy": worst_strategy,
        "worst_strategy_pnl": round(strat_pnl[worst_strategy], 2) if worst_strategy else None,
        "best_regime": best_regime,
        "best_regime_pnl": round(regime_pnl[best_regime], 2) if best_regime else None,
        "worst_regime": worst_regime,
        "worst_regime_pnl": round(regime_pnl[worst_regime], 2) if worst_regime else None,
    }


def monthly_summary(trades: list[dict]) -> list[dict[str, Any]]:
    """Group trades by (year, month) and return P&L summary rows."""
    groups: dict[tuple[int, int], list[dict]] = defaultdict(list)
    for t in trades:
        closed = _parse_dt(t.get("closed_at"))
        if closed:
            groups[(closed.year, closed.month)].append(t)

    result = []
    for (year, month), ts in sorted(groups.items()):
        pnls = [float(t["pnl"]) for t in ts]
        wins = [p for p in pnls if p > 0]
        result.append(
            {
                "year": year,
                "month": month,
                "pnl": round(sum(pnls), 2),
                "trades": len(ts),
                "win_rate": round(_safe_div(len(wins), len(ts)) * 100, 2),
            }
        )
    return result


# ===========================================================================
# Extended metrics engine — accepts list[TradeRecord]
# ===========================================================================

_RISK_FREE_ANNUAL = 0.07        # Indian G-Sec proxy (~7%)
_TRADING_DAYS_PER_YEAR = 252


@dataclass
class ExtendedMetrics:
    """Comprehensive performance metrics computed from a list[TradeRecord]."""

    # summary
    n_trades: int = 0
    total_pnl: float = 0.0

    # risk-adjusted returns
    sharpe: float = 0.0
    sortino: float = 0.0
    calmar_ratio: float = 0.0
    omega_ratio: float = 0.0

    # drawdown
    max_drawdown_pct: float = 0.0
    avg_drawdown_pct: float = 0.0
    drawdown_duration_avg_days: float = 0.0

    # win/loss
    win_rate_pct: float = 0.0
    win_loss_ratio: float = 0.0
    profit_factor: float = 0.0
    expectancy: float = 0.0

    # R multiples
    avg_win_r: float = 0.0
    avg_loss_r: float = 0.0

    # extremes
    largest_win_inr: float = 0.0
    largest_loss_inr: float = 0.0

    # streaks
    consecutive_wins_max: int = 0
    consecutive_losses_max: int = 0

    # composite
    recovery_factor: float = 0.0
    payoff_ratio: float = 0.0

    # activity
    trade_frequency_per_day: float = 0.0

    def to_dict(self) -> dict:
        return {k: getattr(self, k) for k in self.__dataclass_fields__}


# ---------------------------------------------------------------------------
# Internal helpers for extended metrics
# ---------------------------------------------------------------------------

def _em_equity_curve(trades: list) -> pd.Series:
    """Build a cumulative daily P&L equity curve from a list of TradeRecord."""
    if not trades:
        return pd.Series(dtype=float)
    rows = [{"date": t.closed_at.date(), "pnl": t.pnl} for t in trades]
    df = pd.DataFrame(rows)
    daily = df.groupby("date")["pnl"].sum().sort_index()
    return daily.cumsum()


def _em_drawdown_stats(equity: pd.Series) -> tuple[float, float, float]:
    """Return (max_drawdown_pct, avg_drawdown_pct, avg_duration_days)."""
    if equity.empty:
        return 0.0, 0.0, 0.0

    peak = equity.cummax()
    # Compute pct drawdown only in positive-peak territory
    dd_pct = pd.Series(0.0, index=equity.index)
    mask = peak > 0
    if mask.any():
        dd_pct[mask] = (peak[mask] - equity[mask]) / peak[mask] * 100.0

    max_dd = float(dd_pct.max()) if not dd_pct.empty else 0.0

    # Episode analysis
    in_dd = dd_pct > 0
    episode_pcts: list[float] = []
    episode_durations: list[int] = []
    cur_start: Optional[int] = None
    cur_pcts: list[float] = []

    for i, v in enumerate(in_dd):
        if v:
            if cur_start is None:
                cur_start = i
            cur_pcts.append(float(dd_pct.iloc[i]))
        else:
            if cur_start is not None:
                episode_pcts.append(float(np.max(cur_pcts)))
                episode_durations.append(i - cur_start)
                cur_start = None
                cur_pcts = []

    if cur_start is not None:
        episode_pcts.append(float(np.max(cur_pcts)))
        episode_durations.append(len(in_dd) - cur_start)

    avg_dd = float(np.mean(episode_pcts)) if episode_pcts else 0.0
    avg_dur = float(np.mean(episode_durations)) if episode_durations else 0.0
    return max_dd, avg_dd, avg_dur


def _em_max_dd_inr(equity: pd.Series) -> float:
    """Max absolute drawdown in INR."""
    if equity.empty:
        return 0.0
    peak = equity.cummax()
    return float((peak - equity).max())


def _em_streak_stats(pnls: np.ndarray) -> tuple[int, int]:
    """Return (max_consecutive_wins, max_consecutive_losses)."""
    if len(pnls) == 0:
        return 0, 0
    max_w = max_l = cur_w = cur_l = 0
    for p in pnls:
        if p > 0:
            cur_w += 1
            cur_l = 0
        elif p < 0:
            cur_l += 1
            cur_w = 0
        else:
            cur_w = cur_l = 0
        max_w = max(max_w, cur_w)
        max_l = max(max_l, cur_l)
    return max_w, max_l


def _em_r_multiples(wins: list, losses: list) -> tuple[float, float]:
    """Return (avg_win_r, avg_loss_r) normalized by average realized loss."""
    if not losses:
        avg_w = float(np.mean([t.pnl for t in wins])) if wins else 0.0
        return avg_w, 0.0
    avg_risk = float(np.mean([abs(t.pnl) for t in losses]))
    if avg_risk <= 0:
        return 0.0, 0.0
    avg_win_r = float(np.mean([t.pnl / avg_risk for t in wins])) if wins else 0.0
    avg_loss_r = float(np.mean([t.pnl / avg_risk for t in losses]))
    return avg_win_r, avg_loss_r


def _em_sharpe_sortino(date_pnl: pd.Series) -> tuple[float, float]:
    """Sharpe and Sortino from a daily P&L series."""
    if date_pnl.empty or len(date_pnl) < 2:
        return 0.0, 0.0

    cum = date_pnl.cumsum()
    shifted = cum - cum.iloc[0] + max(abs(cum.min()) + 1.0, 1.0)
    daily_ret = shifted.pct_change().dropna()

    if daily_ret.empty or daily_ret.std() < 1e-12:
        return 0.0, 0.0

    rf_daily = (1 + _RISK_FREE_ANNUAL) ** (1 / _TRADING_DAYS_PER_YEAR) - 1
    excess = daily_ret - rf_daily
    std = excess.std()
    sharpe = float(excess.mean() / std * np.sqrt(_TRADING_DAYS_PER_YEAR)) if std > 1e-12 else 0.0

    downside = excess[excess < 0]
    dstd = downside.std() if len(downside) > 1 else 0.0
    sortino = float(excess.mean() / dstd * np.sqrt(_TRADING_DAYS_PER_YEAR)) if dstd > 1e-12 else 0.0
    return sharpe, sortino


def _em_calmar(total_pnl: float, max_dd_inr: float, period_days: int) -> float:
    if max_dd_inr <= 0 or period_days <= 0:
        return 0.0
    annualised = total_pnl * 365.25 / period_days
    return float(annualised / max_dd_inr)


def _em_omega(date_pnl: pd.Series, threshold: float = 0.0) -> float:
    if date_pnl.empty:
        return 0.0
    gains = float(date_pnl[date_pnl > threshold].sum())
    losses = float(abs(date_pnl[date_pnl < threshold].sum()))
    if losses <= 0:
        return float("inf") if gains > 0 else 1.0
    return gains / losses


# ---------------------------------------------------------------------------
# Public extended-metrics API
# ---------------------------------------------------------------------------

def compute_extended_metrics(trades: list) -> ExtendedMetrics:
    """Compute comprehensive performance metrics from a list[TradeRecord].

    Returns ExtendedMetrics dataclass (all fields are floats/ints — JSON-serialisable).
    """
    if not trades:
        return ExtendedMetrics()

    sorted_trades = sorted(trades, key=lambda t: t.closed_at)
    pnls = np.array([t.pnl for t in sorted_trades], dtype=float)
    wins = [t for t in sorted_trades if t.pnl > 0]
    losses = [t for t in sorted_trades if t.pnl < 0]

    n = len(sorted_trades)
    total_pnl = float(pnls.sum())

    win_rate = len(wins) / n * 100.0
    win_loss_ratio = (
        float(len(wins)) / len(losses) if losses
        else (float("inf") if wins else 0.0)
    )

    avg_win = float(np.mean([t.pnl for t in wins])) if wins else 0.0
    avg_loss = float(np.mean([t.pnl for t in losses])) if losses else 0.0

    gross_profit = float(sum(t.pnl for t in wins))
    gross_loss = float(abs(sum(t.pnl for t in losses)))
    profit_factor = (
        gross_profit / gross_loss if gross_loss > 0
        else (float("inf") if gross_profit > 0 else 0.0)
    )

    expectancy = total_pnl / n

    avg_win_r, avg_loss_r = _em_r_multiples(wins, losses)

    win_pnls = pnls[pnls > 0]
    loss_pnls = pnls[pnls < 0]
    largest_win = float(win_pnls.max()) if len(win_pnls) else 0.0
    largest_loss = float(loss_pnls.min()) if len(loss_pnls) else 0.0

    max_cons_wins, max_cons_losses = _em_streak_stats(pnls)

    equity = _em_equity_curve(sorted_trades)
    max_dd_pct, avg_dd_pct, avg_dd_dur = _em_drawdown_stats(equity)
    max_dd_inr = _em_max_dd_inr(equity)

    recovery_factor = (
        total_pnl / max_dd_inr if max_dd_inr > 0
        else (float("inf") if total_pnl > 0 else 0.0)
    )
    payoff_ratio = (
        avg_win / abs(avg_loss) if avg_loss != 0
        else (float("inf") if avg_win > 0 else 0.0)
    )

    date_pnl = pd.Series(
        [t.pnl for t in sorted_trades],
        index=pd.DatetimeIndex([t.closed_at for t in sorted_trades]),
    ).resample("D").sum()

    sharpe, sortino = _em_sharpe_sortino(date_pnl)

    first_dt = sorted_trades[0].closed_at
    last_dt = sorted_trades[-1].closed_at
    period_days = max((last_dt - first_dt).days, 1)

    calmar = _em_calmar(total_pnl, max_dd_inr, period_days)
    omega = _em_omega(date_pnl)
    freq = n / period_days

    return ExtendedMetrics(
        n_trades=n,
        total_pnl=total_pnl,
        sharpe=sharpe,
        sortino=sortino,
        calmar_ratio=calmar,
        omega_ratio=omega,
        max_drawdown_pct=max_dd_pct,
        avg_drawdown_pct=avg_dd_pct,
        drawdown_duration_avg_days=avg_dd_dur,
        win_rate_pct=win_rate,
        win_loss_ratio=win_loss_ratio,
        profit_factor=profit_factor,
        expectancy=expectancy,
        avg_win_r=avg_win_r,
        avg_loss_r=avg_loss_r,
        largest_win_inr=largest_win,
        largest_loss_inr=largest_loss,
        consecutive_wins_max=max_cons_wins,
        consecutive_losses_max=max_cons_losses,
        recovery_factor=recovery_factor,
        payoff_ratio=payoff_ratio,
        trade_frequency_per_day=freq,
    )


def compute_strategy_attribution(trades: list) -> dict[str, ExtendedMetrics]:
    """Per-strategy ExtendedMetrics breakdown, keyed by strategy name."""
    by_strategy: dict[str, list] = {}
    for t in trades:
        key = getattr(t, "strategy", None) or "unknown"
        by_strategy.setdefault(key, []).append(t)
    return {k: compute_extended_metrics(v) for k, v in by_strategy.items()}


_STRATEGY_REGIME_MAP: dict[str, str] = {
    "trend_breakout": "TREND",
    "rsi_momentum": "TREND",
    "supertrend": "TREND",
    "supertrend_short": "TREND",
    "rsi_mean_reversion": "RANGE",
    "mean_reversion": "RANGE",
    "bb_squeeze": "VOLATILE",
    "volatility_breakout": "VOLATILE",
}


def compute_regime_attribution(trades: list) -> dict[str, ExtendedMetrics]:
    """Per-regime ExtendedMetrics breakdown.

    Regime is inferred from the strategy name via _STRATEGY_REGIME_MAP.
    Trades with unrecognised strategy names are grouped under 'UNKNOWN'.
    """
    by_regime: dict[str, list] = {}
    for t in trades:
        strat = (getattr(t, "strategy", None) or "").lower()
        regime = _STRATEGY_REGIME_MAP.get(strat, "UNKNOWN")
        by_regime.setdefault(regime, []).append(t)
    return {k: compute_extended_metrics(v) for k, v in by_regime.items()}


def compute_monthly_pnl(trades: list) -> pd.DataFrame:
    """Return a DataFrame indexed by 'YYYY-MM' with columns: pnl, n_trades, win_rate.

    win_rate is expressed as a percentage (0–100).
    """
    if not trades:
        return pd.DataFrame(columns=["pnl", "n_trades", "win_rate"])

    rows = [
        {
            "year_month": t.closed_at.strftime("%Y-%m"),
            "pnl": t.pnl,
            "win": 1 if t.pnl > 0 else 0,
        }
        for t in trades
    ]
    df = pd.DataFrame(rows)
    agg = df.groupby("year_month").agg(
        pnl=("pnl", "sum"),
        n_trades=("pnl", "count"),
        win_rate=("win", "mean"),
    )
    agg["win_rate"] = agg["win_rate"] * 100.0
    agg.index.name = "year_month"
    return agg.sort_index()


def load_trades_from_db(db_path: Union[str, Path]) -> list:
    """Load all trades from a Store SQLite database and return list[TradeRecord].

    Useful for feeding compute_extended_metrics / compute_monthly_pnl from a
    live or archived database without importing the full orchestrator.
    """
    from core.broker.paper import TradeRecord
    from core.types import Side as _Side

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        cur = conn.execute("SELECT * FROM trades ORDER BY closed_at ASC")
        rows = cur.fetchall()
    finally:
        conn.close()

    result: list = []
    for row in rows:
        try:
            side = _Side.BUY if str(row["side"]).upper() == "BUY" else _Side.SELL
            result.append(TradeRecord(
                symbol=row["symbol"],
                side=side,
                qty=int(row["qty"]),
                entry_price=float(row["entry_price"]),
                exit_price=float(row["exit_price"]),
                pnl=float(row["pnl"]),
                charges=float(row["charges"]),
                strategy=row["strategy"] or "",
                opened_at=datetime.fromisoformat(row["opened_at"]),
                closed_at=datetime.fromisoformat(row["closed_at"]),
                exit_reason=row["exit_reason"] or "",
            ))
        except Exception:
            continue
    return result
