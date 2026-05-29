"""Streamlit live dashboard for the NSE Trading Agent.

Launch with:
    streamlit run dashboard.py --server.port 8501

Reads from:
  - data/agent.db        -- SQLite: trades, signals, equity snapshots, regime log, guardrail events
  - data/snapshot.json   -- Live agent state (positions, equity, halt status)
  - data/backtest_gate.json -- Last walk-forward gate result
  - config/default.yaml  -- Active configuration
  - .env                 -- Angel One credentials (can also enter in UI)
"""
from __future__ import annotations

import json
import os
import signal
import sqlite3
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st
from plotly.subplots import make_subplots

st.set_page_config(
    page_title="NSE Trading Agent",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="expanded",
)

DB_PATH = Path("data/agent.db")
SNAPSHOT_PATH = Path("data/snapshot.json")
GATE_PATH = Path("data/backtest_gate.json")
CONFIG_PATH = Path("config/default.yaml")
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


def _start_agent() -> str:
    """Launch the agent as a background subprocess. Returns status message."""
    try:
        python = sys.executable
        proc = subprocess.Popen(
            [python, "cli.py", "run"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            cwd=str(Path(__file__).parent),
        )
        AGENT_PID_PATH.parent.mkdir(parents=True, exist_ok=True)
        AGENT_PID_PATH.write_text(str(proc.pid))
        return f"Agent started (PID {proc.pid})"
    except Exception as e:
        return f"Failed to start agent: {e}"


def _stop_agent() -> str:
    """Send SIGTERM to the agent process."""
    if not AGENT_PID_PATH.exists():
        return "No agent PID file found."
    try:
        pid = int(AGENT_PID_PATH.read_text().strip())
        import psutil
        if psutil.pid_exists(pid):
            proc = psutil.Process(pid)
            proc.terminate()
            AGENT_PID_PATH.unlink(missing_ok=True)
            return f"Agent (PID {pid}) stopped."
        else:
            AGENT_PID_PATH.unlink(missing_ok=True)
            return "Agent was not running (PID file removed)."
    except Exception as e:
        return f"Error stopping agent: {e}"


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


@st.cache_data(ttl=60, show_spinner=False)
def _fetch_angel_one_data(api_key: str, client_code: str, password: str, totp_secret: str) -> dict:
    """Fetch live positions, funds, and order book from Angel One. DATA-ONLY."""
    result = {"positions": [], "funds": {}, "orders": [], "error": None, "connected": False}
    if not all([api_key, client_code, password, totp_secret]):
        result["error"] = "Angel One credentials not set in .env"
        return result
    try:
        import pyotp
        from SmartApi import SmartConnect
        smart = SmartConnect(api_key=api_key)
        totp = pyotp.TOTP(totp_secret).now()
        resp = smart.generateSession(client_code, password, totp)
        if not resp.get("status"):
            result["error"] = f"Login failed: {resp.get('message', 'unknown')}"
            return result

        # Neutralize order methods immediately after login (DATA-ONLY contract)
        for method in ("placeOrder", "modifyOrder", "cancelOrder", "placeOrderFullResponse"):
            if hasattr(smart, method):
                setattr(smart, method, lambda *a, **k: (_ for _ in ()).throw(
                    RuntimeError("DATA-ONLY: order methods disabled in dashboard")))

        result["connected"] = True

        # Positions (read-only)
        try:
            pos_resp = smart.getPosition()
            if pos_resp.get("status") and pos_resp.get("data"):
                result["positions"] = pos_resp["data"]
        except Exception as e:
            result["positions_error"] = str(e)

        # Funds / RMS (read-only)
        try:
            rms_resp = smart.rmsLimit()
            if rms_resp.get("status") and rms_resp.get("data"):
                result["funds"] = rms_resp["data"]
        except Exception as e:
            result["funds_error"] = str(e)

        # Order book (read-only)
        try:
            order_resp = smart.orderBook()
            if order_resp.get("status") and order_resp.get("data"):
                result["orders"] = order_resp["data"]
        except Exception as e:
            result["orders_error"] = str(e)

    except Exception as e:
        result["error"] = str(e)
    return result


def _load_env_creds() -> tuple[str, str, str, str]:
    """Load Angel One credentials: session-state UI inputs take precedence over .env."""
    from dotenv import load_dotenv
    load_dotenv(override=False)
    ss = st.session_state
    return (
        ss.get("ao_api_key") or os.getenv("ANGEL_ONE_API_KEY", ""),
        ss.get("ao_client_code") or os.getenv("ANGEL_ONE_CLIENT_CODE", ""),
        ss.get("ao_password") or os.getenv("ANGEL_ONE_PASSWORD", ""),
        ss.get("ao_totp_secret") or os.getenv("ANGEL_ONE_TOTP_SECRET", ""),
    )


def _pnl_by_period(trades_df: pd.DataFrame) -> dict[str, pd.DataFrame]:
    """Return dict of period -> grouped P&L DataFrames."""
    if trades_df.empty:
        return {}
    df = trades_df.copy()
    df["closed_at"] = pd.to_datetime(df["closed_at"])
    df["pnl_net"] = df["pnl"] - df["charges"]
    df["date"] = df["closed_at"].dt.date
    df["week"] = df["closed_at"].dt.to_period("W").apply(lambda x: str(x.start_time.date()))
    df["month"] = df["closed_at"].dt.to_period("M").astype(str)
    df["quarter"] = df["closed_at"].dt.to_period("Q").astype(str)

    cutoff_3m = datetime.utcnow() - timedelta(days=90)
    df_3m = df[df["closed_at"] >= cutoff_3m]

    return {
        "daily": df.groupby("date")["pnl_net"].sum().reset_index().rename(columns={"date": "period"}),
        "weekly": df.groupby("week")["pnl_net"].sum().reset_index().rename(columns={"week": "period"}),
        "monthly": df.groupby("month")["pnl_net"].sum().reset_index().rename(columns={"month": "period"}),
        "3month": df_3m.groupby("month")["pnl_net"].sum().reset_index().rename(columns={"month": "period"}),
    }


# ── sidebar ──────────────────────────────────────────────────────────────────

with st.sidebar:
    st.title("NSE Trading Agent")
    auto_refresh = st.checkbox("Auto-refresh (30s)", value=False)
    if auto_refresh:
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
    _running = _agent_is_running()
    _run_color = "#2ecc71" if _running else "#e74c3c"
    _run_label = "RUNNING" if _running else "STOPPED"
    st.markdown(f"**Agent:** <span style='color:{_run_color}'>{_run_label}</span>", unsafe_allow_html=True)

    st.divider()
    if st.button("Refresh now"):
        st.cache_data.clear()
        st.rerun()


# ── tabs ─────────────────────────────────────────────────────────────────────

conn = _db_connect()
tab_live, tab_pnl, tab_positions, tab_regime, tab_backtest = st.tabs([
    "🔴 Live Account",
    "📊 P&L & Equity",
    "📋 Positions & Signals",
    "🌡️ Regime & Market",
    "🔬 Backtest Results",
])


# ────────────────────────────────────────────────────────────────────────────
# TAB 1: LIVE ANGEL ONE ACCOUNT
# ────────────────────────────────────────────────────────────────────────────

with tab_live:
    st.header("Live Angel One Account")

    # ── Agent start/stop controls ────────────────────────────────────────────
    agent_running = _agent_is_running()
    agent_col1, agent_col2, agent_col3 = st.columns([1, 1, 3])
    status_color = "#2ecc71" if agent_running else "#e74c3c"
    status_label = "RUNNING" if agent_running else "STOPPED"
    agent_col1.markdown(
        f"<div style='background:{status_color};padding:10px;border-radius:6px;text-align:center'>"
        f"<b style='color:white'>Agent: {status_label}</b></div>",
        unsafe_allow_html=True,
    )
    with agent_col2:
        if agent_running:
            if st.button("Stop Agent", type="secondary", use_container_width=True):
                msg = _stop_agent()
                st.toast(msg)
                st.rerun()
        else:
            if st.button("Start Agent", type="primary", use_container_width=True):
                msg = _start_agent()
                st.toast(msg)
                st.rerun()

    st.divider()

    # ── Angel One credential form ─────────────────────────────────────────────
    api_key, client_code, password, totp_secret = _load_env_creds()
    creds_present = all([api_key, client_code, password, totp_secret])

    with st.expander(
        "Connect Angel One Account" + (" (connected)" if creds_present else " (not configured)"),
        expanded=not creds_present,
    ):
        st.caption("Credentials are stored in session state only — they are never written to disk here.")
        with st.form("ao_creds_form"):
            c1, c2 = st.columns(2)
            inp_api = c1.text_input("API Key", value=api_key, type="password", key="_inp_api")
            inp_code = c2.text_input("Client Code", value=client_code, key="_inp_code")
            inp_pwd = c1.text_input("Password", value=password, type="password", key="_inp_pwd")
            inp_totp = c2.text_input("TOTP Secret", value=totp_secret, type="password", key="_inp_totp")
            col_save, col_clear = st.columns(2)
            if col_save.form_submit_button("Connect", type="primary", use_container_width=True):
                st.session_state["ao_api_key"] = inp_api
                st.session_state["ao_client_code"] = inp_code
                st.session_state["ao_password"] = inp_pwd
                st.session_state["ao_totp_secret"] = inp_totp
                st.cache_data.clear()
                st.rerun()
            if col_clear.form_submit_button("Disconnect", use_container_width=True):
                for k in ("ao_api_key", "ao_client_code", "ao_password", "ao_totp_secret"):
                    st.session_state.pop(k, None)
                st.cache_data.clear()
                st.rerun()

    # re-read creds after potential form submit
    api_key, client_code, password, totp_secret = _load_env_creds()
    creds_present = all([api_key, client_code, password, totp_secret])

    if not creds_present:
        st.info("Enter your Angel One credentials above to view live account data.")
    else:
        with st.spinner("Connecting to Angel One (DATA-ONLY)..."):
            ao = _fetch_angel_one_data(api_key, client_code, password, totp_secret)

        if ao.get("error"):
            st.error(f"Angel One connection failed: {ao['error']}")
        else:
            conn_color = "#2ecc71" if ao["connected"] else "#e74c3c"
            st.markdown(
                f"<span style='background:{conn_color};color:white;padding:4px 12px;"
                f"border-radius:4px;font-weight:bold'>"
                f"{'CONNECTED' if ao['connected'] else 'DISCONNECTED'} — DATA-ONLY</span>",
                unsafe_allow_html=True,
            )
            st.caption("Order methods are disabled. This tab reads account data only.")
            st.divider()

            # Funds
            funds = ao.get("funds", {})
            if funds:
                st.subheader("Account Funds & Margin")
                f1, f2, f3, f4 = st.columns(4)
                def _inr(key, default=0):
                    try:
                        return float(funds.get(key, default) or 0)
                    except Exception:
                        return 0.0
                net_cash = _inr("net")
                available = _inr("availablecash")
                used_margin = _inr("utilisedamount")
                m2m = _inr("m2munrealisedprofit")
                f1.metric("Net Available", f"₹{net_cash:,.0f}")
                f2.metric("Available Cash", f"₹{available:,.0f}")
                f3.metric("Used Margin", f"₹{used_margin:,.0f}")
                f4.metric("M2M Unrealised", f"₹{m2m:,.0f}", delta_color="normal" if m2m >= 0 else "inverse")
                st.divider()

            # Live positions from Angel One
            positions = ao.get("positions", [])
            st.subheader(f"Angel One Positions ({len(positions)} open)")
            if positions:
                pos_df = pd.DataFrame(positions)
                # Normalise key column names across Angel One API versions
                rename_map = {
                    "tradingsymbol": "symbol", "symboltoken": "token",
                    "netqty": "qty", "netprice": "avg_price",
                    "ltp": "ltp", "unrealised": "unrealised_pnl",
                    "realised": "realised_pnl", "pnl": "pnl",
                    "producttype": "product",
                }
                pos_df = pos_df.rename(columns={k: v for k, v in rename_map.items() if k in pos_df.columns})
                display_cols = [c for c in ["symbol", "qty", "avg_price", "ltp",
                                             "unrealised_pnl", "realised_pnl", "pnl", "product"]
                                if c in pos_df.columns]
                pnl_cols = [c for c in ["unrealised_pnl", "realised_pnl", "pnl"] if c in display_cols]
                styled = pos_df[display_cols].style
                if pnl_cols:
                    styled = styled.applymap(_color_pnl, subset=pnl_cols)
                    styled = styled.format({c: "₹{:.2f}" for c in pnl_cols})
                if "avg_price" in display_cols:
                    styled = styled.format({"avg_price": "₹{:.2f}", "ltp": "₹{:.2f}"})
                st.dataframe(styled, use_container_width=True)
            else:
                st.info("No open positions in Angel One account.")

            st.divider()

            # Order book
            orders = ao.get("orders", [])
            st.subheader(f"Today's Order Book ({len(orders)} orders)")
            if orders:
                ord_df = pd.DataFrame(orders)
                ord_rename = {
                    "tradingsymbol": "symbol", "transactiontype": "side",
                    "quantity": "qty", "price": "price", "status": "status",
                    "ordertype": "order_type", "producttype": "product",
                    "updatetime": "updated_at",
                }
                ord_df = ord_df.rename(columns={k: v for k, v in ord_rename.items() if k in ord_df.columns})
                display_ord = [c for c in ["updated_at", "symbol", "side", "qty",
                                            "price", "order_type", "status", "product"]
                               if c in ord_df.columns]

                def _order_status_style(val):
                    if str(val).upper() in ("COMPLETE", "FILLED"):
                        return "color: green"
                    if str(val).upper() in ("REJECTED", "CANCELLED"):
                        return "color: red"
                    return "color: orange"

                ord_styled = ord_df[display_ord].style
                if "status" in display_ord:
                    ord_styled = ord_styled.applymap(_order_status_style, subset=["status"])
                st.dataframe(ord_styled, use_container_width=True, height=300)
            else:
                st.info("No orders today.")

    st.divider()

    # Paper agent open positions (always shown regardless of Angel One creds)
    st.subheader("Paper Agent — Open Positions")
    positions_list = snap.get("open_positions", [])
    if positions_list:
        pos_df = pd.DataFrame(positions_list)
        display_cols = [c for c in ["symbol", "qty", "avg_price", "last_price",
                                     "unrealized_pnl", "stop_loss", "target", "strategy", "opened_at"]
                        if c in pos_df.columns]
        pnl_cols = [c for c in ["unrealized_pnl"] if c in display_cols]
        styled = pos_df[display_cols].style
        if pnl_cols:
            styled = styled.applymap(_color_pnl, subset=pnl_cols)
        st.dataframe(styled, use_container_width=True)
    else:
        st.info("Paper agent has no open positions." + (" (Agent not running)" if not snap else ""))


# ────────────────────────────────────────────────────────────────────────────
# TAB 2: P&L & EQUITY
# ────────────────────────────────────────────────────────────────────────────

with tab_pnl:
    st.header("P&L & Equity Curve")

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
    eq_df = _query_df(conn, "SELECT ts, equity FROM equity_snapshots ORDER BY ts")
    if not eq_df.empty:
        eq_df["ts"] = pd.to_datetime(eq_df["ts"])
        eq_df = eq_df.sort_values("ts")
        eq_df["peak"] = eq_df["equity"].cummax()
        eq_df["dd_pct"] = (eq_df["peak"] - eq_df["equity"]) / eq_df["peak"] * 100

        fig = make_subplots(rows=2, cols=1, shared_xaxes=True, row_heights=[0.7, 0.3],
                            subplot_titles=("Equity Curve (INR)", "Drawdown (%)"),
                            vertical_spacing=0.08)
        fig.add_trace(go.Scatter(x=eq_df["ts"], y=eq_df["equity"], mode="lines", name="Equity",
                                  line=dict(color="#2ecc71", width=2),
                                  fill="tozeroy", fillcolor="rgba(46,204,113,0.1)"), row=1, col=1)
        fig.add_trace(go.Scatter(x=eq_df["ts"], y=-eq_df["dd_pct"], mode="lines", name="Drawdown",
                                  line=dict(color="#e74c3c", width=1.5),
                                  fill="tozeroy", fillcolor="rgba(231,76,60,0.15)"), row=2, col=1)
        fig.update_layout(height=500, showlegend=False, margin=dict(l=0, r=0, t=30, b=0))
        st.plotly_chart(fig, use_container_width=True)
    else:
        st.info("No equity snapshots yet. Run the agent to see the equity curve.")

    st.divider()

    # P&L breakdown by period
    st.subheader("P&L Breakdown")
    trades_df = _query_df(conn, "SELECT * FROM trades ORDER BY closed_at DESC")
    pnl_periods = _pnl_by_period(trades_df)

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
                pdata["color"] = pdata["pnl_net"].apply(lambda x: "#2ecc71" if x >= 0 else "#e74c3c")
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
        display_cols = ["closed_at", "symbol", "side", "qty", "entry_price", "exit_price",
                        "pnl", "charges", "pnl_net", "strategy", "exit_reason"]
        display_cols = [c for c in display_cols if c in trades_df.columns]
        pnl_cols = [c for c in ["pnl", "pnl_net"] if c in display_cols]
        styled = trades_df[display_cols].head(100).style.applymap(_color_pnl, subset=pnl_cols)
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
    else:
        st.info("No closed trades yet.")


# ────────────────────────────────────────────────────────────────────────────
# TAB 3: POSITIONS & SIGNALS
# ────────────────────────────────────────────────────────────────────────────

with tab_positions:
    st.header("Open Positions & Recent Signals")

    st.subheader("Open Positions")
    if positions_list:
        pos_df = pd.DataFrame(positions_list)
        pos_display_cols = [c for c in
            ["symbol", "qty", "avg_price", "last_price", "unrealized_pnl", "stop_loss", "target", "strategy", "opened_at"]
            if c in pos_df.columns]
        styled_pos = pos_df[pos_display_cols].style
        if "unrealized_pnl" in pos_df.columns:
            styled_pos = styled_pos.applymap(_color_pnl, subset=["unrealized_pnl"])
        st.dataframe(styled_pos, use_container_width=True)
    else:
        st.info("No open positions." + (" Agent not running." if not snap else ""))

    st.divider()

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
    gr_df = _query_df(conn, "SELECT * FROM guardrail_events ORDER BY id DESC LIMIT 100")
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


# ────────────────────────────────────────────────────────────────────────────
# TAB 4: REGIME & MARKET
# ────────────────────────────────────────────────────────────────────────────

with tab_regime:
    st.header("Regime & Market State")

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

    st.subheader("Regime History")
    reg_df = _query_df(conn, "SELECT * FROM regime_log ORDER BY ts DESC LIMIT 500")
    if not reg_df.empty:
        reg_df["ts"] = pd.to_datetime(reg_df["ts"])
        reg_df = reg_df.sort_values("ts")

        pie_col, timeline_col = st.columns([1, 2])
        with pie_col:
            reg_counts = reg_df["regime"].value_counts().reset_index()
            reg_counts.columns = ["regime", "count"]
            color_map = {r: _regime_color(r) for r in reg_counts["regime"]}
            fig_pie = px.pie(reg_counts, names="regime", values="count",
                              color="regime", color_discrete_map=color_map, title="Regime Distribution")
            fig_pie.update_layout(height=300, margin=dict(l=0, r=0, t=40, b=10))
            st.plotly_chart(fig_pie, use_container_width=True)

        with timeline_col:
            fig_tl = px.scatter(reg_df, x="ts", y="regime", color="regime",
                                 color_discrete_map={r: _regime_color(r) for r in reg_df["regime"].unique()},
                                 title="Regime Timeline", height=300)
            fig_tl.update_traces(marker=dict(size=6))
            fig_tl.update_layout(margin=dict(l=0, r=0, t=40, b=0))
            st.plotly_chart(fig_tl, use_container_width=True)

        if "adx" in reg_df.columns and "bb_width" in reg_df.columns:
            fig_ind = make_subplots(rows=2, cols=1, shared_xaxes=True,
                                     subplot_titles=("ADX (>20 = TREND)", "BB Width (<6% = RANGE)"),
                                     vertical_spacing=0.12)
            fig_ind.add_trace(go.Scatter(x=reg_df["ts"], y=reg_df["adx"], name="ADX",
                                          line=dict(color="#f39c12", width=1.5)), row=1, col=1)
            fig_ind.add_hline(y=20, line_dash="dash", line_color="gray", row=1, col=1)
            fig_ind.add_trace(go.Scatter(x=reg_df["ts"], y=reg_df["bb_width"], name="BB Width",
                                          line=dict(color="#9b59b6", width=1.5)), row=2, col=1)
            fig_ind.add_hline(y=0.06, line_dash="dash", line_color="gray", row=2, col=1)
            fig_ind.update_layout(height=400, margin=dict(l=0, r=0, t=30, b=0), showlegend=False)
            st.plotly_chart(fig_ind, use_container_width=True)
    else:
        st.info("No regime history yet. Run the agent to populate.")


# ────────────────────────────────────────────────────────────────────────────
# TAB 5: BACKTEST RESULTS
# ────────────────────────────────────────────────────────────────────────────

with tab_backtest:
    st.header("Backtest & Walk-Forward Results")

    if gate:
        gate_passed = gate.get("passed", False)
        gate_ts = gate.get("timestamp", "unknown")[:19].replace("T", " ")
        period_start = gate.get("period_start", "?")[:10] if gate.get("period_start") else "?"
        period_end = gate.get("period_end", "?")[:10] if gate.get("period_end") else "?"

        banner_color = "#1a7a4a" if gate_passed else "#7a1a1a"
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
                return "color: green; font-weight: bold" if "PASS" in str(val) else "color: red; font-weight: bold"

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


# ── auto-refresh ─────────────────────────────────────────────────────────────

if auto_refresh:
    import time
    time.sleep(30)
    st.rerun()
