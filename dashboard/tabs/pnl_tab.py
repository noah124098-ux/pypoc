"""P&L & Equity tab."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from plotly.subplots import make_subplots

from dashboard.design import COLORS, _metric_card, _section
from dashboard.utils.charts import color_pnl, fmt_inr, pnl_by_period
from dashboard.utils.db import DB_PATH, get_equity_snapshots, get_trades, query_df


def render(snap: dict, config: dict, conn) -> None:
    """Render the P&L & Equity tab."""
    st.header("P&L & Equity Curve")

    capital = config.get("capital", {}).get("initial_inr", 0)
    equity = float(snap.get("equity", capital or 0))
    peak = float(snap.get("peak_equity", equity) or equity)
    start_today = float(snap.get("starting_equity_today", equity) or equity)
    realized_pnl = float(snap.get("realized_pnl", 0.0))
    positions_list = snap.get("open_positions", [])
    unrealized = sum(float(p.get("unrealized_pnl", 0.0)) for p in positions_list)
    day_pnl_pct = (equity - start_today) / start_today * 100 if start_today else 0.0
    dd_pct = (peak - equity) / peak * 100 if peak else 0.0

    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Equity", f"₹{equity:,.0f}", delta=f"{equity - capital:+,.0f} total" if capital else None)
    c2.metric("Realized P&L", f"₹{realized_pnl:,.0f}")
    c3.metric("Unrealized P&L", f"₹{unrealized:,.0f}")
    c4.metric("Day P&L", f"{day_pnl_pct:+.2f}%", delta_color="normal" if day_pnl_pct >= 0 else "inverse")
    c5.metric("Drawdown", f"-{dd_pct:.2f}%", delta_color="inverse")

    st.divider()

    # Equity curve
    eq_df = get_equity_snapshots(str(DB_PATH))

    # Query trades for entry/exit markers
    _trades_for_markers = query_df(
        conn,
        "SELECT opened_at, closed_at, entry_price, exit_price, pnl, symbol FROM trades ORDER BY opened_at",
    )

    if not eq_df.empty:
        eq_df["ts"] = pd.to_datetime(eq_df["ts"])
        eq_df = eq_df.sort_values("ts").reset_index(drop=True)
        eq_df["peak"] = eq_df["equity"].cummax()
        eq_df["dd_pct"] = (eq_df["peak"] - eq_df["equity"]) / eq_df["peak"] * 100

        _start_equity = float(capital or eq_df["equity"].iloc[0])
        _end_equity = float(eq_df["equity"].iloc[-1])
        _total_return_pct = (_end_equity - _start_equity) / _start_equity * 100 if _start_equity else 0.0

        # CAGR
        _ts_start = eq_df["ts"].iloc[0]
        _ts_end = eq_df["ts"].iloc[-1]
        _years_span = max((_ts_end - _ts_start).total_seconds() / (365.25 * 86400), 1 / 365.25)
        _cagr_pct = ((_end_equity / _start_equity) ** (1.0 / _years_span) - 1) * 100 if _start_equity > 0 else 0.0

        # Sharpe from daily equity returns
        _eq_daily = eq_df.set_index("ts")["equity"].resample("D").last().dropna()
        _daily_returns = _eq_daily.pct_change().dropna()
        _sharpe = (
            (_daily_returns.mean() / _daily_returns.std() * (252 ** 0.5))
            if len(_daily_returns) >= 2 and _daily_returns.std() > 0
            else 0.0
        )

        # Best / worst day in absolute INR
        _eq_shifted = _eq_daily.shift(1).dropna()
        _eq_aligned = _eq_daily.iloc[1:]
        _daily_pnl_arr = (_eq_aligned.values - _eq_shifted.values)
        _best_day_inr = float(_daily_pnl_arr.max()) if len(_daily_pnl_arr) else 0.0
        _worst_day_inr = float(_daily_pnl_arr.min()) if len(_daily_pnl_arr) else 0.0

        # Avg trade duration
        _avg_duration_str = "N/A"
        if (
            not _trades_for_markers.empty
            and "opened_at" in _trades_for_markers.columns
            and "closed_at" in _trades_for_markers.columns
        ):
            try:
                _tdf = _trades_for_markers.copy()
                _tdf["opened_at"] = pd.to_datetime(_tdf["opened_at"], errors="coerce")
                _tdf["closed_at"] = pd.to_datetime(_tdf["closed_at"], errors="coerce")
                _tdf = _tdf.dropna(subset=["opened_at", "closed_at"])
                if not _tdf.empty:
                    _durations = (_tdf["closed_at"] - _tdf["opened_at"]).dt.total_seconds() / 3600
                    _avg_hours = _durations.mean()
                    if _avg_hours >= 24:
                        _avg_duration_str = f"{_avg_hours / 24:.1f} days"
                    else:
                        _avg_duration_str = f"{_avg_hours:.1f} h"
            except Exception:
                pass

        # Summary metrics above chart
        st.subheader("Equity Curve — Paper Agent")
        _ret_color = COLORS["profit"] if _total_return_pct >= 0 else COLORS["loss"]
        _ret_sign = "+" if _total_return_pct >= 0 else ""
        st.markdown(
            f"<span style='font-size:1.1em;color:{_ret_color};font-weight:bold'>"
            f"Total Return: {_ret_sign}{_total_return_pct:.2f}%</span>"
            f"&nbsp;&nbsp;<span style='color:#888;font-size:0.9em'>"
            f"(starting ₹{_start_equity:,.0f} → current ₹{_end_equity:,.0f})</span>",
            unsafe_allow_html=True,
        )

        _sm1, _sm2, _sm3, _sm4, _sm5 = st.columns(5)
        _sm1.metric(
            "Total Return",
            f"{_ret_sign}{_total_return_pct:.2f}%",
            delta_color="normal" if _total_return_pct >= 0 else "inverse",
        )
        _cagr_sign = "+" if _cagr_pct >= 0 else ""
        _sm2.metric("Ann. Return (CAGR)", f"{_cagr_sign}{_cagr_pct:.2f}%")
        _sm3.metric("Sharpe Ratio", f"{_sharpe:.2f}")
        _sm4.metric("Best Day", f"₹{_best_day_inr:+,.0f}")
        _sm5.metric("Worst Day", f"₹{_worst_day_inr:+,.0f}")
        _dur_col1, _dur_col2 = st.columns([1, 4])
        _dur_col1.metric("Avg Trade Duration", _avg_duration_str)

        st.divider()

        # Build figure: equity + drawdown panel
        _fig_eq = make_subplots(
            rows=2,
            cols=1,
            shared_xaxes=True,
            row_heights=[0.72, 0.28],
            vertical_spacing=0.06,
            specs=[[{"secondary_y": True}], [{"secondary_y": False}]],
        )

        # Equity curve (profit-green line + light fill)
        _fig_eq.add_trace(
            go.Scatter(
                x=eq_df["ts"],
                y=eq_df["equity"],
                mode="lines",
                name="Equity",
                line=dict(color=COLORS["profit"], width=2.5),
                fill="tozeroy",
                fillcolor="rgba(0,200,83,0.08)",
                hovertemplate="<b>%{x|%Y-%m-%d %H:%M}</b><br>Equity: ₹%{y:,.0f}<extra></extra>",
            ),
            row=1,
            col=1,
            secondary_y=False,
        )

        # Reference line at starting equity
        _fig_eq.add_hline(
            y=_start_equity,
            line_dash="dot",
            line_color="rgba(150,150,150,0.6)",
            line_width=1.5,
            annotation_text=f"Start ₹{_start_equity:,.0f}",
            annotation_position="bottom right",
            annotation_font_size=11,
            row=1,
            col=1,
        )

        # Nifty benchmark reference line
        _nifty_ltp_bench = snap.get("nifty_ltp", None)
        if _nifty_ltp_bench:
            try:
                _nifty_now = float(_nifty_ltp_bench)
                if _nifty_now > 0:
                    _fig_eq.add_trace(
                        go.Scatter(
                            x=[eq_df["ts"].iloc[0], eq_df["ts"].iloc[-1]],
                            y=[_start_equity, _start_equity],
                            mode="lines",
                            name="Nifty B&H (ref)",
                            line=dict(color="#3498db", width=1.5, dash="dash"),
                            hovertemplate="Nifty B&H ref: ₹%{y:,.0f}<extra></extra>",
                        ),
                        row=1,
                        col=1,
                        secondary_y=False,
                    )
            except Exception:
                pass

        # Trade entry / exit markers on equity curve
        if not _trades_for_markers.empty:
            try:
                _tdf2 = _trades_for_markers.copy()
                _tdf2["opened_at"] = pd.to_datetime(_tdf2["opened_at"], errors="coerce")
                _tdf2["closed_at"] = pd.to_datetime(_tdf2["closed_at"], errors="coerce")
                _tdf2 = _tdf2.dropna(subset=["opened_at", "closed_at"])

                if not _tdf2.empty:
                    _eq_idx = eq_df.set_index("ts")["equity"]

                    def _nearest_equity(ts_val):
                        try:
                            i = _eq_idx.index.searchsorted(ts_val)
                            i = min(i, len(_eq_idx) - 1)
                            return float(_eq_idx.iloc[i])
                        except Exception:
                            return None

                    _tdf2["entry_equity"] = _tdf2["opened_at"].apply(_nearest_equity)
                    _tdf2["exit_equity"] = _tdf2["closed_at"].apply(_nearest_equity)

                    _entries = _tdf2.dropna(subset=["entry_equity"])
                    if not _entries.empty:
                        _fig_eq.add_trace(
                            go.Scatter(
                                x=_entries["opened_at"],
                                y=_entries["entry_equity"],
                                mode="markers",
                                name="Entry",
                                marker=dict(
                                    symbol="triangle-up",
                                    color=COLORS["profit"],
                                    size=9,
                                    line=dict(color="white", width=1),
                                ),
                                hovertemplate=(
                                    "<b>Entry</b><br>%{x|%Y-%m-%d %H:%M}<br>"
                                    "Equity: ₹%{y:,.0f}<extra></extra>"
                                ),
                            ),
                            row=1,
                            col=1,
                            secondary_y=False,
                        )

                    _exits = _tdf2.dropna(subset=["exit_equity"])
                    if not _exits.empty:
                        _fig_eq.add_trace(
                            go.Scatter(
                                x=_exits["closed_at"],
                                y=_exits["exit_equity"],
                                mode="markers",
                                name="Exit",
                                marker=dict(
                                    symbol="triangle-down",
                                    color=COLORS["loss"],
                                    size=9,
                                    line=dict(color="white", width=1),
                                ),
                                hovertemplate=(
                                    "<b>Exit</b><br>%{x|%Y-%m-%d %H:%M}<br>"
                                    "Equity: ₹%{y:,.0f}<extra></extra>"
                                ),
                            ),
                            row=1,
                            col=1,
                            secondary_y=False,
                        )
            except Exception:
                pass

        # Drawdown panel (loss-red fill, negative direction)
        _fig_eq.add_trace(
            go.Scatter(
                x=eq_df["ts"],
                y=-eq_df["dd_pct"],
                mode="lines",
                name="Drawdown %",
                line=dict(color=COLORS["loss"], width=1.5),
                fill="tozeroy",
                fillcolor="rgba(255,23,68,0.18)",
                hovertemplate="<b>%{x|%Y-%m-%d %H:%M}</b><br>Drawdown: %{y:.2f}%<extra></extra>",
            ),
            row=2,
            col=1,
        )

        _fig_eq.update_yaxes(
            title_text="Equity (INR)", row=1, col=1, secondary_y=False,
            tickprefix="₹", tickformat=",.0f",
        )
        _fig_eq.update_yaxes(title_text="Drawdown (%)", row=2, col=1,
                              tickformat=".1f", ticksuffix="%")
        _fig_eq.update_xaxes(title_text="Date", row=2, col=1)
        _fig_eq.update_layout(
            height=640,
            margin=dict(l=0, r=0, t=10, b=0),
            legend=dict(orientation="h", yanchor="bottom", y=1.01, xanchor="right", x=1),
            hovermode="x unified",
            plot_bgcolor="rgba(0,0,0,0)",
            paper_bgcolor="rgba(0,0,0,0)",
        )

        st.plotly_chart(_fig_eq, use_container_width=True)
    else:
        st.info("No equity snapshots yet. Run the agent to see the equity curve.")

    st.divider()

    # P&L breakdown by period
    st.subheader("P&L Breakdown")
    trades_df = get_trades(str(DB_PATH))
    pnl_periods = pnl_by_period(trades_df)

    if pnl_periods:
        period_tab_labels = ["Daily", "Weekly", "Monthly", "Last 3 Months"]
        period_keys = ["daily", "weekly", "monthly", "3month"]
        ptabs = st.tabs(period_tab_labels)

        for ptab, key in zip(ptabs, period_keys):
            with ptab:
                pdata = pnl_periods.get(key, pd.DataFrame())
                if pdata.empty:
                    st.info(f"No trades for {key} view.")
                    continue
                pdata = pdata.sort_values("period")
                pdata["color"] = pdata["pnl_net"].apply(
                    lambda x: COLORS["profit"] if x >= 0 else COLORS["loss"]
                )
                total = pdata["pnl_net"].sum()
                wins = (pdata["pnl_net"] > 0).sum()
                losses = (pdata["pnl_net"] < 0).sum()

                m1, m2, m3 = st.columns(3)
                m1.metric("Total P&L", f"₹{total:,.0f}", delta_color="normal" if total >= 0 else "inverse")
                m2.metric("Profitable periods", f"{wins}")
                m3.metric("Loss periods", f"{losses}")

                fig_bar = go.Figure(go.Bar(
                    x=pdata["period"].astype(str), y=pdata["pnl_net"],
                    marker_color=pdata["color"],
                    text=pdata["pnl_net"].apply(lambda x: f"₹{x:,.0f}"),
                    textposition="outside",
                ))
                fig_bar.update_layout(height=350, margin=dict(l=0, r=0, t=20, b=0),
                                       yaxis_title="P&L (INR)", xaxis_title="Period")
                st.plotly_chart(fig_bar, use_container_width=True)
    else:
        st.info("No closed trades yet.")

    st.divider()

    # Recent trades table
    st.subheader("Recent Closed Trades")
    if not trades_df.empty:
        trades_df["pnl_net"] = trades_df["pnl"] - trades_df["charges"]

        # Build W/L streak column
        _sorted_trades = trades_df.sort_values("closed_at").reset_index(drop=True)
        _streak_labels: list[str] = []
        _run = 0
        _run_type = ""
        for _, _tr in _sorted_trades.iterrows():
            _tr_pnl = float(_tr.get("pnl", 0) or 0)
            _this_type = "W" if _tr_pnl > 0 else ("L" if _tr_pnl < 0 else "=")
            if _this_type == _run_type:
                _run += 1
            else:
                _run = 1
                _run_type = _this_type
            _streak_labels.append(f"{_this_type}{_run}")
        _sorted_trades["streak"] = _streak_labels
        _sorted_trades = _sorted_trades.sort_values("closed_at", ascending=False).reset_index(drop=True)

        display_cols = ["closed_at", "symbol", "side", "qty", "entry_price", "exit_price",
                        "pnl", "charges", "pnl_net", "strategy", "exit_reason", "streak"]
        display_cols = [c for c in display_cols if c in _sorted_trades.columns]
        pnl_cols = [c for c in ["pnl", "pnl_net"] if c in display_cols]

        def _row_bg(row):
            """Color entire row green (profitable) or red (loss)."""
            try:
                val = float(row.get("pnl", 0) or 0)
            except (TypeError, ValueError):
                val = 0.0
            if val > 0:
                return ["background-color: rgba(0,200,83,0.15)"] * len(row)
            if val < 0:
                return ["background-color: rgba(255,23,68,0.15)"] * len(row)
            return [""] * len(row)

        _display_slice = _sorted_trades[display_cols].head(100)
        styled = _display_slice.style.apply(_row_bg, axis=1)
        styled = styled.applymap(color_pnl, subset=[c for c in pnl_cols if c in _display_slice.columns])
        styled = styled.format({c: "₹{:.2f}" for c in ["entry_price", "exit_price"] + pnl_cols
                                 if c in display_cols})
        st.dataframe(styled, use_container_width=True, height=350)

        total_trades = len(trades_df)
        wins = (trades_df["pnl"] > 0).sum()
        win_rate = wins / total_trades * 100 if total_trades else 0
        avg_win = trades_df.loc[trades_df["pnl"] > 0, "pnl"].mean() if wins else 0
        losses_n = total_trades - wins
        avg_loss = trades_df.loc[trades_df["pnl"] < 0, "pnl"].mean() if losses_n else 0
        pf = abs(avg_win * wins) / abs(avg_loss * losses_n) if avg_loss and losses_n else float("inf")

        s1, s2, s3, s4 = st.columns(4)
        s1.metric("Total Trades", total_trades)
        s2.metric("Win Rate", f"{win_rate:.1f}%")
        s3.metric("Avg Win / Loss", f"₹{avg_win:.0f} / ₹{avg_loss:.0f}")
        s4.metric("Profit Factor", f"{pf:.2f}" if pf != float("inf") else "inf")

        csv_data = trades_df.to_csv(index=False).encode("utf-8")
        st.download_button(
            label="📥 Export trades to CSV",
            data=csv_data,
            file_name=f"pypoc_trades_{datetime.now().strftime('%Y%m%d')}.csv",
            mime="text/csv",
            key="export_trades_csv",
        )
    else:
        st.info("No closed trades yet.")
