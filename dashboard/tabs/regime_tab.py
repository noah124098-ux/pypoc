"""Regime & Market tab."""
from __future__ import annotations

from datetime import datetime

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st
from plotly.subplots import make_subplots

from dashboard.utils.charts import safe_html, regime_color
from dashboard.utils.db import query_df

_REGIME_COLOR_MAP = {
    "TREND": "#2ecc71",
    "RANGE": "#3498db",
    "VOLATILE": "#e67e22",
    "UNKNOWN": "#95a5a6",
}
_REGIME_ORDER = ["TREND", "RANGE", "VOLATILE", "UNKNOWN"]


def _nifty_dma_status(conn, nifty_ltp: float | None) -> dict:
    """Compute Nifty DMA status from the regime_log ADX/VIX history."""
    result = {
        "ltp": nifty_ltp,
        "above_200dma": None,
        "above_50dma": None,
        "dma50_rising": None,
    }
    if conn is None:
        return result

    try:
        df_rl = pd.read_sql_query(
            "SELECT ts, nifty_change_pct FROM regime_log ORDER BY ts ASC LIMIT 500",
            conn,
        )
        if df_rl.empty or "nifty_change_pct" not in df_rl.columns:
            return result
        df_rl["ts"] = pd.to_datetime(df_rl["ts"])
        df_rl = df_rl.dropna(subset=["nifty_change_pct"]).sort_values("ts").reset_index(drop=True)
        if len(df_rl) < 5:
            return result

        df_rl["synth_close"] = 1000.0 * (1 + df_rl["nifty_change_pct"] / 100).cumprod()
        closes = df_rl["synth_close"]

        last_price = nifty_ltp if (nifty_ltp and nifty_ltp > 0) else closes.iloc[-1]

        dma50 = closes.tail(50).mean() if len(closes) >= 50 else closes.mean()
        dma200 = closes.tail(200).mean() if len(closes) >= 200 else closes.mean()

        if len(closes) >= 60:
            dma50_prev = closes.iloc[-60:-10].mean()
            result["dma50_rising"] = bool(dma50 > dma50_prev)
        else:
            result["dma50_rising"] = None

        if nifty_ltp and nifty_ltp > 0 and closes.iloc[-1] > 0:
            scale = nifty_ltp / closes.iloc[-1]
            dma50 *= scale
            dma200 *= scale

        result["above_50dma"] = bool(last_price > dma50)
        result["above_200dma"] = bool(last_price > dma200)
    except Exception:
        pass
    return result


def render(snap: dict, conn) -> None:
    """Render the Regime & Market tab."""
    st.header("Regime & Market State")

    current_regime = snap.get("regime", "UNKNOWN")
    _regime_color = regime_color(current_regime)
    halted = snap.get("halted", False)
    halt_reason = snap.get("halt_reason", "")

    rc1, rc2, rc3, rc4 = st.columns(4)
    rc1.markdown(
        f"<div style='background:{_regime_color};padding:16px;border-radius:8px;text-align:center'>"
        f"<h3 style='color:white;margin:0'>Current Regime</h3>"
        f"<h2 style='color:white;margin:4px 0'>{safe_html(current_regime)}</h2></div>",
        unsafe_allow_html=True,
    )
    halt_bg = "#e74c3c" if halted else "#2ecc71"
    rc2.markdown(
        f"<div style='background:{halt_bg};padding:16px;border-radius:8px;text-align:center'>"
        f"<h3 style='color:white;margin:0'>Agent Status</h3>"
        f"<h2 style='color:white;margin:4px 0'>{'HALTED' if halted else 'ACTIVE'}</h2></div>",
        unsafe_allow_html=True,
    )
    if snap:
        vix = snap.get("vix", "n/a")
        adx = snap.get("adx", "n/a")
        rc3.metric("VIX (proxy)", f"{vix:.1f}" if isinstance(vix, float) else vix)
        rc4.metric("ADX", f"{adx:.1f}" if isinstance(adx, float) else adx)

    if halted and halt_reason:
        st.error(f"HALT reason: {halt_reason}")

    st.divider()

    # Nifty DMA Status
    st.subheader("Nifty DMA Status")

    _nifty_ltp_raw = snap.get("nifty_ltp", None)
    try:
        _nifty_ltp = float(_nifty_ltp_raw) if _nifty_ltp_raw not in (None, "", "n/a") else None
    except (TypeError, ValueError):
        _nifty_ltp = None

    _pcr_raw = snap.get("pcr", None)
    try:
        _pcr_val = float(_pcr_raw) if _pcr_raw not in (None, "", "n/a") else None
    except (TypeError, ValueError):
        _pcr_val = None

    _dma_status = _nifty_dma_status(conn, _nifty_ltp)

    def _yn_metric(col, label: str, value, true_color: str = "#2ecc71", false_color: str = "#e74c3c"):
        """Render a Yes/No/N/A metric with color."""
        if value is None:
            col.metric(label, "N/A")
        else:
            color = true_color if value else false_color
            yn = "Yes" if value else "No"
            col.markdown(
                f"<div style='text-align:center'>"
                f"<small style='color:#888'>{label}</small><br>"
                f"<span style='font-size:1.4em;font-weight:bold;color:{color}'>{yn}</span>"
                f"</div>",
                unsafe_allow_html=True,
            )

    dma_c1, dma_c2, dma_c3, dma_c4, dma_c5 = st.columns(5)

    ltp_display = f"{_nifty_ltp:,.1f}" if _nifty_ltp else "N/A"
    dma_c1.metric("Nifty LTP", ltp_display)

    _yn_metric(dma_c2, "Above 200-DMA", _dma_status["above_200dma"])
    _yn_metric(dma_c3, "Above 50-DMA", _dma_status["above_50dma"])
    _yn_metric(dma_c4, "50-DMA Rising", _dma_status["dma50_rising"])

    if _pcr_val is not None:
        pcr_color = "#2ecc71" if _pcr_val > 1.0 else ("#e74c3c" if _pcr_val < 0.7 else "#f39c12")
        dma_c5.markdown(
            f"<div style='text-align:center'>"
            f"<small style='color:#888'>PCR</small><br>"
            f"<span style='font-size:1.4em;font-weight:bold;color:{pcr_color}'>{_pcr_val:.2f}</span>"
            f"</div>",
            unsafe_allow_html=True,
        )
    else:
        dma_c5.metric("PCR", "N/A")

    if _nifty_ltp is None:
        st.caption("Nifty LTP not in snapshot — DMA values derived from synthetic regime_log series.")

    st.divider()

    # Regime History charts
    st.subheader("Regime History")
    reg_df = query_df(conn, "SELECT ts, regime, adx, bb_width, vix FROM regime_log ORDER BY ts DESC LIMIT 500")
    if not reg_df.empty:
        reg_df["ts"] = pd.to_datetime(reg_df["ts"])
        reg_df = reg_df.sort_values("ts").reset_index(drop=True)

        # 1. Color-coded timeline
        st.markdown("**Regime Timeline (last 500 entries)**")
        fig_tl = go.Figure()
        for regime_name in _REGIME_ORDER:
            sub = reg_df[reg_df["regime"] == regime_name]
            if sub.empty:
                continue
            fig_tl.add_trace(go.Scatter(
                x=sub["ts"],
                y=[regime_name] * len(sub),
                mode="markers+lines",
                name=regime_name,
                marker=dict(color=_REGIME_COLOR_MAP[regime_name], size=7, symbol="circle"),
                line=dict(color=_REGIME_COLOR_MAP[regime_name], width=1.5, dash="dot"),
                connectgaps=False,
            ))
        fig_tl.update_layout(
            height=280,
            margin=dict(l=0, r=0, t=20, b=0),
            yaxis=dict(
                title="Regime",
                categoryorder="array",
                categoryarray=_REGIME_ORDER,
            ),
            xaxis_title="Timestamp",
            legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
            showlegend=True,
        )
        st.plotly_chart(fig_tl, use_container_width=True)

        # 2. Distribution charts: all-time + last 30 days
        pie_col1, pie_col2 = st.columns(2)

        with pie_col1:
            reg_counts_all = reg_df["regime"].value_counts().reset_index()
            reg_counts_all.columns = ["regime", "count"]
            color_map_all = {r: _REGIME_COLOR_MAP.get(r, "#95a5a6") for r in reg_counts_all["regime"]}
            fig_pie_all = px.pie(
                reg_counts_all, names="regime", values="count",
                color="regime", color_discrete_map=color_map_all,
                title="Regime Distribution (all history)",
            )
            fig_pie_all.update_layout(height=300, margin=dict(l=0, r=0, t=40, b=10))
            st.plotly_chart(fig_pie_all, use_container_width=True)

        with pie_col2:
            cutoff_30d = pd.Timestamp.utcnow().tz_localize(None) - pd.Timedelta(days=30)
            reg_df_30 = reg_df[reg_df["ts"] >= cutoff_30d]
            if not reg_df_30.empty:
                reg_counts_30 = reg_df_30["regime"].value_counts().reset_index()
                reg_counts_30.columns = ["regime", "count"]
                color_map_30 = {r: _REGIME_COLOR_MAP.get(r, "#95a5a6") for r in reg_counts_30["regime"]}
                fig_pie_30 = px.pie(
                    reg_counts_30, names="regime", values="count",
                    color="regime", color_discrete_map=color_map_30,
                    title="Regime Distribution (last 30 days)",
                )
                fig_pie_30.update_layout(height=300, margin=dict(l=0, r=0, t=40, b=10))
                st.plotly_chart(fig_pie_30, use_container_width=True)
            else:
                st.info("No regime entries in the last 30 days.")

        # 3. Regime transition table
        st.subheader("Last 20 Regime Transitions")
        reg_df_sorted = reg_df.sort_values("ts").reset_index(drop=True)
        transitions = []
        prev_regime = None
        for _, row in reg_df_sorted.iterrows():
            if prev_regime is not None and row["regime"] != prev_regime:
                transitions.append({
                    "Timestamp": row["ts"].strftime("%Y-%m-%d %H:%M"),
                    "From": prev_regime,
                    "To": row["regime"],
                })
            prev_regime = row["regime"]

        if transitions:
            trans_df = pd.DataFrame(transitions[-20:]).iloc[::-1].reset_index(drop=True)

            def _color_regime_cell(val: str) -> str:
                color = _REGIME_COLOR_MAP.get(str(val), "#95a5a6")
                return f"background-color:{color};color:white;font-weight:bold;border-radius:4px"

            styled_trans = trans_df.style.applymap(
                _color_regime_cell, subset=["From", "To"]
            )
            st.dataframe(styled_trans, use_container_width=True, hide_index=True)
        else:
            st.info("No regime transitions recorded yet (need at least 2 entries).")

        # 4. ADX / BB Width indicator subplots
        if "adx" in reg_df.columns and "bb_width" in reg_df.columns:
            st.subheader("Market Indicators")
            fig_ind = make_subplots(
                rows=2, cols=1, shared_xaxes=True,
                subplot_titles=("ADX (>20 = TREND)", "BB Width (<6% = RANGE)"),
                vertical_spacing=0.12,
            )
            fig_ind.add_trace(go.Scatter(
                x=reg_df["ts"], y=reg_df["adx"], name="ADX",
                line=dict(color="#f39c12", width=1.5),
            ), row=1, col=1)
            fig_ind.add_hline(y=20, line_dash="dash", line_color="gray", row=1, col=1)
            fig_ind.add_trace(go.Scatter(
                x=reg_df["ts"], y=reg_df["bb_width"], name="BB Width",
                line=dict(color="#9b59b6", width=1.5),
            ), row=2, col=1)
            fig_ind.add_hline(y=0.06, line_dash="dash", line_color="gray", row=2, col=1)
            fig_ind.update_layout(height=400, margin=dict(l=0, r=0, t=30, b=0), showlegend=False)
            st.plotly_chart(fig_ind, use_container_width=True)
    else:
        st.info("No regime history yet. Run the agent to populate.")

    st.divider()

    # FII/DII 5-day grouped bar chart
    st.subheader("Institutional Flows (FII vs DII) — Last 5 Days")
    try:
        from core.data.nse_fii_dii import get_fii_dii_flows as _get_flows
        _flows = _get_flows(days=5)
        if _flows:
            import plotly.graph_objects as _go_fii
            _dates = [f.date for f in reversed(_flows)]
            _fii_vals = [f.fii_net_inr_cr for f in reversed(_flows)]
            _dii_vals = [f.dii_net_inr_cr for f in reversed(_flows)]

            _fig_fii = _go_fii.Figure()
            _fig_fii.add_trace(_go_fii.Bar(
                x=_dates, y=_fii_vals, name="FII Net",
                marker_color=["#2980b9" if v >= 0 else "#c0392b" for v in _fii_vals],
                text=[f"₹{v:+,.0f}Cr" for v in _fii_vals],
                textposition="outside",
            ))
            _fig_fii.add_trace(_go_fii.Bar(
                x=_dates, y=_dii_vals, name="DII Net",
                marker_color=["#27ae60" if v >= 0 else "#e67e22" for v in _dii_vals],
                text=[f"₹{v:+,.0f}Cr" for v in _dii_vals],
                textposition="outside",
            ))
            _fig_fii.update_layout(
                barmode="group",
                height=320,
                margin=dict(l=0, r=0, t=20, b=0),
                yaxis_title="Net Flow (₹ Cr)",
                xaxis_title="Date",
                legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
            )
            _fig_fii.add_hline(y=0, line_dash="solid", line_color="gray", line_width=1)
            st.plotly_chart(_fig_fii, use_container_width=True)
            _fii_caption_parts = []
            _avg_fii = sum(_fii_vals) / len(_fii_vals)
            _fii_caption_parts.append(f"5-day avg FII: ₹{_avg_fii:+,.0f}Cr")
            _avg_dii = sum(_dii_vals) / len(_dii_vals)
            _fii_caption_parts.append(f"DII: ₹{_avg_dii:+,.0f}Cr")
            st.caption(" | ".join(_fii_caption_parts))
        else:
            st.info("FII/DII flow data unavailable (market may be closed or NSE unreachable).")
    except Exception as _fii_exc:
        st.caption(f"FII/DII chart unavailable: {_fii_exc}")

    st.divider()

    # Upcoming Blackout Events widget
    st.subheader("Upcoming Blackout Events")
    try:
        from core.data.economic_calendar import _ALL_EVENT_DATES, is_blackout_day
        _ec_today = datetime.now().date()
        _ec_upcoming = sorted([d for d in _ALL_EVENT_DATES if d >= _ec_today])[:3]
        if _ec_upcoming:
            st.caption("**Upcoming blackout events (no new entries ±1 day):**")
            for _ec_ev in _ec_upcoming:
                _ec_days_away = (_ec_ev - _ec_today).days
                _ec_label = "🔴 TODAY" if _ec_days_away == 0 else f"in {_ec_days_away}d"
                st.caption(f"  • {_ec_ev.strftime('%b %d %Y')} ({_ec_label})")
        else:
            st.caption("No upcoming blackout events in the calendar.")
    except Exception as _ec_exc:
        st.caption(f"Economic calendar unavailable: {_ec_exc}")
