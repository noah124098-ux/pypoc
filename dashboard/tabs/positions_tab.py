"""Positions & Signals tab."""
from __future__ import annotations

import html as _html

import pandas as pd
import plotly.express as px
import streamlit as st

from dashboard.utils.charts import safe_html, color_pnl, time_ago
from dashboard.utils.db import DB_PATH, get_signals, query_df


def render(snap: dict, config: dict, conn) -> None:
    """Render the Open Positions & Recent Signals tab."""
    st.header("Open Positions & Recent Signals")

    # Halt banner
    _pos_halted = snap.get("halted", False)
    _pos_halt_reason = snap.get("halt_reason", "")
    if _pos_halted:
        _pos_halt_reason_safe = _html.escape(str(_pos_halt_reason or "No reason recorded"))
        st.markdown(
            f"<div style='background:#c0392b;color:white;padding:12px 16px;border-radius:8px;"
            f"font-weight:bold;font-size:1.05em;margin-bottom:12px'>"
            f"AGENT HALTED: {_pos_halt_reason_safe}</div>",
            unsafe_allow_html=True,
        )

    # Summary metrics row
    positions_list = snap.get("open_positions", [])
    _max_pos = int(config.get("risk", {}).get("max_open_positions", 5))
    _initial_capital = float(config.get("capital", {}).get("initial_inr", 0) or 0)
    _current_equity = float(snap.get("equity", _initial_capital) or _initial_capital)
    _n_open = len(positions_list)
    _total_exposure = sum(
        float(p.get("qty", 0)) * float(p.get("last_price") or p.get("avg_price", 0))
        for p in positions_list
    )
    _total_unrealized = sum(float(p.get("unrealized_pnl", 0)) for p in positions_list)
    _exposure_pct = (_total_exposure / _current_equity * 100) if _current_equity else 0.0

    sm1, sm2, sm3, sm4 = st.columns(4)
    sm1.metric(
        "Open Positions",
        f"{_n_open} / {_max_pos}",
        help="Current open positions vs maximum allowed",
    )
    sm2.metric(
        "Total Exposure",
        f"₹{_total_exposure:,.0f}",
        delta=f"{_exposure_pct:.1f}% of equity",
        delta_color="off",
    )
    _unr_delta_color = "normal" if _total_unrealized >= 0 else "inverse"
    sm3.metric(
        "Total Unrealized P&L",
        f"₹{_total_unrealized:,.0f}",
        delta_color=_unr_delta_color,
    )
    sm4.metric(
        "Equity",
        f"₹{_current_equity:,.0f}",
    )

    st.divider()

    st.subheader("Open Positions")
    if positions_list:
        _STRATEGY_COLORS = {
            "trend_breakout": "#1a6ba0",
            "rsi_momentum": "#7b2fa0",
            "mean_reversion": "#a06b1a",
            "bb_squeeze": "#1a7a4a",
            "supertrend": "#7a1a1a",
            "supertrend_short": "#c0392b",
        }

        for _pos in positions_list:
            _sym = str(_pos.get("symbol", "?"))
            _strategy = str(_pos.get("strategy", ""))
            _side = str(_pos.get("side", "BUY")).upper()
            _qty = int(_pos.get("qty", 0) or 0)
            _avg_price = float(_pos.get("avg_price", 0) or 0)
            _last_price = float(_pos.get("last_price") or _pos.get("avg_price", 0) or 0)
            _stop = float(_pos.get("stop_loss", 0) or 0)
            _target = float(_pos.get("target", 0) or 0)
            _unr = float(_pos.get("unrealized_pnl", 0) or 0)
            _opened_at = str(_pos.get("opened_at", ""))
            _pos_value = _qty * _last_price

            _pnl_pct = (_unr / (_avg_price * _qty) * 100) if (_avg_price * _qty) != 0 else 0.0
            _rr_ratio = (
                abs(_target - _avg_price) / abs(_avg_price - _stop)
                if _stop and _target and abs(_avg_price - _stop) > 0
                else None
            )
            _portfolio_pct = (_pos_value / _current_equity * 100) if _current_equity else 0.0

            _progress_val: float | None = None
            if _stop and _target and _stop != _target:
                _progress_val = (_last_price - _stop) / (_target - _stop)
                _progress_val = max(0.0, min(1.0, _progress_val))

            _side_color = "#1a7a4a" if _side == "BUY" else "#7a1a1a"
            _pnl_color = "#2ecc71" if _unr >= 0 else "#e74c3c"
            _strat_color = _STRATEGY_COLORS.get(_strategy.lower(), "#555")

            with st.container():
                st.markdown(
                    f"<div style='border:1px solid #333;border-radius:10px;padding:14px 18px;"
                    f"margin-bottom:14px;background:#1a1a1a'>",
                    unsafe_allow_html=True,
                )

                _hc1, _hc2, _hc3 = st.columns([3, 2, 1])
                _hc1.markdown(
                    f"<span style='font-size:1.3em;font-weight:700;color:#f0f0f0'>{safe_html(_sym)}</span>",
                    unsafe_allow_html=True,
                )
                _hc2.markdown(
                    f"<span style='background:{_strat_color};color:white;padding:3px 10px;"
                    f"border-radius:12px;font-size:0.8em;font-weight:600'>{safe_html(_strategy)}</span>",
                    unsafe_allow_html=True,
                )
                _hc3.markdown(
                    f"<span style='background:{_side_color};color:white;padding:3px 10px;"
                    f"border-radius:12px;font-size:0.8em;font-weight:700'>{safe_html(_side)}</span>",
                    unsafe_allow_html=True,
                )

                _r1c1, _r1c2, _r1c3, _r1c4 = st.columns(4)
                _r1c1.metric("Entry Price", f"₹{_avg_price:,.2f}")
                _r1c2.metric("Current Price", f"₹{_last_price:,.2f}")
                _r1c3.metric(
                    "P&L (₹)",
                    f"₹{_unr:+,.2f}",
                    delta_color="normal" if _unr >= 0 else "inverse",
                )
                _r1c4.metric(
                    "P&L (%)",
                    f"{_pnl_pct:+.2f}%",
                    delta_color="normal" if _pnl_pct >= 0 else "inverse",
                )

                _r2c1, _r2c2, _r2c3 = st.columns(3)
                _r2c1.markdown(
                    f"<div style='text-align:center'>"
                    f"<small style='color:#e74c3c;font-weight:600'>Stop Loss</small><br>"
                    f"<span style='font-size:1.1em;color:#e74c3c;font-weight:700'>"
                    f"{'₹' + f'{_stop:,.2f}' if _stop else 'N/A'}</span>"
                    f"</div>",
                    unsafe_allow_html=True,
                )
                _r2c2.markdown(
                    f"<div style='text-align:center'>"
                    f"<small style='color:#2ecc71;font-weight:600'>Target</small><br>"
                    f"<span style='font-size:1.1em;color:#2ecc71;font-weight:700'>"
                    f"{'₹' + f'{_target:,.2f}' if _target else 'N/A'}</span>"
                    f"</div>",
                    unsafe_allow_html=True,
                )
                _rr_display = f"{_rr_ratio:.2f}R" if _rr_ratio is not None else "N/A"
                _rr_color = "#2ecc71" if (_rr_ratio or 0) >= 1.5 else ("#f39c12" if (_rr_ratio or 0) >= 1.0 else "#e74c3c")
                _r2c3.markdown(
                    f"<div style='text-align:center'>"
                    f"<small style='color:#aaa;font-weight:600'>Risk:Reward</small><br>"
                    f"<span style='font-size:1.1em;color:{_rr_color};font-weight:700'>"
                    f"{_rr_display}</span>"
                    f"</div>",
                    unsafe_allow_html=True,
                )

                _r3c1, _r3c2, _r3c3, _r3c4 = st.columns(4)
                _r3c1.metric("Quantity", f"{_qty:,}")
                _r3c2.metric("Position Value", f"₹{_pos_value:,.0f}")
                _r3c3.metric("% of Portfolio", f"{_portfolio_pct:.1f}%")
                _r3c4.metric("Opened", time_ago(_opened_at))

                if _progress_val is not None:
                    _bar_color = "#2ecc71" if _progress_val >= 0.5 else "#f39c12"
                    if _progress_val <= 0.2:
                        _bar_color = "#e74c3c"
                    st.markdown(
                        f"<div style='margin-top:8px'>"
                        f"<small style='color:#aaa'>Price position: stop"
                        f" ← {_progress_val*100:.0f}% → target</small>"
                        f"</div>",
                        unsafe_allow_html=True,
                    )
                    st.progress(_progress_val)

                st.markdown("</div>", unsafe_allow_html=True)
    else:
        st.info("No open positions." + (" Agent not running." if not snap else ""))

    st.divider()

    st.subheader("Recent Signals")
    col_filter1, col_filter2 = st.columns(2)
    with col_filter1:
        show_rejected = st.checkbox("Show rejected signals", value=True)
    with col_filter2:
        limit_signals = st.selectbox("Show last N signals", [50, 100, 200, 500], index=0)

    sig_df = get_signals(str(DB_PATH), accepted_only=not show_rejected, limit=int(limit_signals))

    if not sig_df.empty:
        sig_display = [c for c in
            ["ts", "symbol", "side", "strategy", "regime", "entry_price",
             "stop_loss", "target", "confidence", "accepted", "rejection_reason", "rationale"]
            if c in sig_df.columns]

        def _accepted_style(val):
            return "color: green" if val == 1 else "color: red"

        styled_sig = sig_df[sig_display].style
        if "accepted" in sig_display:
            styled_sig = styled_sig.applymap(_accepted_style, subset=["accepted"])
        st.dataframe(styled_sig, use_container_width=True, height=400)

        if "strategy" in sig_df.columns and "accepted" in sig_df.columns:
            breakdown = sig_df.groupby(["strategy", "accepted"]).size().reset_index(name="count")
            breakdown["status"] = breakdown["accepted"].map({1: "Accepted", 0: "Rejected"})
            fig_sig = px.bar(breakdown, x="strategy", y="count", color="status",
                              color_discrete_map={"Accepted": "#2ecc71", "Rejected": "#e74c3c"},
                              title="Signal Acceptance by Strategy", barmode="group")
            fig_sig.update_layout(height=300, margin=dict(l=0, r=0, t=40, b=0))
            st.plotly_chart(fig_sig, use_container_width=True)
    else:
        st.info("No signals recorded yet.")

    st.divider()

    st.subheader("Guardrail Rejections (recent)")
    gr_df = query_df(conn, "SELECT * FROM guardrail_events ORDER BY id DESC LIMIT 100")
    if not gr_df.empty:
        st.dataframe(gr_df, use_container_width=True, height=250)
        rule_counts = gr_df["rule"].value_counts().reset_index()
        rule_counts.columns = ["rule", "count"]
        fig_gr = px.bar(rule_counts, x="count", y="rule", orientation="h",
                        title="Guardrail Rule Frequency",
                        color="count", color_continuous_scale="Reds")
        fig_gr.update_layout(height=300, margin=dict(l=0, r=0, t=40, b=0), showlegend=False)
        st.plotly_chart(fig_gr, use_container_width=True)
    else:
        st.info("No guardrail events recorded yet.")
