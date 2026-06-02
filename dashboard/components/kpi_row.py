"""KPI metrics row component for the live tab."""
from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

import streamlit as st

from dashboard.utils.charts import fmt_inr, time_ago, is_market_hours
from dashboard.utils.db import db_connect, query_df

AGENT_PID_PATH = Path("data/agent.pid")


def _agent_is_running() -> bool:
    """Check if the paper agent process is alive via PID file."""
    if not AGENT_PID_PATH.exists():
        return False
    try:
        pid = int(AGENT_PID_PATH.read_text().strip())
        import psutil
        return psutil.pid_exists(pid)
    except Exception:
        return False


def render_kpi_row(snap: dict, config: dict, conn) -> None:
    """Render the two-row KPI metrics display at the top of the live tab."""
    capital = config.get("capital", {}).get("initial_inr", 0)

    # Gather data once for KPI display
    _kpi_equity = float(snap.get("equity", capital or 0))
    _kpi_start_today = float(snap.get("starting_equity_today", _kpi_equity) or _kpi_equity)
    _kpi_peak = float(snap.get("peak_equity", _kpi_equity) or _kpi_equity)
    _kpi_day_pnl = _kpi_equity - _kpi_start_today
    _kpi_day_pnl_pct = (_kpi_day_pnl / _kpi_start_today * 100) if _kpi_start_today else 0.0
    _kpi_dd_pct = ((_kpi_peak - _kpi_equity) / _kpi_peak * 100) if _kpi_peak else 0.0
    _kpi_positions = snap.get("open_positions", [])
    _kpi_n_open = len(_kpi_positions)
    _kpi_max_pos = int(config.get("risk", {}).get("max_open_positions", 5))
    _kpi_regime = snap.get("regime", "UNKNOWN")
    _kpi_halted = snap.get("halted", False)
    _kpi_running = _agent_is_running()

    # Win rate: last 30 trades
    _kpi_trades_df = query_df(
        conn,
        "SELECT pnl FROM trades ORDER BY closed_at DESC LIMIT 30",
    )
    if not _kpi_trades_df.empty and "pnl" in _kpi_trades_df.columns:
        _kpi_n_trades_30 = len(_kpi_trades_df)
        _kpi_wins_30 = int((_kpi_trades_df["pnl"] > 0).sum())
        _kpi_win_rate = _kpi_wins_30 / _kpi_n_trades_30 * 100 if _kpi_n_trades_30 else 0.0
        # Trend arrow: compare win rate of last 10 vs trades 11-30
        _kpi_win_recent = int((_kpi_trades_df.head(10)["pnl"] > 0).sum())
        _kpi_win_older = int((_kpi_trades_df.tail(20)["pnl"] > 0).sum())
        _kpi_win_trend = "↑" if (_kpi_win_recent / 10) > (_kpi_win_older / max(_kpi_n_trades_30 - 10, 1)) else "↓"
    else:
        _kpi_n_trades_30 = 0
        _kpi_win_rate = 0.0
        _kpi_win_trend = "—"

    # Trades today count
    _kpi_trades_today_df = query_df(
        conn,
        "SELECT id FROM trades WHERE date(closed_at) = date('now')",
    )
    _kpi_trades_today = len(_kpi_trades_today_df)

    # Last signal time
    _kpi_last_sig_df = query_df(conn, "SELECT ts FROM signals ORDER BY id DESC LIMIT 1")
    if not _kpi_last_sig_df.empty and "ts" in _kpi_last_sig_df.columns:
        _kpi_last_sig_str = str(_kpi_last_sig_df["ts"].iloc[0])
        _kpi_last_sig_ago = time_ago(_kpi_last_sig_str)
    else:
        _kpi_last_sig_ago = "No signals"

    # Agent status label + emoji
    if _kpi_halted:
        _kpi_agent_status = "🔴 Halted"
    elif _kpi_running:
        _kpi_agent_status = "🟢 Running"
    else:
        _kpi_agent_status = "⚫ Offline"

    # Regime color badge (markdown)
    _kpi_regime_colors = {
        "TREND": "#2ecc71", "RANGE": "#3498db",
        "VOLATILE": "#e74c3c", "UNKNOWN": "#95a5a6",
    }
    _kpi_regime_bg = _kpi_regime_colors.get(_kpi_regime, "#95a5a6")

    # Row 1: 6-column primary KPI row
    _kr1, _kr2, _kr3, _kr4, _kr5, _kr6 = st.columns(6)

    # col1: Equity with delta vs yesterday start
    _kpi_equity_delta = f"{_kpi_equity - float(capital or _kpi_equity):+,.0f} total" if capital else None
    _kr1.metric(
        "Equity",
        fmt_inr(_kpi_equity),
        delta=_kpi_equity_delta,
        help="Current paper-agent equity from snapshot.json",
    )

    # col2: Today P&L + %
    _kr2.metric(
        "Today P&L",
        fmt_inr(_kpi_day_pnl),
        delta=f"{_kpi_day_pnl_pct:+.2f}%",
        delta_color="normal" if _kpi_day_pnl >= 0 else "inverse",
        help="Equity change since market open today",
    )

    # col3: Win Rate last 30 trades + trend arrow
    _kr3.metric(
        "Win Rate (30T)",
        f"{_kpi_win_rate:.1f}% {_kpi_win_trend}",
        help="Win rate over the last 30 closed trades. Arrow shows improvement/decline vs prior 20.",
    )

    # col4: Open Positions n/max
    _kr4.metric(
        "Open Positions",
        f"{_kpi_n_open} / {_kpi_max_pos}",
        help="Current open positions vs guardrail maximum",
    )

    # col5: Current Regime (color badge)
    _kr5.markdown(
        f"<div style='text-align:center'>"
        f"<small style='color:#888;font-size:0.8em'>Current Regime</small><br>"
        f"<span style='background:{_kpi_regime_bg};color:white;padding:5px 14px;"
        f"border-radius:14px;font-weight:700;font-size:1.1em;display:inline-block;margin-top:4px'>"
        f"{_kpi_regime}</span>"
        f"</div>",
        unsafe_allow_html=True,
    )

    # col6: Agent Status
    _kr6.metric(
        "Agent Status",
        _kpi_agent_status,
        help="🟢 Running = agent process alive, 🔴 Halted = halted by guardrail, ⚫ Offline = not running",
    )

    st.markdown("<hr style='margin:8px 0 4px 0;border-color:#333'>", unsafe_allow_html=True)

    # Row 2: 4-column secondary KPI row
    _ks1, _ks2, _ks3, _ks4 = st.columns(4)

    # col1: Peak Equity
    _ks1.metric(
        "Peak Equity",
        fmt_inr(_kpi_peak),
        help="All-time high equity recorded in snapshot",
    )

    # col2: Max Drawdown % from peak — red if >5%
    _ks2.metric(
        "Drawdown from Peak",
        f"-{_kpi_dd_pct:.2f}%",
        delta=f"peak {fmt_inr(_kpi_peak)}",
        delta_color="off",
        help="Current drawdown from all-time peak equity. Red if >5%.",
    )
    if _kpi_dd_pct > 5.0:
        _ks2.markdown(
            f"<small style='color:#e74c3c;font-weight:600'>Drawdown >5%</small>",
            unsafe_allow_html=True,
        )

    # col3: Total Trades Today
    _ks3.metric(
        "Trades Today",
        f"{_kpi_trades_today}",
        help="Number of trades closed today (UTC date)",
    )

    # col4: Last Signal time ago
    _ks4.metric(
        "Last Signal",
        _kpi_last_sig_ago,
        help="Time since the most recent signal was generated",
    )

    st.markdown("<hr style='margin:4px 0 12px 0;border-color:#333'>", unsafe_allow_html=True)

    # Row 3: Market breadth indicator (single row, shown when breadth data is available)
    _kpi_breadth_raw = snap.get("breadth_pct", None)
    if _kpi_breadth_raw is not None:
        try:
            _kpi_breadth_float = float(_kpi_breadth_raw)
            # breadth_pct is percentage of 50 stocks above 50-DMA → convert to count
            _kpi_breadth_count = int(round(_kpi_breadth_float / 100 * 50))
            _breadth_color = (
                "#2ecc71" if _kpi_breadth_float >= 60
                else ("#f39c12" if _kpi_breadth_float >= 40 else "#e74c3c")
            )
            _breadth_emoji = "🟢" if _kpi_breadth_float >= 60 else ("🟡" if _kpi_breadth_float >= 40 else "🔴")
            st.markdown(
                f"<div style='padding:6px 0 2px 0'>"
                f"<span style='color:#888;font-size:0.85em'>Market Breadth: </span>"
                f"<span style='font-weight:700;font-size:1.05em;color:{_breadth_color}'>"
                f"{_breadth_emoji} {_kpi_breadth_count}/50 above 50-DMA "
                f"({_kpi_breadth_float:.1f}%)</span>"
                f"</div>",
                unsafe_allow_html=True,
            )
        except (TypeError, ValueError):
            pass
