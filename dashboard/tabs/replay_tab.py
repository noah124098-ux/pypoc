"""Trade Replay tab."""
from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from dashboard.utils.charts import safe_html
from dashboard.utils.db import DB_PATH

SQLITE_DB_PATH = str(DB_PATH)


def render() -> None:
    """Render the Trade Replay tab."""
    st.header("Trade Replay")
    st.caption("Replay individual trades with candlestick chart, entry/exit markers, and timeline.")

    # 1. TRADE SELECTOR
    try:
        from core.analytics.trade_replay import list_recent_trades as _list_recent_trades
        _replay_trades_list = _list_recent_trades(SQLITE_DB_PATH)
    except Exception:
        _replay_trades_list = []

    if not _replay_trades_list:
        st.info("No trades found. Run the paper agent to generate trades.")
        st.stop()

    _trade_options = {
        f"#{t['id']} {t['symbol']} {t.get('strategy', '')} | "
        f"{'✅' if float(t['pnl']) > 0 else '❌'} ₹{float(t['pnl']):+,.0f} | "
        f"{str(t.get('opened_at', ''))[:10]}": t['id']
        for t in _replay_trades_list
    }
    _selected_label = st.selectbox("Select trade to replay:", list(_trade_options.keys()), key="replay_trade_select")
    _selected_trade_id = _trade_options[_selected_label]

    # Load full replay object
    try:
        from core.analytics.trade_replay import load_trade_replay as _load_trade_replay
        _replay = _load_trade_replay(_selected_trade_id, SQLITE_DB_PATH)
    except Exception:
        _replay = None

    if _replay is None:
        st.error("Could not load trade details.")
        st.stop()

    # 2. TRADE SUMMARY CARD
    st.divider()

    _rpl_pnl = float(_replay.pnl)
    _rpl_charges = float(_replay.charges)
    _rpl_net = _rpl_pnl - _rpl_charges
    _rpl_entry = float(_replay.entry_price)
    _rpl_exit = float(_replay.exit_price)
    _rpl_qty = int(getattr(_replay, "qty", 1) if hasattr(_replay, "qty") else 1)

    # Fallback: try to read qty from DB if not on replay object
    if _rpl_qty <= 0 or not hasattr(_replay, "qty"):
        try:
            _rpl_conn2 = sqlite3.connect(DB_PATH)
            _rpl_row2 = _rpl_conn2.execute("SELECT qty FROM trades WHERE id=?", (_selected_trade_id,)).fetchone()
            _rpl_conn2.close()
            _rpl_qty = int(_rpl_row2[0]) if _rpl_row2 else 1
        except Exception:
            _rpl_qty = 1

    _rpl_cost_basis = _rpl_entry * _rpl_qty
    _rpl_pnl_pct = (_rpl_pnl / _rpl_cost_basis * 100) if _rpl_cost_basis else 0.0
    try:
        _rpl_opened_dt = datetime.fromisoformat(str(_replay.entry_time).replace(" ", "T")[:19])
        _rpl_closed_dt = datetime.fromisoformat(str(_replay.exit_time).replace(" ", "T")[:19])
        _rpl_dur_total = (_rpl_closed_dt - _rpl_opened_dt).total_seconds()
        if _rpl_dur_total >= 86400:
            _rpl_dur_str = f"{_rpl_dur_total / 86400:.1f}d"
        elif _rpl_dur_total >= 3600:
            _rpl_dur_str = f"{_rpl_dur_total / 3600:.1f}h"
        else:
            _rpl_dur_str = f"{_rpl_dur_total / 60:.0f}m"
    except Exception:
        _rpl_dur_str = "N/A"

    _rpl_exit_reason = str(_replay.exit_reason or "")
    _rpl_exit_color = {
        "TARGET": "#2ecc71", "STOP": "#e74c3c", "STOP_LOSS": "#e74c3c",
        "EOD": "#f39c12", "MANUAL": "#3498db",
    }.get(_rpl_exit_reason.upper().split("_")[0], "#95a5a6")

    _rpl_stop = float(_replay.stop_loss) if _replay.stop_loss else None
    _rpl_target_price = float(_replay.target) if _replay.target else None
    _rpl_r_multiple: float | None = None
    if _rpl_stop and _rpl_stop > 0 and _rpl_entry:
        _risk_per_share = abs(_rpl_entry - _rpl_stop)
        if _risk_per_share > 0:
            _rpl_r_multiple = _rpl_pnl / (_risk_per_share * _rpl_qty)

    _rpl_regime = str(_replay.regime_at_entry or "UNKNOWN")
    _rpl_strategy = str(_replay.strategy or "")
    _rpl_confidence = float(_replay.signal_confidence) if getattr(_replay, "signal_confidence", None) is not None else None

    _sc1, _sc2 = st.columns(2)

    with _sc1:
        st.subheader("Trade Details")
        _m1, _m2 = st.columns(2)
        _m1.metric("Entry Price", f"₹{_rpl_entry:,.2f}")
        _m2.metric("Exit Price", f"₹{_rpl_exit:,.2f}")
        _m3, _m4 = st.columns(2)
        _m3.metric("Gross P&L", f"₹{_rpl_pnl:+,.2f}", delta=f"{_rpl_pnl_pct:+.2f}%",
                   delta_color="normal" if _rpl_pnl >= 0 else "inverse")
        _m4.metric("Charges", f"₹{_rpl_charges:,.2f}")
        _m5, _m6 = st.columns(2)
        _m5.metric("Net P&L", f"₹{_rpl_net:+,.2f}",
                   delta_color="normal" if _rpl_net >= 0 else "inverse")
        if _rpl_r_multiple is not None:
            _m6.metric("R-Multiple", f"{_rpl_r_multiple:+.2f}R")
        else:
            _m6.metric("R-Multiple", "N/A")
        st.markdown(
            f"<div style='margin-top:8px'>"
            f"<span style='background:{_rpl_exit_color};color:white;padding:4px 14px;"
            f"border-radius:12px;font-weight:700;font-size:0.95em'>"
            f"Exit: {safe_html(_rpl_exit_reason or 'UNKNOWN')}</span>"
            f"</div>",
            unsafe_allow_html=True,
        )

    with _sc2:
        st.subheader("Context")
        _c1, _c2 = st.columns(2)
        _c1.metric("Strategy", _rpl_strategy or "N/A")
        _rpl_regime_color = {"TREND": "#2ecc71", "RANGE": "#3498db",
                              "VOLATILE": "#e74c3c", "UNKNOWN": "#95a5a6"}.get(_rpl_regime, "#95a5a6")
        _c2.markdown(
            f"<div style='text-align:center'>"
            f"<small style='color:#888'>Regime at Entry</small><br>"
            f"<span style='background:{_rpl_regime_color};color:white;padding:3px 12px;"
            f"border-radius:10px;font-weight:700'>{safe_html(_rpl_regime)}</span>"
            f"</div>",
            unsafe_allow_html=True,
        )
        _c3, _c4 = st.columns(2)
        if _rpl_confidence is not None:
            _c3.metric("Signal Confidence", f"{_rpl_confidence:.2f}")
        else:
            _c3.metric("Signal Confidence", "N/A")
        _c4.metric("Duration", _rpl_dur_str)
        _c5, _c6 = st.columns(2)
        _c5.metric("Qty", f"{_rpl_qty:,}")
        _c6.metric("Side", str(_replay.strategy or "BUY"))

    # 3. PRICE CHART WITH MARKERS
    st.divider()
    st.subheader("Price Chart")

    _df_chart: pd.DataFrame | None = None
    try:
        from backtest.data_loader import HistoricalLoader as _HistLoader
        _hist_loader = _HistLoader()
        _stock_data = _hist_loader.load_universe([_replay.symbol], days=60)
        _df_chart = _stock_data.get(_replay.symbol)
        if _df_chart is not None and _df_chart.empty:
            _df_chart = None
    except Exception:
        _df_chart = None

    _rpl_pnl_sign = "✅" if _rpl_pnl >= 0 else "❌"
    _rpl_r_str = f"{_rpl_r_multiple:+.1f}R" if _rpl_r_multiple is not None else ""
    _chart_title = (
        f"{_replay.symbol} — {_rpl_strategy} — "
        f"{_rpl_pnl_sign} ₹{_rpl_pnl:+,.0f}"
        + (f" ({_rpl_r_str})" if _rpl_r_str else "")
    )

    if _df_chart is not None:
        try:
            _idx = pd.to_datetime(_df_chart.index)
            _df_chart.index = _idx

            _entry_dt = pd.Timestamp(_replay.entry_time[:10])
            _exit_dt = pd.Timestamp(_replay.exit_time[:10])

            _all_dates = _df_chart.index
            _entry_pos = _all_dates.searchsorted(_entry_dt)
            _exit_pos = _all_dates.searchsorted(_exit_dt)
            _start_pos = max(0, _entry_pos - 5)
            _end_pos = min(len(_all_dates), _exit_pos + 6)
            _df_window = _df_chart.iloc[_start_pos:_end_pos]

            if not _df_window.empty:
                _col_map = {c.lower(): c for c in _df_window.columns}
                _open_col = _col_map.get("open", _col_map.get("o", None))
                _high_col = _col_map.get("high", _col_map.get("h", None))
                _low_col = _col_map.get("low", _col_map.get("l", None))
                _close_col = _col_map.get("close", _col_map.get("c", None))

                _has_ohlc = all(c is not None for c in [_open_col, _high_col, _low_col, _close_col])

                _fig_chart = go.Figure()

                if _has_ohlc:
                    _fig_chart.add_trace(go.Candlestick(
                        x=_df_window.index,
                        open=_df_window[_open_col],
                        high=_df_window[_high_col],
                        low=_df_window[_low_col],
                        close=_df_window[_close_col],
                        name="Price",
                        increasing_line_color="#2ecc71",
                        decreasing_line_color="#e74c3c",
                    ))
                else:
                    _any_col = next(iter(_df_window.columns))
                    _fig_chart.add_trace(go.Scatter(
                        x=_df_window.index,
                        y=_df_window[_any_col],
                        mode="lines",
                        name="Price",
                        line=dict(color="#3498db", width=2),
                    ))

                _entry_bar = _all_dates[_entry_pos] if _entry_pos < len(_all_dates) else _entry_dt
                _exit_bar = _all_dates[min(_exit_pos, len(_all_dates) - 1)]
                _fig_chart.add_vrect(
                    x0=str(_entry_bar), x1=str(_exit_bar),
                    fillcolor="rgba(52,152,219,0.08)",
                    line_width=0,
                )

                _fig_chart.add_trace(go.Scatter(
                    x=[_entry_bar],
                    y=[_rpl_entry],
                    mode="markers",
                    name="Entry",
                    marker=dict(symbol="triangle-up", color="#27ae60", size=14,
                                line=dict(color="white", width=1.5)),
                    hovertemplate=f"<b>ENTRY</b><br>₹{_rpl_entry:,.2f}<extra></extra>",
                ))

                _fig_chart.add_trace(go.Scatter(
                    x=[_exit_bar],
                    y=[_rpl_exit],
                    mode="markers",
                    name="Exit",
                    marker=dict(symbol="triangle-down", color="#e74c3c", size=14,
                                line=dict(color="white", width=1.5)),
                    hovertemplate=f"<b>EXIT</b><br>₹{_rpl_exit:,.2f}<extra></extra>",
                ))

                _fig_chart.add_hline(
                    y=_rpl_entry, line_dash="solid", line_color="#3498db", line_width=1.5,
                    annotation_text=f"Entry ₹{_rpl_entry:,.2f}",
                    annotation_position="top right",
                )
                if _rpl_stop:
                    _fig_chart.add_hline(
                        y=_rpl_stop, line_dash="dash", line_color="#e74c3c", line_width=1.5,
                        annotation_text=f"Stop ₹{_rpl_stop:,.2f}",
                        annotation_position="bottom right",
                    )
                if _rpl_target_price:
                    _fig_chart.add_hline(
                        y=_rpl_target_price, line_dash="dash", line_color="#2ecc71", line_width=1.5,
                        annotation_text=f"Target ₹{_rpl_target_price:,.2f}",
                        annotation_position="top right",
                    )

                _fig_chart.update_layout(
                    title=_chart_title,
                    height=480,
                    margin=dict(l=0, r=0, t=50, b=0),
                    xaxis_rangeslider_visible=False,
                    yaxis_title="Price (₹)",
                    yaxis_tickprefix="₹",
                    yaxis_tickformat=",.2f",
                    legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
                    hovermode="x unified",
                    plot_bgcolor="rgba(0,0,0,0)",
                    paper_bgcolor="rgba(0,0,0,0)",
                )
                st.plotly_chart(_fig_chart, use_container_width=True)
            else:
                _df_chart = None
        except Exception:
            _df_chart = None

    if _df_chart is None:
        st.caption("Historical OHLCV not available — showing simplified entry/exit price line.")
        _simple_fig = go.Figure()
        _simple_fig.add_trace(go.Scatter(
            x=[_replay.entry_time[:10], _replay.exit_time[:10]],
            y=[_rpl_entry, _rpl_exit],
            mode="lines+markers",
            name="Price",
            line=dict(color="#3498db", width=2.5),
            marker=dict(size=10, color=["#27ae60", "#e74c3c"],
                        symbol=["triangle-up", "triangle-down"]),
        ))
        if _rpl_stop:
            _simple_fig.add_hline(y=_rpl_stop, line_dash="dash", line_color="#e74c3c",
                                   annotation_text=f"Stop ₹{_rpl_stop:,.2f}")
        if _rpl_target_price:
            _simple_fig.add_hline(y=_rpl_target_price, line_dash="dash", line_color="#2ecc71",
                                   annotation_text=f"Target ₹{_rpl_target_price:,.2f}")
        _simple_fig.update_layout(
            title=_chart_title,
            height=300,
            margin=dict(l=0, r=0, t=50, b=0),
            yaxis_title="Price (₹)",
            yaxis_tickprefix="₹",
            plot_bgcolor="rgba(0,0,0,0)",
            paper_bgcolor="rgba(0,0,0,0)",
        )
        st.plotly_chart(_simple_fig, use_container_width=True)

    # 4. TRADE TIMELINE
    st.divider()
    st.markdown("**Trade Timeline**")

    _tl_entry_time = str(_replay.entry_time or "")
    _tl_exit_time = str(_replay.exit_time or "")
    _tl_entry_display = _tl_entry_time[:16].replace("T", " ")
    _tl_exit_display = _tl_exit_time[:16].replace("T", " ")
    _tl_side = "BUY" if "BUY" in str(getattr(_replay, "side", "BUY")).upper() else "SELL"

    _tl_mid_price = (_rpl_entry + _rpl_exit) / 2
    _tl_mid_unr = (_tl_mid_price - _rpl_entry) * _rpl_qty
    _tl_mid_time = ""
    try:
        _open2 = datetime.fromisoformat(_tl_entry_time[:19].replace(" ", "T"))
        _close2 = datetime.fromisoformat(_tl_exit_time[:19].replace(" ", "T"))
        _mid2 = _open2 + (_close2 - _open2) / 2
        _tl_mid_time = _mid2.strftime("%Y-%m-%d %H:%M")
    except Exception:
        _tl_mid_time = "—"

    _exit_emoji = "🎯" if "TARGET" in _rpl_exit_reason.upper() else (
        "🛑" if "STOP" in _rpl_exit_reason.upper() else "🔚"
    )

    st.markdown(
        f"- 📅 **{_tl_entry_display}** — ENTRY {_tl_side} @ ₹{_rpl_entry:,.2f} "
        f"({_rpl_strategy}, {_rpl_regime} regime)"
    )
    if _tl_mid_time and _tl_mid_time != "—":
        st.markdown(
            f"- ⏱️ **{_tl_mid_time}** — Position value: ₹{_tl_mid_price * _rpl_qty:,.0f} "
            f"(unrealized {'+' if _tl_mid_unr >= 0 else ''}₹{_tl_mid_unr:,.0f})"
        )
    _rpl_r_exit = f", {_rpl_r_str}" if _rpl_r_str else ""
    st.markdown(
        f"- {_exit_emoji} **{_tl_exit_display}** — EXIT {_rpl_exit_reason or 'CLOSED'} "
        f"@ ₹{_rpl_exit:,.2f} "
        f"(net ₹{_rpl_net:+,.0f}{_rpl_r_exit})"
    )

    # 5. SIGNAL DETAILS (expander)
    with st.expander("Signal Details", expanded=False):
        _sig_rat = str(_replay.signal_rationale or "")
        if _sig_rat:
            st.markdown(f"**Rationale:** {_sig_rat}")
        else:
            st.caption("No rationale recorded for this signal.")

        st.markdown("**Strategy context at entry:**")
        _sd1, _sd2, _sd3 = st.columns(3)
        _sd1.metric("Strategy", _rpl_strategy or "N/A")
        _sd2.metric("Regime", _rpl_regime)
        if _rpl_confidence is not None:
            _sd3.metric("Signal Confidence", f"{_rpl_confidence:.2f}")
        else:
            _sd3.metric("Signal Confidence", "N/A")

        if _rpl_stop or _rpl_target_price:
            _sd4, _sd5 = st.columns(2)
            if _rpl_stop:
                _sd4.metric("Stop Loss", f"₹{_rpl_stop:,.2f}")
            if _rpl_target_price:
                _sd5.metric("Target", f"₹{_rpl_target_price:,.2f}")

    # 6. DECISION TRACE
    st.divider()
    st.markdown("**Decision Trace** — signal → guardrail pipeline")

    _dtrace_sig_id: int | None = None
    try:
        _dtrace_conn = sqlite3.connect(DB_PATH)
        _dtrace_conn.row_factory = sqlite3.Row
        _dtrace_sig_row = _dtrace_conn.execute(
            "SELECT id FROM signals WHERE symbol=? AND ts <= ? AND accepted=1 "
            "ORDER BY ts DESC LIMIT 1",
            (_replay.symbol, _replay.entry_time),
        ).fetchone()
        _dtrace_conn.close()
        if _dtrace_sig_row:
            _dtrace_sig_id = int(_dtrace_sig_row["id"])
    except Exception:
        pass

    _dtrace: dict = {}
    if _dtrace_sig_id is not None:
        try:
            from core.analytics.decision_trace import trace_signal_decision as _trace_signal_decision
            _dtrace = _trace_signal_decision(_dtrace_sig_id, SQLITE_DB_PATH)
        except Exception:
            _dtrace = {}

    if _dtrace:
        _dtsz = _dtrace.get("sizing", {})
        _dtrace_cols = st.columns(4)
        _dtrace_cols[0].metric(
            "Equity at Signal",
            f"₹{_dtsz['equity_at_time']:,.0f}" if _dtsz.get("equity_at_time") else "N/A",
        )
        _dtrace_cols[1].metric("Risk per Share", f"₹{_dtsz.get('risk_per_share', 0):.2f}")
        _dtrace_cols[2].metric("Qty (raw)", str(_dtsz.get("raw_qty", "N/A")))
        _dtrace_cols[3].metric("Qty (final)", str(_dtsz.get("final_qty", "N/A")))

        st.markdown("")

        _dt_checks = _dtrace.get("guardrail_checks", [])
        _RULE_LABELS = {
            "global_halt":          "Global halt",
            "qty_positive":         "Qty positive",
            "stop_loss_required":   "Stop loss required",
            "market_window":        "Market window",
            "stale_data":           "Stale data",
            "black_swan_nifty":     "Black swan (Nifty)",
            "black_swan_vix":       "Black swan (VIX)",
            "max_open_positions":   "Max open positions",
            "per_trade_risk":       "Per-trade risk",
            "max_position_size":    "Max position size",
            "liquidity":            "Liquidity check",
            "spread":               "Spread check",
            "daily_loss_circuit":   "Daily loss circuit",
            "drawdown_circuit":     "Drawdown circuit",
            "signal_cooldown":      "Signal cooldown",
            "duplicate_position":   "Duplicate position",
        }
        for _dtc in _dt_checks:
            _dtc_rule   = _dtc.get("rule", "")
            _dtc_passed = _dtc.get("passed")
            _dtc_detail = _dtc.get("detail", "")
            _dtc_label  = _RULE_LABELS.get(_dtc_rule, _dtc_rule.replace("_", " ").title())
            if _dtc_passed is True:
                _dtc_icon = "✅"
            elif _dtc_passed is False:
                _dtc_icon = "❌"
            else:
                _dtc_icon = "⬜"
            st.markdown(
                f"{_dtc_icon} **{_dtc_label}:** {_dtc_detail}",
                unsafe_allow_html=False,
            )

        st.markdown("")
        _dt_decision = _dtrace.get("final_decision", "UNKNOWN")
        _dt_rej      = _dtrace.get("rejection_reason")
        if _dt_decision == "ACCEPTED":
            st.success(
                f"→ ACCEPTED — {_dtsz.get('final_qty', '?')} shares @ market",
                icon="✅",
            )
        else:
            _dt_rej_text = f" — {_dt_rej}" if _dt_rej else ""
            st.error(f"→ REJECTED{_dt_rej_text}", icon="❌")
    else:
        st.caption("Decision trace not available — signal record not found in DB.")

    # 7. SIMILAR TRADES
    _similar = []
    try:
        _sim_conn = sqlite3.connect(DB_PATH)
        _sim_conn.row_factory = sqlite3.Row
        _sim_rows = _sim_conn.execute(
            """
            SELECT t.id, t.symbol, t.pnl, COALESCE(t.charges,0) as charges,
                   t.opened_at, t.exit_reason, COALESCE(t.strategy,'') as strategy,
                   s.accepted as sig_accepted, s.rejection_reason as sig_rejection_reason
            FROM trades t
            LEFT JOIN signals s ON (s.symbol = t.symbol AND s.accepted = 1
                                    AND s.ts <= t.opened_at
                                    AND s.ts >= datetime(t.opened_at, '-5 minutes'))
            WHERE t.strategy = ? AND t.id != ?
            ORDER BY t.closed_at DESC LIMIT 5
            """,
            (_rpl_strategy, _selected_trade_id),
        ).fetchall()
        _sim_conn.close()
        _similar = [dict(r) for r in _sim_rows]
    except Exception:
        _similar = []

    if _similar:
        st.divider()
        st.markdown(f"**Similar Trades — {_rpl_strategy}**")
        for _sim in _similar:
            _sim_pnl = float(_sim.get("pnl", 0))
            _sim_charges = float(_sim.get("charges", 0))
            _sim_net = _sim_pnl - _sim_charges
            _sim_badge = "✅" if _sim_pnl > 0 else "❌"
            _sim_rej = _sim.get("sig_rejection_reason")
            _sim_col1, _sim_col2, _sim_col3, _sim_col4 = st.columns([1, 2, 2, 2])
            _sim_col1.markdown(f"**{_sim_badge}**")
            _sim_col2.markdown(f"**#{_sim['id']}** {_sim.get('symbol', '')}")
            _sim_col3.metric(
                "Net P&L",
                f"₹{_sim_net:+,.0f}",
                delta_color="normal" if _sim_net >= 0 else "inverse",
                label_visibility="collapsed",
            )
            _sim_col4.caption(str(_sim.get("opened_at", ""))[:10])
            if _sim_rej:
                st.caption(f"  ⚠ Rejection: {_sim_rej}")
