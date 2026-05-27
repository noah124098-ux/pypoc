"""Streamlit live dashboard for the NSE Trading Agent.

Launch with:
    streamlit run dashboard.py --server.port 8501

Reads from:
  - data/agent.db        — SQLite: trades, signals, equity snapshots, regime log, guardrail events
  - data/snapshot.json   — Live agent state (positions, equity, halt status)
  - data/backtest_gate.json — Last walk-forward gate result
  - config/default.yaml  — Active configuration
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st
from plotly.subplots import make_subplots

# ── page config ─────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="NSE Trading Agent",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── paths ────────────────────────────────────────────────────────────────────
DB_PATH = Path("data/agent.db")
SNAPSHOT_PATH = Path("data/snapshot.json")
GATE_PATH = Path("data/backtest_gate.json")
CONFIG_PATH = Path("config/default.yaml")

# ── helpers ──────────────────────────────────────────────────────────────────

def _db_connect():
    if not DB_PATH.exists():
        return None
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    return c


def _read_snapshot() -> dict:
    if not SNAPSHOT_PATH.exists():
        return {}
    try:
        return json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _read_gate() -> dict:
    if not GATE_PATH.exists():
        return {}
    try:
        return json.loads(GATE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _read_config() -> dict:
    if not CONFIG_PATH.exists():
        return {}
    try:
        import yaml
        return yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8")) or {}
    except Exception:
        return {}


def _query_df(conn, sql: str, params=()) -> pd.DataFrame:
    if conn is None:
        return pd.DataFrame()
    try:
        return pd.read_sql_query(sql, conn, params=params)
    except Exception:
        return pd.DataFrame()


def _color_pnl(val):
    color = "green" if val > 0 else ("red" if val < 0 else "gray")
    return f"color: {color}; font-weight: bold"


def _regime_color(regime: str) -> str:
    return {"TREND": "#2ecc71", "RANGE": "#3498db", "VOLATILE": "#e74c3c", "UNKNOWN": "#95a5a6"}.get(regime, "#95a5a6")


# ── sidebar ──────────────────────────────────────────────────────────────────

with st.sidebar:
    st.title("NSE Trading Agent")
    auto_refresh = st.checkbox("Auto-refresh (30s)", value=False)
    if auto_refresh:
        import time
        st.caption(f"Last refresh: {datetime.now().strftime('%H:%M:%S')}")

    st.divider()
    config = _read_config()
    snap = _read_snapshot()
    gate = _read_gate()

    mode = config.get("mode", "unknown").upper()
    mode_color = "#e74c3c" if mode == "LIVE" else "#2ecc71"
    st.markdown(f"**Mode:** <span style='color:{mode_color}'>{mode}</span>", unsafe_allow_html=True)

    capital = config.get("capital", {}).get("initial_inr", 0)
    st.markdown(f"**Capital:** ₹{capital:,.0f}")

    st.divider()
    gate_passed = gate.get("passed", False)
    gate_color = "#2ecc71" if gate_passed else "#e74c3c"
    gate_label = "PASSED" if gate_passed else "FAILED"
    st.markdown(f"**Gate:** <span style='color:{gate_color}'>{gate_label}</span>", unsafe_allow_html=True)
    if gate.get("timestamp"):
        ts = gate["timestamp"][:19].replace("T", " ")
        st.caption(f"Last run: {ts}")

    st.divider()
    risk_cfg = config.get("risk", {})
    st.markdown("**Risk config**")
    st.caption(f"Per-trade risk: {risk_cfg.get('per_trade_risk_pct', '-')}%")
    st.caption(f"Max positions: {risk_cfg.get('max_open_positions', '-')}")
    st.caption(f"Daily loss circuit: -{risk_cfg.get('daily_loss_circuit_pct', '-')}%")
    st.caption(f"Drawdown circuit: -{risk_cfg.get('drawdown_circuit_pct', '-')}%")

    st.divider()
    if st.button("Refresh now"):
        st.rerun()

# ── main layout: tabs ────────────────────────────────────────────────────────

conn = _db_connect()
tab_pnl, tab_positions, tab_regime, tab_backtest = st.tabs([
    "📊 P&L & Equity",
    "📋 Positions & Signals",
    "🌡️ Regime & Market",
    "🔬 Backtest Results",
])

# ────────────────────────────────────────────────────────────────────────────
# TAB 1: P&L & EQUITY CURVE
# ────────────────────────────────────────────────────────────────────────────

with tab_pnl:
    st.header("P&L & Equity Curve")

    # Live metrics row
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
    day_delta_color = "normal" if day_pnl_pct >= 0 else "inverse"
    c4.metric("Day P&L", f"{day_pnl_pct:+.2f}%", delta_color=day_delta_color)
    c5.metric("Drawdown", f"-{dd_pct:.2f}%", delta_color="inverse")

    st.divider()

    # Equity curve
    eq_df = _query_df(conn, "SELECT ts, equity FROM equity_snapshots ORDER BY ts")
    if not eq_df.empty:
        eq_df["ts"] = pd.to_datetime(eq_df["ts"])
        eq_df = eq_df.sort_values("ts")

        # Compute drawdown series
        eq_df["peak"] = eq_df["equity"].cummax()
        eq_df["dd_pct"] = (eq_df["peak"] - eq_df["equity"]) / eq_df["peak"] * 100

        fig = make_subplots(
            rows=2, cols=1,
            shared_xaxes=True,
            row_heights=[0.7, 0.3],
            subplot_titles=("Equity Curve (₹)", "Drawdown (%)"),
            vertical_spacing=0.08,
        )
        fig.add_trace(
            go.Scatter(
                x=eq_df["ts"], y=eq_df["equity"],
                mode="lines", name="Equity",
                line=dict(color="#2ecc71", width=2),
                fill="tozeroy", fillcolor="rgba(46,204,113,0.1)",
            ),
            row=1, col=1,
        )
        fig.add_trace(
            go.Scatter(
                x=eq_df["ts"], y=-eq_df["dd_pct"],
                mode="lines", name="Drawdown",
                line=dict(color="#e74c3c", width=1.5),
                fill="tozeroy", fillcolor="rgba(231,76,60,0.15)",
            ),
            row=2, col=1,
        )
        fig.update_layout(height=500, showlegend=False, margin=dict(l=0, r=0, t=30, b=0))
        st.plotly_chart(fig, use_container_width=True)
    else:
        st.info("No equity snapshots yet. Run the agent to see the equity curve.")

    st.divider()

    # Recent trades
    st.subheader("Recent Closed Trades")
    trades_df = _query_df(conn, "SELECT * FROM trades ORDER BY closed_at DESC LIMIT 100")
    if not trades_df.empty:
        trades_df["pnl_net"] = trades_df["pnl"] - trades_df["charges"]
        display_cols = ["closed_at", "symbol", "side", "qty", "entry_price", "exit_price",
                        "pnl", "charges", "pnl_net", "strategy", "exit_reason"]
        display_cols = [c for c in display_cols if c in trades_df.columns]
        styled = trades_df[display_cols].style.applymap(
            _color_pnl, subset=["pnl", "pnl_net"] if "pnl_net" in display_cols else ["pnl"]
        ).format({
            "entry_price": "₹{:.2f}", "exit_price": "₹{:.2f}",
            "pnl": "₹{:.2f}", "charges": "₹{:.2f}",
            "pnl_net": "₹{:.2f}",
        })
        st.dataframe(styled, use_container_width=True, height=350)

        # Trade stats
        total_trades = len(trades_df)
        wins = (trades_df["pnl"] > 0).sum()
        win_rate = wins / total_trades * 100 if total_trades else 0
        avg_win = trades_df.loc[trades_df["pnl"] > 0, "pnl"].mean() if wins else 0
        avg_loss = trades_df.loc[trades_df["pnl"] < 0, "pnl"].mean() if (total_trades - wins) else 0
        pf = abs(avg_win * wins) / abs(avg_loss * (total_trades - wins)) if avg_loss and (total_trades - wins) else float("inf")

        s1, s2, s3, s4 = st.columns(4)
        s1.metric("Total Trades", total_trades)
        s2.metric("Win Rate", f"{win_rate:.1f}%")
        s3.metric("Avg Win / Loss", f"₹{avg_win:.0f} / ₹{avg_loss:.0f}")
        s4.metric("Profit Factor", f"{pf:.2f}" if pf != float("inf") else "∞")
    else:
        st.info("No closed trades yet.")

# ────────────────────────────────────────────────────────────────────────────
# TAB 2: POSITIONS & SIGNALS
# ────────────────────────────────────────────────────────────────────────────

with tab_positions:
    st.header("Open Positions & Recent Signals")

    # Open positions
    st.subheader("Open Positions")
    if positions_list:
        pos_df = pd.DataFrame(positions_list)
        pos_display_cols = [c for c in
            ["symbol", "qty", "avg_price", "last_price", "unrealized_pnl", "stop_loss", "target", "strategy", "opened_at"]
            if c in pos_df.columns]
        styled_pos = pos_df[pos_display_cols].style.applymap(
            _color_pnl, subset=["unrealized_pnl"] if "unrealized_pnl" in pos_df.columns else []
        )
        st.dataframe(styled_pos, use_container_width=True)
    else:
        agent_running = snap.get("running", snap != {})
        st.info("No open positions." + (" Agent not running." if not agent_running else ""))

    st.divider()

    # Recent signals
    st.subheader("Recent Signals")
    col_filter1, col_filter2 = st.columns(2)
    with col_filter1:
        show_rejected = st.checkbox("Show rejected signals", value=True)
    with col_filter2:
        limit_signals = st.selectbox("Show last N signals", [50, 100, 200, 500], index=0)

    where = "" if show_rejected else "WHERE accepted = 1"
    sig_df = _query_df(conn, f"SELECT * FROM signals {where} ORDER BY id DESC LIMIT {limit_signals}")

    if not sig_df.empty:
        sig_display = [c for c in
            ["ts", "symbol", "side", "strategy", "regime", "entry_price",
             "stop_loss", "target", "confidence", "accepted", "rejection_reason", "rationale"]
            if c in sig_df.columns]

        def _accepted_style(val):
            if val == 1:
                return "color: green"
            return "color: red"

        styled_sig = sig_df[sig_display].style.applymap(
            _accepted_style, subset=["accepted"] if "accepted" in sig_display else []
        )
        st.dataframe(styled_sig, use_container_width=True, height=400)

        # Signal breakdown chart
        if "strategy" in sig_df.columns and "accepted" in sig_df.columns:
            breakdown = sig_df.groupby(["strategy", "accepted"]).size().reset_index(name="count")
            breakdown["status"] = breakdown["accepted"].map({1: "Accepted", 0: "Rejected"})
            fig_sig = px.bar(
                breakdown, x="strategy", y="count", color="status",
                color_discrete_map={"Accepted": "#2ecc71", "Rejected": "#e74c3c"},
                title="Signal Acceptance by Strategy",
                barmode="group",
            )
            fig_sig.update_layout(height=300, margin=dict(l=0, r=0, t=40, b=0))
            st.plotly_chart(fig_sig, use_container_width=True)
    else:
        st.info("No signals recorded yet.")

    st.divider()

    # Guardrail rejections
    st.subheader("Guardrail Rejections (recent)")
    gr_df = _query_df(conn, "SELECT * FROM guardrail_events ORDER BY id DESC LIMIT 100")
    if not gr_df.empty:
        st.dataframe(gr_df, use_container_width=True, height=250)

        rule_counts = gr_df["rule"].value_counts().reset_index()
        rule_counts.columns = ["rule", "count"]
        fig_gr = px.bar(rule_counts, x="count", y="rule", orientation="h",
                        title="Guardrail Rule Firing Frequency",
                        color="count", color_continuous_scale="Reds")
        fig_gr.update_layout(height=300, margin=dict(l=0, r=0, t=40, b=0), showlegend=False)
        st.plotly_chart(fig_gr, use_container_width=True)
    else:
        st.info("No guardrail events recorded yet.")

# ────────────────────────────────────────────────────────────────────────────
# TAB 3: REGIME & MARKET STATE
# ────────────────────────────────────────────────────────────────────────────

with tab_regime:
    st.header("Regime & Market State")

    # Current regime from snapshot
    current_regime = snap.get("regime", "UNKNOWN")
    regime_color = _regime_color(current_regime)
    halted = snap.get("halted", False)
    halt_reason = snap.get("halt_reason", "")

    rc1, rc2, rc3, rc4 = st.columns(4)
    rc1.markdown(
        f"<div style='background:{regime_color};padding:16px;border-radius:8px;text-align:center'>"
        f"<h3 style='color:white;margin:0'>Current Regime</h3>"
        f"<h2 style='color:white;margin:4px 0'>{current_regime}</h2></div>",
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

    # Regime history timeline
    st.subheader("Regime History")
    reg_df = _query_df(conn, "SELECT * FROM regime_log ORDER BY ts DESC LIMIT 500")
    if not reg_df.empty:
        reg_df["ts"] = pd.to_datetime(reg_df["ts"])
        reg_df = reg_df.sort_values("ts")

        # Pie chart of regime distribution
        pie_col, timeline_col = st.columns([1, 2])
        with pie_col:
            reg_counts = reg_df["regime"].value_counts().reset_index()
            reg_counts.columns = ["regime", "count"]
            color_map = {r: _regime_color(r) for r in reg_counts["regime"]}
            fig_pie = px.pie(
                reg_counts, names="regime", values="count",
                color="regime", color_discrete_map=color_map,
                title="Regime Distribution",
            )
            fig_pie.update_layout(height=300, margin=dict(l=0, r=0, t=40, b=10))
            st.plotly_chart(fig_pie, use_container_width=True)

        with timeline_col:
            # Encode regime as numeric for color
            regime_num = {"TREND": 1, "RANGE": 2, "VOLATILE": 3, "UNKNOWN": 0}
            reg_df["regime_num"] = reg_df["regime"].map(regime_num)
            fig_tl = px.scatter(
                reg_df, x="ts", y="regime",
                color="regime", color_discrete_map={r: _regime_color(r) for r in reg_df["regime"].unique()},
                title="Regime Timeline",
                height=300,
            )
            fig_tl.update_traces(marker=dict(size=6))
            fig_tl.update_layout(margin=dict(l=0, r=0, t=40, b=0), showlegend=True)
            st.plotly_chart(fig_tl, use_container_width=True)

        # ADX and BB width over time
        if "adx" in reg_df.columns and "bb_width" in reg_df.columns:
            fig_ind = make_subplots(
                rows=2, cols=1,
                shared_xaxes=True,
                subplot_titles=("ADX (trend strength — >20 = TREND)", "BB Width (range tightness — <6% = RANGE)"),
                vertical_spacing=0.12,
            )
            fig_ind.add_trace(
                go.Scatter(x=reg_df["ts"], y=reg_df["adx"], name="ADX",
                           line=dict(color="#f39c12", width=1.5)),
                row=1, col=1,
            )
            fig_ind.add_hline(y=20, line_dash="dash", line_color="gray", row=1, col=1, annotation_text="Threshold 20")
            fig_ind.add_trace(
                go.Scatter(x=reg_df["ts"], y=reg_df["bb_width"], name="BB Width",
                           line=dict(color="#9b59b6", width=1.5)),
                row=2, col=1,
            )
            fig_ind.add_hline(y=0.06, line_dash="dash", line_color="gray", row=2, col=1, annotation_text="Threshold 6%")
            fig_ind.update_layout(height=400, margin=dict(l=0, r=0, t=30, b=0), showlegend=False)
            st.plotly_chart(fig_ind, use_container_width=True)
    else:
        st.info("No regime history yet. Run the agent to populate.")

# ────────────────────────────────────────────────────────────────────────────
# TAB 4: BACKTEST RESULTS
# ────────────────────────────────────────────────────────────────────────────

with tab_backtest:
    st.header("Backtest & Walk-Forward Results")

    if gate:
        gate_passed = gate.get("passed", False)
        gate_ts = gate.get("timestamp", "unknown")[:19].replace("T", " ")
        period_start = gate.get("period_start", "?")[:10] if gate.get("period_start") else "?"
        period_end = gate.get("period_end", "?")[:10] if gate.get("period_end") else "?"

        # Gate summary banner
        banner_color = "#1a7a4a" if gate_passed else "#7a1a1a"
        banner_label = "GATE PASSED" if gate_passed else "GATE FAILED"
        st.markdown(
            f"<div style='background:{banner_color};padding:16px;border-radius:8px;margin-bottom:16px'>"
            f"<h2 style='color:white;margin:0'>{banner_label}</h2>"
            f"<p style='color:#ddd;margin:4px 0'>Period: {period_start} → {period_end} &nbsp;|&nbsp; Run at: {gate_ts}</p>"
            f"</div>",
            unsafe_allow_html=True,
        )

        if gate.get("failures"):
            st.error(f"Failures: {', '.join(gate['failures'])}")

        # Metrics cards
        metrics = gate.get("metrics", {})
        checks = gate.get("checks", [])
        m1, m2, m3, m4, m5 = st.columns(5)
        def _metric_card(col, label, actual, threshold, direction):
            passed = actual >= threshold if direction == "ge" else actual <= threshold
            delta_label = f"{'≥' if direction == 'ge' else '≤'} {threshold}"
            col.metric(label, f"{actual:.2f}", delta=delta_label,
                       delta_color="normal" if passed else "inverse")

        check_map = {c["name"]: c for c in checks} if checks else {}
        for col, name in zip([m1, m2, m3, m4, m5], ["sharpe", "max_drawdown_pct", "win_rate_pct", "profit_factor", "n_trades"]):
            if name in check_map:
                c = check_map[name]
                _metric_card(col, name.replace("_", " ").title(), c["actual"], c["threshold"], c["direction"])

        st.divider()

        # Gate check details table
        st.subheader("Gate Check Details")
        if checks:
            checks_df = pd.DataFrame(checks)
            checks_df["status"] = checks_df["pass_"].apply(lambda x: "✅ PASS" if x else "❌ FAIL")
            checks_df["operator"] = checks_df["direction"].map({"ge": "≥", "le": "≤"})
            display_checks = checks_df[["name", "actual", "operator", "threshold", "status"]].copy()
            display_checks.columns = ["Metric", "Actual", "Op", "Threshold", "Status"]

            def _status_style(val):
                return "color: green; font-weight: bold" if "PASS" in str(val) else "color: red; font-weight: bold"

            st.dataframe(
                display_checks.style.applymap(_status_style, subset=["Status"]).format({"Actual": "{:.3f}", "Threshold": "{:.3f}"}),
                use_container_width=True, hide_index=True,
            )

        # Config used
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
        "To generate a new backtest result, run one of these commands in the terminal:\n\n"
        "```\n"
        "# Quick single-window backtest (365 days):\n"
        "python cli.py backtest --days 365\n\n"
        "# Full 3-year walk-forward (updates gate file):\n"
        "python cli.py walk-forward --years 3\n\n"
        "# Diagnose why the agent barely trades:\n"
        "python cli.py debug-rejections --days 365 --capital 500000\n"
        "```\n\n"
        "Then click **Refresh now** in the sidebar to see updated results."
    )

# ── auto-refresh ─────────────────────────────────────────────────────────────

if auto_refresh:
    import time
    time.sleep(30)
    st.rerun()
