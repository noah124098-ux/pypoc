"""Backtest Results tab."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from dashboard.design import COLORS, _metric_card, _section, regime_hex
from dashboard.utils.charts import color_pnl
from dashboard.utils.db import DB_PATH, get_equity_snapshots, query_df


def render(snap: dict, config: dict, gate: dict, conn) -> None:
    """Render the Backtest & Walk-Forward Results tab."""
    st.header("Backtest & Walk-Forward Results")

    capital = config.get("capital", {}).get("initial_inr", 0)

    # Backtest vs Live Equity Comparison
    st.subheader("Backtest vs Live (Paper) Equity Comparison")

    _bt_live_eq_df = get_equity_snapshots(str(DB_PATH))

    # Build backtest trend line from gate JSON
    _bt_gate_start_eq: float = 0.0
    _bt_gate_end_eq: float = 0.0
    _bt_gate_start_dt = None
    _bt_gate_end_dt = None
    _bt_cagr: float = 0.0
    _live_cagr: float = 0.0

    if gate:
        try:
            _bt_metrics = gate.get("metrics", {})
            _bt_gate_start_eq = float(_bt_metrics.get("starting_equity", 0) or 0)
            _bt_gate_end_eq = float(_bt_metrics.get("ending_equity", 0) or 0)
            _bt_gate_start_dt = datetime.fromisoformat(
                str(gate.get("period_start", ""))[:19]
            ) if gate.get("period_start") else None
            _bt_gate_end_dt = datetime.fromisoformat(
                str(gate.get("period_end", ""))[:19]
            ) if gate.get("period_end") else None
            if (
                _bt_gate_start_dt and _bt_gate_end_dt
                and _bt_gate_start_eq and _bt_gate_end_eq
                and _bt_gate_start_eq > 0
            ):
                _bt_years = max(
                    (_bt_gate_end_dt - _bt_gate_start_dt).total_seconds() / (365.25 * 86400),
                    1 / 365.25,
                )
                _bt_cagr = (
                    (_bt_gate_end_eq / _bt_gate_start_eq) ** (1.0 / _bt_years) - 1
                ) * 100
        except Exception:
            pass

    # Compute live CAGR from equity snapshots
    _live_start_eq: float = float(capital or 0)
    _live_end_eq: float = 0.0
    _live_max_dd: float = 0.0
    _bt_live_eq_df_sorted = pd.DataFrame()

    if not _bt_live_eq_df.empty:
        try:
            _bt_live_eq_df_sorted = _bt_live_eq_df.copy()
            _bt_live_eq_df_sorted["ts"] = pd.to_datetime(_bt_live_eq_df_sorted["ts"])
            _bt_live_eq_df_sorted = _bt_live_eq_df_sorted.sort_values("ts").reset_index(drop=True)
            _live_ts_start = _bt_live_eq_df_sorted["ts"].iloc[0]
            _live_ts_end = _bt_live_eq_df_sorted["ts"].iloc[-1]
            _live_start_eq_snapshot = float(_bt_live_eq_df_sorted["equity"].iloc[0])
            _live_end_eq = float(_bt_live_eq_df_sorted["equity"].iloc[-1])
            _live_years = max(
                (_live_ts_end - _live_ts_start).total_seconds() / (365.25 * 86400),
                1 / 365.25,
            )
            _live_start_base = _live_start_eq if _live_start_eq > 0 else _live_start_eq_snapshot
            if _live_start_base > 0 and _live_end_eq > 0:
                _live_cagr = ((_live_end_eq / _live_start_base) ** (1.0 / _live_years) - 1) * 100
            _peak_arr = _bt_live_eq_df_sorted["equity"].cummax()
            _dd_arr = (_peak_arr - _bt_live_eq_df_sorted["equity"]) / _peak_arr * 100
            _live_max_dd = float(_dd_arr.max()) if not _dd_arr.empty else 0.0
        except Exception:
            pass

    # Compute live Sharpe from equity snapshots
    _live_sharpe: float = 0.0
    if not _bt_live_eq_df_sorted.empty:
        try:
            _eq_for_sharpe = _bt_live_eq_df_sorted.set_index("ts")["equity"].resample("D").last().dropna()
            _daily_ret = _eq_for_sharpe.pct_change().dropna()
            if len(_daily_ret) >= 2 and _daily_ret.std() > 0:
                _live_sharpe = float(_daily_ret.mean() / _daily_ret.std() * (252 ** 0.5))
        except Exception:
            pass

    # Compute live win rate and profit factor from trades table
    _live_win_rate: float = 0.0
    _live_pf: float = 0.0
    _all_trades_for_cmp = query_df(conn, "SELECT pnl, charges FROM trades")
    if not _all_trades_for_cmp.empty and "pnl" in _all_trades_for_cmp.columns:
        try:
            _n_all = len(_all_trades_for_cmp)
            _wins_all = int((_all_trades_for_cmp["pnl"] > 0).sum())
            _live_win_rate = (_wins_all / _n_all * 100) if _n_all > 0 else 0.0
            _gross_p = float(_all_trades_for_cmp.loc[_all_trades_for_cmp["pnl"] > 0, "pnl"].sum())
            _gross_l = abs(float(_all_trades_for_cmp.loc[_all_trades_for_cmp["pnl"] < 0, "pnl"].sum()))
            _live_pf = (_gross_p / _gross_l) if _gross_l > 0 else 0.0
        except Exception:
            pass

    # Tracking annotation
    _tracking_label = "N/A"
    if _bt_cagr != 0.0 and _live_cagr != 0.0:
        _cagr_diff_pct = abs(_live_cagr - _bt_cagr) / abs(_bt_cagr) * 100
        _tracking_label = "YES" if _cagr_diff_pct <= 20.0 else "NO"

    # Plot: both equity curves on same chart
    _fig_cmp = go.Figure()

    _ref_eq = _bt_gate_start_eq or _live_start_eq or float(capital or 500000)
    _ref_x_start = _bt_gate_start_dt
    _ref_x_end = _bt_gate_end_dt
    if not _bt_live_eq_df_sorted.empty:
        if _ref_x_start is None:
            _ref_x_start = _bt_live_eq_df_sorted["ts"].iloc[0]
        if _ref_x_end is None:
            _ref_x_end = _bt_live_eq_df_sorted["ts"].iloc[-1]

    if _ref_x_start and _ref_x_end and _ref_eq:
        _fig_cmp.add_trace(go.Scatter(
            x=[_ref_x_start, _ref_x_end],
            y=[_ref_eq, _ref_eq],
            mode="lines",
            name=f"Starting Equity (₹{_ref_eq:,.0f})",
            line=dict(color="rgba(150,150,150,0.7)", width=1.5, dash="dot"),
            hovertemplate="Starting equity: ₹%{y:,.0f}<extra></extra>",
        ))

    if _bt_gate_start_dt and _bt_gate_end_dt and _bt_gate_start_eq and _bt_gate_end_eq:
        _fig_cmp.add_trace(go.Scatter(
            x=[_bt_gate_start_dt, _bt_gate_end_dt],
            y=[_bt_gate_start_eq, _bt_gate_end_eq],
            mode="lines",
            name=f"Backtest trend (CAGR {_bt_cagr:+.1f}%)",
            line=dict(color=COLORS["profit"], width=2.5, dash="dash"),
            hovertemplate="Backtest trend: ₹%{y:,.0f}<extra></extra>",
        ))

    if not _bt_live_eq_df_sorted.empty:
        _fig_cmp.add_trace(go.Scatter(
            x=_bt_live_eq_df_sorted["ts"],
            y=_bt_live_eq_df_sorted["equity"],
            mode="lines",
            name=f"Live paper equity (CAGR {_live_cagr:+.1f}%)",
            line=dict(color=COLORS["accent"], width=2.5),
            fill="tozeroy",
            fillcolor="rgba(2,136,209,0.07)",
            hovertemplate="<b>%{x|%Y-%m-%d %H:%M}</b><br>Live equity: ₹%{y:,.0f}<extra></extra>",
        ))

    _tracking_color = COLORS["profit"] if _tracking_label == "YES" else (
        COLORS["loss"] if _tracking_label == "NO" else COLORS["neutral"]
    )
    _fig_cmp.add_annotation(
        xref="paper", yref="paper",
        x=0.01, y=0.97,
        text=f"Paper agent tracks backtest: <b>{_tracking_label}</b>",
        showarrow=False,
        font=dict(size=13, color=_tracking_color),
        bgcolor="rgba(0,0,0,0.4)" if st.session_state.get("dark_mode") else "rgba(255,255,255,0.8)",
        bordercolor=_tracking_color,
        borderwidth=1,
        borderpad=6,
        align="left",
    )

    _fig_cmp.update_layout(
        height=420,
        margin=dict(l=0, r=0, t=30, b=0),
        xaxis_title="Date",
        yaxis_title="Equity (₹)",
        yaxis_tickprefix="₹",
        yaxis_tickformat=",.0f",
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0),
        plot_bgcolor="rgba(0,0,0,0)",
        paper_bgcolor="rgba(0,0,0,0)",
        hovermode="x unified",
    )

    if _bt_gate_start_dt or not _bt_live_eq_df_sorted.empty:
        st.plotly_chart(_fig_cmp, use_container_width=True)
    else:
        st.info(
            "No equity data available yet. Run the paper agent to populate equity snapshots "
            "and run `python cli.py walk-forward --years 3` to populate the backtest gate."
        )

    # Stats Comparison Table
    st.subheader("Backtest vs Live — Stats Comparison")

    _bt_metrics_cmp = gate.get("metrics", {}) if gate else {}
    _bt_sharpe_cmp = float(_bt_metrics_cmp.get("sharpe", 0) or 0)
    _bt_win_rate_cmp = float(_bt_metrics_cmp.get("win_rate_pct", 0) or 0)
    _bt_max_dd_cmp = float(_bt_metrics_cmp.get("max_drawdown_pct", 0) or 0)
    _bt_pf_cmp = float(_bt_metrics_cmp.get("profit_factor", 0) or 0)

    def _cmp_delta(live_val: float, bt_val: float) -> str:
        if bt_val == 0:
            return "N/A"
        diff_pct = (live_val - bt_val) / abs(bt_val) * 100
        sign = "+" if diff_pct >= 0 else ""
        return f"{sign}{diff_pct:.1f}% vs backtest"

    _has_live_eq = not _bt_live_eq_df_sorted.empty
    _has_live_trades = not _all_trades_for_cmp.empty

    _cmp_rows = [
        {
            "Metric": "Sharpe Ratio",
            "Backtest": f"{_bt_sharpe_cmp:.2f}",
            "Live (paper)": f"{_live_sharpe:.2f}" if _has_live_eq else "N/A",
            "Delta": _cmp_delta(_live_sharpe, _bt_sharpe_cmp) if _has_live_eq else "N/A",
        },
        {
            "Metric": "Win Rate",
            "Backtest": f"{_bt_win_rate_cmp:.1f}%",
            "Live (paper)": f"{_live_win_rate:.1f}%" if _has_live_trades else "N/A",
            "Delta": _cmp_delta(_live_win_rate, _bt_win_rate_cmp) if _has_live_trades else "N/A",
        },
        {
            "Metric": "Max Drawdown",
            "Backtest": f"{_bt_max_dd_cmp:.2f}%",
            "Live (paper)": f"{_live_max_dd:.2f}%" if _has_live_eq else "N/A",
            "Delta": _cmp_delta(-_live_max_dd, -_bt_max_dd_cmp) if _has_live_eq else "N/A",
        },
        {
            "Metric": "Profit Factor",
            "Backtest": f"{_bt_pf_cmp:.2f}",
            "Live (paper)": f"{_live_pf:.2f}" if _has_live_trades else "N/A",
            "Delta": _cmp_delta(_live_pf, _bt_pf_cmp) if _has_live_trades else "N/A",
        },
    ]

    _cmp_df = pd.DataFrame(_cmp_rows)

    def _delta_cell_style(val: str) -> str:
        try:
            numeric_part = str(val).split("%")[0].replace("+", "").strip()
            v = float(numeric_part)
            if v > 0:
                return f"color: {COLORS['profit']}; font-weight: bold"
            if v < 0:
                return f"color: {COLORS['loss']}; font-weight: bold"
        except Exception:
            pass
        return f"color: {COLORS['neutral']}"

    st.dataframe(
        _cmp_df.style.applymap(_delta_cell_style, subset=["Delta"]),
        use_container_width=True,
        hide_index=True,
    )
    st.caption(
        "Delta = (Live − Backtest) / |Backtest| as %. "
        "Max Drawdown delta: positive = live drawdown is smaller (better). "
        "Tracking badge uses CAGR within 20% tolerance (N/A if live has no equity history)."
    )

    st.divider()

    # Gate file expiry indicator
    if gate and gate.get("timestamp"):
        try:
            _gate_ts_str = gate["timestamp"][:19].replace("Z", "")
            _gate_dt = datetime.fromisoformat(_gate_ts_str)
            _gate_age_days = (datetime.now() - _gate_dt).days
            _days_remaining = 30 - _gate_age_days
            if _days_remaining > 7:
                st.success(f"Gate file valid — {_days_remaining} days until expiry")
            elif _days_remaining > 0:
                st.warning(f"Gate file expiring in {_days_remaining} days — re-run walk-forward soon")
            else:
                st.error("Gate file EXPIRED — re-run walk-forward before going live")
        except Exception:
            pass

    if gate:
        gate_passed = gate.get("passed", False)
        gate_ts = gate.get("timestamp", "unknown")[:19].replace("T", " ")
        period_start = gate.get("period_start", "?")[:10] if gate.get("period_start") else "?"
        period_end = gate.get("period_end", "?")[:10] if gate.get("period_end") else "?"

        banner_color = COLORS["profit"] if gate_passed else COLORS["loss"]
        banner_label = "GATE PASSED" if gate_passed else "GATE FAILED"
        st.markdown(
            f"<div style='background:{banner_color};padding:16px;border-radius:8px;margin-bottom:16px'>"
            f"<h2 style='color:white;margin:0'>{banner_label}</h2>"
            f"<p style='color:#ddd;margin:4px 0'>Period: {period_start} to {period_end} | Run at: {gate_ts}</p>"
            f"</div>",
            unsafe_allow_html=True,
        )

        if gate.get("failures"):
            st.error(f"Failures: {', '.join(gate['failures'])}")

        metrics = gate.get("metrics", {})
        checks = gate.get("checks", [])
        m1, m2, m3, m4, m5 = st.columns(5)

        def _metric_card(col, label, actual, threshold, direction):
            passed = actual >= threshold if direction == "ge" else actual <= threshold
            delta_label = f"{'>=  ' if direction == 'ge' else '<= '}{threshold}"
            col.metric(label, f"{actual:.2f}", delta=delta_label,
                       delta_color="normal" if passed else "inverse")

        check_map = {c["name"]: c for c in checks} if checks else {}
        for col, name in zip([m1, m2, m3, m4, m5],
                              ["sharpe", "max_drawdown_pct", "win_rate_pct", "profit_factor", "n_trades"]):
            if name in check_map:
                c = check_map[name]
                _metric_card(col, name.replace("_", " ").title(), c["actual"], c["threshold"], c["direction"])

        st.divider()
        st.subheader("Gate Check Details")
        if checks:
            checks_df = pd.DataFrame(checks)
            checks_df["status"] = checks_df["pass_"].apply(lambda x: "PASS" if x else "FAIL")
            checks_df["operator"] = checks_df["direction"].map({"ge": ">=", "le": "<="})
            display_checks = checks_df[["name", "actual", "operator", "threshold", "status"]].copy()
            display_checks.columns = ["Metric", "Actual", "Op", "Threshold", "Status"]

            def _status_style(val):
                return (
                    f"color: {COLORS['profit']}; font-weight: bold"
                    if "PASS" in str(val)
                    else f"color: {COLORS['loss']}; font-weight: bold"
                )

            st.dataframe(
                display_checks.style.applymap(_status_style, subset=["Status"])
                    .format({"Actual": "{:.3f}", "Threshold": "{:.3f}"}),
                use_container_width=True, hide_index=True,
            )

        gate_config = gate.get("config", {})
        if gate_config:
            with st.expander("Gate configuration"):
                st.json(gate_config)
    else:
        st.warning(
            "No backtest gate result found. Run:\n\n"
            "```\npython cli.py walk-forward --years 3\n```"
        )

    st.divider()
    st.subheader("Run Backtest or Walk-Forward")
    st.info(
        "Generate a new backtest result from the terminal:\n\n"
        "```\n"
        "# Quick single-window backtest:\n"
        "python cli.py backtest --days 365\n\n"
        "# Full 3-year walk-forward (updates gate file):\n"
        "python cli.py walk-forward --years 3\n\n"
        "# Diagnose rejection reasons:\n"
        "python cli.py debug-rejections --days 365\n"
        "```\n\n"
        "Then click **Refresh now** in the sidebar."
    )

    st.divider()

    # Strategy Performance Attribution
    st.header("Strategy Performance Attribution")

    _bt_trades = query_df(
        conn,
        "SELECT strategy, pnl, charges, closed_at, opened_at, regime FROM trades ORDER BY closed_at",
    )

    if _bt_trades.empty:
        st.info("No closed trades in the database yet. Run the agent or a backtest first.")
        return

    _bt_trades["pnl_net"] = _bt_trades["pnl"] - _bt_trades["charges"]
    _bt_trades["closed_at"] = pd.to_datetime(_bt_trades["closed_at"], errors="coerce")
    _bt_trades["opened_at"] = pd.to_datetime(_bt_trades["opened_at"], errors="coerce")

    # 1. Strategy Performance Table
    st.subheader("Strategy Performance Table")

    def _pf_color(val: float) -> str:
        try:
            v = float(val)
        except (TypeError, ValueError):
            return ""
        if v >= 1.5:
            return "background-color: rgba(46,204,113,0.25); color: #2ecc71; font-weight: bold"
        if v >= 1.0:
            return "background-color: rgba(243,156,18,0.25); color: #f39c12; font-weight: bold"
        return "background-color: rgba(231,76,60,0.25); color: #e74c3c; font-weight: bold"

    _strat_rows = []
    for _strat_name, _sg in _bt_trades.groupby("strategy"):
        _n = len(_sg)
        _wins = (_sg["pnl"] > 0).sum()
        _losses = (_sg["pnl"] < 0).sum()
        _win_pct = _wins / _n * 100 if _n > 0 else 0.0
        _avg_win = float(_sg.loc[_sg["pnl"] > 0, "pnl"].mean()) if _wins > 0 else 0.0
        _avg_loss = float(_sg.loc[_sg["pnl"] < 0, "pnl"].mean()) if _losses > 0 else 0.0
        _gross_profit = float(_sg.loc[_sg["pnl"] > 0, "pnl"].sum()) if _wins > 0 else 0.0
        _gross_loss = abs(float(_sg.loc[_sg["pnl"] < 0, "pnl"].sum())) if _losses > 0 else 0.0
        _pf_val = (_gross_profit / _gross_loss) if _gross_loss > 0 else float("inf")
        _net_pnl = float(_sg["pnl_net"].sum())
        _strat_rows.append({
            "Strategy": str(_strat_name),
            "Trades": _n,
            "Win %": round(_win_pct, 1),
            "Avg Win ₹": round(_avg_win, 0),
            "Avg Loss ₹": round(_avg_loss, 0),
            "Profit Factor": round(_pf_val, 2) if _pf_val != float("inf") else 999.0,
            "Net P&L ₹": round(_net_pnl, 0),
        })

    _strat_df = pd.DataFrame(_strat_rows).sort_values("Net P&L ₹", ascending=False)

    def _strat_table_style(row):
        styles = [""] * len(row)
        if "Profit Factor" in row.index:
            idx = list(row.index).index("Profit Factor")
            styles[idx] = _pf_color(row["Profit Factor"])
        if "Net P&L ₹" in row.index:
            idx_pnl = list(row.index).index("Net P&L ₹")
            try:
                v = float(row["Net P&L ₹"])
                if v > 0:
                    styles[idx_pnl] = "color: #2ecc71; font-weight: bold"
                elif v < 0:
                    styles[idx_pnl] = "color: #e74c3c; font-weight: bold"
            except (TypeError, ValueError):
                pass
        return styles

    _strat_styled = (
        _strat_df.style
        .apply(_strat_table_style, axis=1)
        .format({
            "Win %": "{:.1f}%",
            "Avg Win ₹": "₹{:,.0f}",
            "Avg Loss ₹": "₹{:,.0f}",
            "Profit Factor": "{:.2f}",
            "Net P&L ₹": "₹{:,.0f}",
        })
    )
    st.dataframe(_strat_styled, use_container_width=True, hide_index=True)

    _pnl_bar_colors = [
        COLORS["profit"] if v >= 0 else COLORS["loss"]
        for v in _strat_df["Net P&L ₹"]
    ]
    _fig_strat_bar = go.Figure(go.Bar(
        x=_strat_df["Net P&L ₹"],
        y=_strat_df["Strategy"],
        orientation="h",
        marker_color=_pnl_bar_colors,
        text=_strat_df["Net P&L ₹"].apply(lambda v: f"₹{v:,.0f}"),
        textposition="outside",
        hovertemplate="<b>%{y}</b><br>Net P&L: ₹%{x:,.0f}<extra></extra>",
    ))
    _fig_strat_bar.update_layout(
        height=max(200, len(_strat_rows) * 50 + 60),
        margin=dict(l=0, r=80, t=20, b=0),
        xaxis_title="Net P&L (₹)",
        xaxis_tickprefix="₹",
        xaxis_tickformat=",.0f",
        plot_bgcolor="rgba(0,0,0,0)",
        paper_bgcolor="rgba(0,0,0,0)",
    )
    st.plotly_chart(_fig_strat_bar, use_container_width=True)

    st.divider()

    # 2. Monthly P&L Heatmap
    st.subheader("Monthly P&L Heatmap")

    _analytics_trades: list = []
    try:
        from core.analytics.metrics import (
            compute_monthly_pnl as _compute_monthly_pnl,
            compute_strategy_attribution as _compute_strategy_attribution,
            compute_regime_attribution as _compute_regime_attribution,
            compute_extended_metrics as _compute_extended_metrics,
            load_trades_from_db as _load_trades_from_db,
        )
        if DB_PATH.exists():
            _analytics_trades = _load_trades_from_db(DB_PATH)
    except Exception:
        _analytics_trades = []

    _hm_df = _bt_trades.dropna(subset=["closed_at"]).copy()
    _hm_df["year"] = _hm_df["closed_at"].dt.year
    _hm_df["month"] = _hm_df["closed_at"].dt.month
    _monthly_pnl_raw = (
        _hm_df.groupby(["year", "month"])["pnl_net"]
        .sum()
        .reset_index()
    )
    _monthly_count = (
        _hm_df.groupby(["year", "month"])["pnl_net"]
        .count()
        .reset_index()
        .rename(columns={"pnl_net": "n_trades"})
    )

    _use_rich_hm = bool(_analytics_trades)
    if _use_rich_hm:
        try:
            _rich_monthly = _compute_monthly_pnl(_analytics_trades)
        except Exception:
            _use_rich_hm = False

    if not _monthly_pnl_raw.empty:
        _years_sorted = sorted(_monthly_pnl_raw["year"].unique())
        _month_labels = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
                         "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
        _hm_matrix: list = []
        _hm_text: list = []
        _hm_hover: list = []
        for _yr in _years_sorted:
            _row_vals: list = []
            _row_text: list = []
            _row_hover: list = []
            for _mo in range(1, 13):
                _match = _monthly_pnl_raw[
                    (_monthly_pnl_raw["year"] == _yr) & (_monthly_pnl_raw["month"] == _mo)
                ]
                _cnt_match = _monthly_count[
                    (_monthly_count["year"] == _yr) & (_monthly_count["month"] == _mo)
                ]
                if not _match.empty:
                    _v = float(_match["pnl_net"].iloc[0])
                    _cnt = int(_cnt_match["n_trades"].iloc[0]) if not _cnt_match.empty else 0
                    _row_vals.append(_v)
                    _sign = "+" if _v >= 0 else ""
                    _pnl_str = f"₹{_sign}{_v:,.0f}"
                    _ym_key = f"{_yr}-{_mo:02d}"
                    if _use_rich_hm and _ym_key in _rich_monthly.index:
                        _wr = float(_rich_monthly.loc[_ym_key, "win_rate"])
                        _cnt_r = int(_rich_monthly.loc[_ym_key, "n_trades"])
                        _cell_label = f"{_pnl_str}\n{_cnt_r}T"
                        _hover_str = (
                            f"{_yr} {_month_labels[_mo-1]}<br>"
                            f"P&L: {_pnl_str}<br>"
                            f"Trades: {_cnt_r}<br>"
                            f"Win rate: {_wr:.0f}%"
                        )
                    else:
                        _cell_label = f"{_pnl_str}\n{_cnt}T"
                        _hover_str = (
                            f"{_yr} {_month_labels[_mo-1]}<br>"
                            f"P&L: {_pnl_str}<br>"
                            f"Trades: {_cnt}"
                        )
                    _row_text.append(_cell_label)
                    _row_hover.append(_hover_str)
                else:
                    _row_vals.append(None)
                    _row_text.append("")
                    _row_hover.append(f"{_yr} {_month_labels[_mo-1]}<br>No trades")
            _hm_matrix.append(_row_vals)
            _hm_text.append(_row_text)
            _hm_hover.append(_row_hover)

        _fig_hm = go.Figure(go.Heatmap(
            z=_hm_matrix,
            x=_month_labels,
            y=[str(y) for y in _years_sorted],
            text=_hm_text,
            texttemplate="%{text}",
            customdata=_hm_hover,
            colorscale=[
                [0.0, "#c0392b"],
                [0.45, "#e8a09a"],
                [0.5, "#f5f5f5"],
                [0.55, "#8fd4a7"],
                [1.0, "#1a7a4a"],
            ],
            zmid=0,
            colorbar=dict(title="P&L (₹)", tickprefix="₹", len=0.8),
            hovertemplate="%{customdata}<extra></extra>",
        ))
        _fig_hm.update_layout(
            height=max(220, len(_years_sorted) * 60 + 120),
            margin=dict(l=60, r=80, t=20, b=40),
            xaxis_title="Month",
            yaxis_title="Year",
            font=dict(size=11),
            plot_bgcolor="rgba(0,0,0,0)",
            paper_bgcolor="rgba(0,0,0,0)",
        )
        st.plotly_chart(_fig_hm, use_container_width=True)
        st.caption(
            "Green = profit, red = loss, grey = no trades. "
            "Cell text shows P&L and trade count. Hover for win rate."
        )
    else:
        st.info("Not enough trade data to build the monthly heatmap.")

    st.divider()

    # 2b. Strategy Attribution Chart
    st.subheader("Strategy Attribution")

    if _analytics_trades:
        try:
            _strat_attr = _compute_strategy_attribution(_analytics_trades)
            if _strat_attr:
                _sa_rows = []
                for _sname, _sm in _strat_attr.items():
                    _sa_rows.append({
                        "Strategy": _sname,
                        "Net P&L (₹)": round(_sm.total_pnl, 0),
                        "Win %": round(_sm.win_rate_pct, 1),
                        "Profit Factor": round(min(_sm.profit_factor, 99.0), 2),
                        "Trades": _sm.n_trades,
                    })
                _sa_df = pd.DataFrame(_sa_rows).sort_values("Net P&L (₹)", ascending=True)

                _sa_colors = [
                    COLORS["profit"] if v >= 0 else COLORS["loss"]
                    for v in _sa_df["Net P&L (₹)"]
                ]
                _fig_sa = go.Figure(go.Bar(
                    x=_sa_df["Net P&L (₹)"],
                    y=_sa_df["Strategy"],
                    orientation="h",
                    marker_color=_sa_colors,
                    text=_sa_df.apply(
                        lambda r: (
                            f"₹{r['Net P&L (₹)']:+,.0f} | "
                            f"Win {r['Win %']:.0f}% | "
                            f"PF {r['Profit Factor']:.2f} | "
                            f"{r['Trades']}T"
                        ),
                        axis=1,
                    ),
                    textposition="outside",
                    hovertemplate=(
                        "<b>%{y}</b><br>"
                        "Net P&L: ₹%{x:,.0f}<extra></extra>"
                    ),
                ))
                _fig_sa.update_layout(
                    height=max(200, len(_sa_rows) * 55 + 80),
                    margin=dict(l=0, r=160, t=20, b=0),
                    xaxis_title="Net P&L (₹)",
                    xaxis_tickprefix="₹",
                    xaxis_tickformat=",.0f",
                    plot_bgcolor="rgba(0,0,0,0)",
                    paper_bgcolor="rgba(0,0,0,0)",
                )
                st.plotly_chart(_fig_sa, use_container_width=True)
                st.caption(
                    "Horizontal bar length = net P&L. "
                    "Labels show win rate, profit factor, and trade count."
                )
            else:
                st.info("No strategy attribution data available.")
        except Exception as _sa_exc:
            st.caption(f"Strategy attribution unavailable: {_sa_exc}")
    else:
        st.caption("Load trade records from the database to enable strategy attribution.")

    st.divider()

    # 2c. Regime Performance Radar + Trade Count Bar
    st.subheader("Regime Performance")

    if _analytics_trades:
        try:
            _reg_attr = _compute_regime_attribution(_analytics_trades)
            if _reg_attr:
                _radar_col, _bar_col = st.columns(2)

                with _radar_col:
                    st.markdown("**Win Rate by Regime (Radar)**")
                    _regime_order = ["TREND", "RANGE", "VOLATILE", "UNKNOWN"]
                    _radar_labels = [r for r in _regime_order if r in _reg_attr]
                    _radar_values = [
                        round(_reg_attr[r].win_rate_pct, 1) for r in _radar_labels
                    ]
                    if len(_radar_labels) >= 2:
                        _radar_labels_closed = _radar_labels + [_radar_labels[0]]
                        _radar_values_closed = _radar_values + [_radar_values[0]]
                        _fig_radar = go.Figure(go.Scatterpolar(
                            r=_radar_values_closed,
                            theta=_radar_labels_closed,
                            fill="toself",
                            fillcolor="rgba(52,152,219,0.25)",
                            line=dict(color="#3498db", width=2),
                            hovertemplate="<b>%{theta}</b><br>Win rate: %{r:.1f}%<extra></extra>",
                        ))
                        _fig_radar.update_layout(
                            polar=dict(
                                radialaxis=dict(
                                    range=[0, 100],
                                    ticksuffix="%",
                                    gridcolor="rgba(200,200,200,0.3)",
                                    linecolor="rgba(200,200,200,0.3)",
                                ),
                                angularaxis=dict(
                                    gridcolor="rgba(200,200,200,0.3)",
                                    linecolor="rgba(200,200,200,0.3)",
                                ),
                                bgcolor="rgba(0,0,0,0)",
                            ),
                            height=300,
                            margin=dict(l=30, r=30, t=30, b=30),
                            paper_bgcolor="rgba(0,0,0,0)",
                        )
                        st.plotly_chart(_fig_radar, use_container_width=True)
                    else:
                        st.info("Need trades in at least 2 regimes for the radar chart.")

                with _bar_col:
                    st.markdown("**Trade Count per Regime**")
                    _reg_bar_labels = [r for r in _regime_order if r in _reg_attr]
                    _reg_bar_counts = [_reg_attr[r].n_trades for r in _reg_bar_labels]
                    _reg_bar_colors_map = {
                        "TREND": COLORS["trend"],
                        "RANGE": COLORS["range"],
                        "VOLATILE": COLORS["volatile"],
                        "UNKNOWN": COLORS["unknown"],
                    }
                    _reg_bar_colors = [_reg_bar_colors_map.get(r, "#95a5a6") for r in _reg_bar_labels]
                    _fig_reg_bar = go.Figure(go.Bar(
                        x=_reg_bar_labels,
                        y=_reg_bar_counts,
                        marker_color=_reg_bar_colors,
                        text=_reg_bar_counts,
                        textposition="outside",
                        hovertemplate=(
                            "<b>%{x}</b><br>Trades: %{y}<extra></extra>"
                        ),
                    ))
                    _fig_reg_bar.update_layout(
                        height=300,
                        margin=dict(l=0, r=0, t=20, b=0),
                        yaxis_title="Trade Count",
                        xaxis_title="Regime",
                        plot_bgcolor="rgba(0,0,0,0)",
                        paper_bgcolor="rgba(0,0,0,0)",
                    )
                    st.plotly_chart(_fig_reg_bar, use_container_width=True)

                _reg_metric_cols = st.columns(len(_radar_labels))
                for _rmc, _rn in zip(_reg_metric_cols, _radar_labels):
                    _rm = _reg_attr[_rn]
                    _rmc.metric(
                        _rn,
                        f"Win {_rm.win_rate_pct:.0f}%",
                        delta=f"₹{_rm.total_pnl:+,.0f} | PF {min(_rm.profit_factor, 99.0):.2f}",
                        delta_color="normal" if _rm.total_pnl >= 0 else "inverse",
                    )
            else:
                st.info("No regime attribution data available.")
        except Exception as _reg_exc:
            st.caption(f"Regime radar unavailable: {_reg_exc}")
    else:
        st.caption("Load trade records from the database to enable regime performance.")

    st.divider()

    # 2d. Extended Metrics Table
    st.subheader("Extended Performance Metrics")

    if _analytics_trades:
        try:
            _ext = _compute_extended_metrics(_analytics_trades)

            def _fmt_ratio(v: float, precision: int = 2) -> str:
                if v == float("inf"):
                    return "∞"
                if v != v:  # NaN
                    return "N/A"
                return f"{v:.{precision}f}"

            _em_left = {
                "Sharpe Ratio": _fmt_ratio(_ext.sharpe),
                "Sortino Ratio": _fmt_ratio(_ext.sortino),
                "Calmar Ratio": _fmt_ratio(_ext.calmar_ratio),
                "Omega Ratio": _fmt_ratio(_ext.omega_ratio),
                "Recovery Factor": _fmt_ratio(_ext.recovery_factor),
                "Payoff Ratio": _fmt_ratio(_ext.payoff_ratio),
            }
            _em_right = {
                "Max Consecutive Wins": str(_ext.consecutive_wins_max),
                "Max Consecutive Losses": str(_ext.consecutive_losses_max),
                "Avg Win R": _fmt_ratio(_ext.avg_win_r),
                "Avg Loss R": _fmt_ratio(_ext.avg_loss_r),
                "Trade Frequency / Day": _fmt_ratio(_ext.trade_frequency_per_day, 3),
                "Expectancy (₹/trade)": f"₹{_ext.expectancy:+,.0f}",
            }

            _em_col1, _em_col2 = st.columns(2)
            with _em_col1:
                st.markdown("**Risk-Adjusted Returns**")
                for _k, _val in _em_left.items():
                    _is_good = None
                    if _k == "Sharpe Ratio":
                        try:
                            _is_good = float(_val) >= 1.2
                        except Exception:
                            pass
                    elif _k == "Sortino Ratio":
                        try:
                            _is_good = float(_val) >= 1.5
                        except Exception:
                            pass
                    _color = (
                        COLORS["profit"] if _is_good is True
                        else (COLORS["loss"] if _is_good is False else COLORS["neutral"])
                    )
                    st.markdown(
                        f"<div style='display:flex;justify-content:space-between;"
                        f"padding:4px 0;border-bottom:1px solid #2a2a2a'>"
                        f"<span style='color:#aaa'>{_k}</span>"
                        f"<span style='font-weight:bold;color:{_color}'>{_val}</span>"
                        f"</div>",
                        unsafe_allow_html=True,
                    )
            with _em_col2:
                st.markdown("**Trade Statistics**")
                for _k, _val in _em_right.items():
                    st.markdown(
                        f"<div style='display:flex;justify-content:space-between;"
                        f"padding:4px 0;border-bottom:1px solid #2a2a2a'>"
                        f"<span style='color:#aaa'>{_k}</span>"
                        f"<span style='font-weight:bold;color:#f0f0f0'>{_val}</span>"
                        f"</div>",
                        unsafe_allow_html=True,
                    )
            st.caption(
                f"Based on {_ext.n_trades} closed trades. "
                f"Sharpe gate threshold: 1.2 | Sortino benchmark: 1.5"
            )
        except Exception as _em_exc:
            st.caption(f"Extended metrics unavailable: {_em_exc}")
    else:
        st.caption("No trade records available for extended metrics.")

    st.divider()

    # 3. Trade Distribution Charts
    st.subheader("Trade Distribution")
    _dist_col1, _dist_col2 = st.columns(2)

    with _dist_col1:
        st.markdown("**P&L Distribution (Histogram)**")
        _pnl_series = _bt_trades["pnl_net"].dropna()
        if not _pnl_series.empty:
            _fig_hist = go.Figure()
            _fig_hist.add_trace(go.Histogram(
                x=_pnl_series,
                nbinsx=40,
                marker_color=[
                    COLORS["profit"] if v >= 0 else COLORS["loss"]
                    for v in _pnl_series
                ],
                hovertemplate="P&L bin: ₹%{x:,.0f}<br>Count: %{y}<extra></extra>",
                name="Trades",
            ))
            _fig_hist.add_vline(
                x=0, line_dash="dash", line_color="rgba(200,200,200,0.7)", line_width=1.5
            )
            _fig_hist.add_vline(
                x=float(_pnl_series.mean()),
                line_dash="dot",
                line_color="#f39c12",
                line_width=1.5,
                annotation_text=f"Mean ₹{_pnl_series.mean():,.0f}",
                annotation_font_color="#f39c12",
            )
            _fig_hist.update_layout(
                height=350,
                margin=dict(l=0, r=0, t=10, b=0),
                xaxis_title="Net P&L (₹)",
                yaxis_title="Number of Trades",
                plot_bgcolor="rgba(0,0,0,0)",
                paper_bgcolor="rgba(0,0,0,0)",
                showlegend=False,
            )
            st.plotly_chart(_fig_hist, use_container_width=True)
        else:
            st.info("No P&L data available.")

    with _dist_col2:
        st.markdown("**Entry Time of Day vs P&L**")
        _tod_df = _bt_trades.dropna(subset=["opened_at", "pnl_net"]).copy()
        if not _tod_df.empty:
            _tod_df["hour_frac"] = (
                _tod_df["opened_at"].dt.hour
                + _tod_df["opened_at"].dt.minute / 60.0
            )
            _tod_df["color_flag"] = _tod_df["pnl_net"].apply(
                lambda v: "Win" if v >= 0 else "Loss"
            )
            _fig_tod = px.scatter(
                _tod_df,
                x="hour_frac",
                y="pnl_net",
                color="color_flag",
                color_discrete_map={"Win": COLORS["profit"], "Loss": COLORS["loss"]},
                labels={"hour_frac": "Entry Hour (IST)", "pnl_net": "Net P&L (₹)"},
                opacity=0.65,
            )
            _fig_tod.add_hline(
                y=0,
                line_dash="dash",
                line_color="rgba(200,200,200,0.5)",
                line_width=1,
            )
            _fig_tod.update_layout(
                height=350,
                margin=dict(l=0, r=0, t=10, b=0),
                xaxis=dict(
                    tickvals=[9.25, 10, 11, 12, 13, 14, 15, 15.5],
                    ticktext=["9:15", "10:00", "11:00", "12:00",
                              "13:00", "14:00", "15:00", "15:30"],
                ),
                yaxis_tickprefix="₹",
                yaxis_tickformat=",.0f",
                legend_title_text="",
                plot_bgcolor="rgba(0,0,0,0)",
                paper_bgcolor="rgba(0,0,0,0)",
            )
            st.plotly_chart(_fig_tod, use_container_width=True)
            st.caption("Shows whether certain entry times produce better or worse outcomes.")
        else:
            st.info("No entry timestamp data available.")

    st.divider()

    # 4. Regime Performance Table
    st.subheader("Performance by Regime")

    _reg_col = "regime"
    if _reg_col in _bt_trades.columns:
        _regime_rows = []
        _regime_groups = _bt_trades.dropna(subset=[_reg_col]).groupby(_reg_col)
        for _reg_name, _rg in _regime_groups:
            _rn = len(_rg)
            _rw = (_rg["pnl"] > 0).sum()
            _rw_pct = _rw / _rn * 100 if _rn > 0 else 0.0
            _avg_pnl = float(_rg["pnl_net"].mean())
            _best = float(_rg["pnl_net"].max())
            _worst = float(_rg["pnl_net"].min())
            _regime_rows.append({
                "Regime": str(_reg_name),
                "Trades": _rn,
                "Win %": round(_rw_pct, 1),
                "Avg P&L ₹": round(_avg_pnl, 0),
                "Best Trade ₹": round(_best, 0),
                "Worst Trade ₹": round(_worst, 0),
            })

        if _regime_rows:
            _regime_perf_df = pd.DataFrame(_regime_rows).sort_values("Trades", ascending=False)

            def _regime_row_style(row):
                _bg = ""
                regime_val = str(row.get("Regime", ""))
                bg_map = {
                    "TREND":    "rgba(21,101,192,0.12)",   # COLORS['trend'] at ~12% opacity
                    "RANGE":    "rgba(106,27,154,0.12)",   # COLORS['range'] at ~12% opacity
                    "VOLATILE": "rgba(230,81,0,0.12)",     # COLORS['volatile'] at ~12% opacity
                }
                _bg = bg_map.get(regime_val, "")
                return [f"background-color:{_bg}" if _bg else ""] * len(row)

            def _avg_pnl_color(val):
                try:
                    v = float(val)
                    if v > 0:
                        return "color: #2ecc71; font-weight: bold"
                    if v < 0:
                        return "color: #e74c3c; font-weight: bold"
                except (TypeError, ValueError):
                    pass
                return ""

            _regime_styled = (
                _regime_perf_df.style
                .apply(_regime_row_style, axis=1)
                .applymap(_avg_pnl_color, subset=["Avg P&L ₹", "Best Trade ₹", "Worst Trade ₹"])
                .format({
                    "Win %": "{:.1f}%",
                    "Avg P&L ₹": "₹{:,.0f}",
                    "Best Trade ₹": "₹{:,.0f}",
                    "Worst Trade ₹": "₹{:,.0f}",
                })
            )
            st.dataframe(_regime_styled, use_container_width=True, hide_index=True)
        else:
            st.info("No regime data found in trades.")
    else:
        st.info(
            "Regime column not present in the trades table. "
            "Trades logged after the regime column was added will show here."
        )
