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
LAST_REVIEW_PATH = Path("data/last_review.json")

try:
    from core.llm.eod_reviewer import run_eod_review as _run_eod_review, ReviewReport as _ReviewReport
    _EOD_REVIEWER_AVAILABLE = True
except ImportError:
    _EOD_REVIEWER_AVAILABLE = False

try:
    import anthropic as _anthropic_mod  # noqa: F401 — only used to detect installation
    _ANTHROPIC_INSTALLED = True
except ImportError:
    _ANTHROPIC_INSTALLED = False


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


def _write_config_risk(updates: dict) -> str:
    """Patch the risk section of config/default.yaml in-place. Returns status message."""
    try:
        import yaml
        text = CONFIG_PATH.read_text(encoding="utf-8")
        cfg = yaml.safe_load(text) or {}
        for k, v in updates.items():
            cfg.setdefault("risk", {})[k] = v
        CONFIG_PATH.write_text(yaml.dump(cfg, default_flow_style=False, allow_unicode=True), encoding="utf-8")
        return "Config saved. Restart the agent to apply."
    except Exception as e:
        return f"Save failed: {e}"


def _read_last_review() -> dict:
    """Read data/last_review.json if it exists."""
    if not LAST_REVIEW_PATH.exists():
        return {}
    try:
        return json.loads(LAST_REVIEW_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_last_review(report_dict: dict) -> None:
    """Persist a review report dict to data/last_review.json."""
    try:
        LAST_REVIEW_PATH.parent.mkdir(parents=True, exist_ok=True)
        LAST_REVIEW_PATH.write_text(
            json.dumps(report_dict, indent=2, ensure_ascii=False), encoding="utf-8"
        )
    except Exception as exc:
        logger_dash = __import__("logging").getLogger(__name__)
        logger_dash.warning("Could not save last review: %s", exc)


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
tab_live, tab_pnl, tab_positions, tab_regime, tab_backtest, tab_ai_review, tab_controls, tab_costs = st.tabs([
    "🔴 Live Account",
    "📊 P&L & Equity",
    "📋 Positions & Signals",
    "🌡️ Regime & Market",
    "🔬 Backtest Results",
    "🤖 AI Review",
    "⚙️ Controls",
    "💰 Costs",
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

_REGIME_COLOR_MAP = {
    "TREND": "#2ecc71",
    "RANGE": "#3498db",
    "VOLATILE": "#e67e22",
    "UNKNOWN": "#95a5a6",
}
_REGIME_ORDER = ["TREND", "RANGE", "VOLATILE", "UNKNOWN"]


def _nifty_dma_status(nifty_ltp: float | None) -> dict:
    """Compute Nifty DMA status from the regime_log ADX/VIX history.

    Returns a dict with keys: above_200dma, above_50dma, dma50_rising, ltp.
    Values are True/False/None (None = data unavailable).
    We derive approximate DMA proxies from the regime_log close prices
    stored as nifty_change_pct; if that is absent we return N/A for all.
    """
    result = {
        "ltp": nifty_ltp,
        "above_200dma": None,
        "above_50dma": None,
        "dma50_rising": None,
    }
    if conn is None:
        return result

    # Pull enough rows to compute 200-day MA (need ~200 trading days = ~280 cal days)
    # We use the equity_snapshots table which records equity over time as a proxy
    # for the running Nifty level captured in regime_log nifty_change_pct.
    # A more accurate approach would require persisted Nifty close prices.
    # For now we reconstruct a synthetic Nifty series from the cumulative
    # nifty_change_pct column in regime_log.
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

        # Reconstruct synthetic index starting from 1000 using daily % changes
        df_rl["synth_close"] = 1000.0 * (1 + df_rl["nifty_change_pct"] / 100).cumprod()
        closes = df_rl["synth_close"]

        # Use actual ltp if provided, else last synthetic value
        last_price = nifty_ltp if (nifty_ltp and nifty_ltp > 0) else closes.iloc[-1]

        dma50 = closes.tail(50).mean() if len(closes) >= 50 else closes.mean()
        dma200 = closes.tail(200).mean() if len(closes) >= 200 else closes.mean()

        # For 50-DMA rising: compare current 50-DMA to 10-period-ago 50-DMA
        if len(closes) >= 60:
            dma50_prev = closes.iloc[-60:-10].mean()
            result["dma50_rising"] = bool(dma50 > dma50_prev)
        else:
            result["dma50_rising"] = None

        # Scale DMAs to real LTP if available (synthetic base is arbitrary)
        if nifty_ltp and nifty_ltp > 0 and closes.iloc[-1] > 0:
            scale = nifty_ltp / closes.iloc[-1]
            dma50 *= scale
            dma200 *= scale

        result["above_50dma"] = bool(last_price > dma50)
        result["above_200dma"] = bool(last_price > dma200)
    except Exception:
        pass
    return result


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

    # ── Nifty DMA Status ──────────────────────────────────────────────────────
    st.subheader("Nifty DMA Status")

    _nifty_ltp_raw = snap.get("nifty_ltp", None)
    try:
        _nifty_ltp = float(_nifty_ltp_raw) if _nifty_ltp_raw not in (None, "", "n/a") else None
    except (TypeError, ValueError):
        _nifty_ltp = None

    # PCR from snapshot (Phase 3 data, may not exist yet)
    _pcr_raw = snap.get("pcr", None)
    try:
        _pcr_val = float(_pcr_raw) if _pcr_raw not in (None, "", "n/a") else None
    except (TypeError, ValueError):
        _pcr_val = None

    _dma_status = _nifty_dma_status(_nifty_ltp)

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

    # ── Regime History charts ─────────────────────────────────────────────────
    st.subheader("Regime History")
    reg_df = _query_df(conn, "SELECT ts, regime, adx, bb_width, vix FROM regime_log ORDER BY ts DESC LIMIT 500")
    if not reg_df.empty:
        reg_df["ts"] = pd.to_datetime(reg_df["ts"])
        reg_df = reg_df.sort_values("ts").reset_index(drop=True)

        # ── 1. Color-coded timeline (go.Scatter markers+lines per regime) ────
        st.markdown("**Regime Timeline (last 500 entries)**")
        fig_tl = go.Figure()
        # Assign numeric Y positions for categorical axis
        regime_y = {r: i for i, r in enumerate(_REGIME_ORDER)}
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

        # ── 2. Distribution charts: all-time + last 30 days ─────────────────
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

        # ── 3. Regime transition table ───────────────────────────────────────
        st.subheader("Last 20 Regime Transitions")
        # Detect regime changes
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

        # ── 4. ADX / BB Width indicator subplots ────────────────────────────
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


# ────────────────────────────────────────────────────────────────────────────
# TAB 6: AI REVIEW
# ────────────────────────────────────────────────────────────────────────────

with tab_ai_review:
    st.header("AI End-of-Day Review")
    st.caption(
        "Claude analyses today's trades, signals, guardrail rejections, and regime data "
        "and proposes parameter tweaks. Requires ANTHROPIC_API_KEY in the environment."
    )

    # ── API key check ─────────────────────────────────────────────────────────
    _anthropic_api_key = os.getenv("ANTHROPIC_API_KEY", "")
    _api_key_ok = bool(_anthropic_api_key) and _ANTHROPIC_INSTALLED and _EOD_REVIEWER_AVAILABLE

    if not _ANTHROPIC_INSTALLED:
        st.warning(
            "The `anthropic` Python package is not installed. "
            "Run `pip install anthropic` then restart the dashboard."
        )
    elif not _EOD_REVIEWER_AVAILABLE:
        st.warning("core.llm.eod_reviewer could not be imported. Check the module for errors.")
    elif not _anthropic_api_key:
        st.warning("API key not configured — set ANTHROPIC_API_KEY in your .env file.")

    # ── Auto-review toggle (session state only) ───────────────────────────────
    auto_eod = st.toggle(
        "Auto-review at EOD",
        value=st.session_state.get("auto_eod_review", False),
        disabled=not _api_key_ok,
        help="Session-only setting. When enabled, the review runs automatically when the page refreshes near market close.",
    )
    st.session_state["auto_eod_review"] = auto_eod

    st.divider()

    # ── Run review button ─────────────────────────────────────────────────────
    st.subheader("Run EOD Review Now")
    if st.button(
        "Run EOD Review Now",
        type="primary",
        disabled=not _api_key_ok,
        help="Calls Claude to analyse today's activity and propose parameter changes.",
    ):
        with st.spinner("Running EOD review with Claude... (may take 15–30 seconds)"):
            try:
                _report = _run_eod_review(
                    db_path=str(DB_PATH),
                    snapshot_path=str(SNAPSHOT_PATH),
                    api_key=_anthropic_api_key,
                )
            except Exception as _exc:
                _report = None
                st.error(f"Review failed with exception: {_exc}")

        if _report is None:
            st.error("EOD review returned no result. Check logs for details.")
        else:
            # Persist to disk
            _review_dict = {
                "timestamp": datetime.utcnow().isoformat(),
                "summary": _report.summary,
                "suggestions": [
                    {
                        "strategy": s.strategy,
                        "parameter": s.parameter,
                        "current_value": s.current_value,
                        "suggested_value": s.suggested_value,
                        "rationale": s.rationale,
                    }
                    for s in _report.suggestions
                ],
                "flags": _report.flags,
            }
            _save_last_review(_review_dict)

            # Display results
            st.info(_report.summary)

            if _report.suggestions:
                st.subheader("Parameter Suggestions")
                _sugg_rows = [
                    {
                        "Strategy": s.strategy,
                        "Parameter": s.parameter,
                        "Current": s.current_value,
                        "Suggested": s.suggested_value,
                        "Change": f"{s.current_value} → {s.suggested_value}",
                        "Rationale": s.rationale,
                    }
                    for s in _report.suggestions
                ]
                st.dataframe(
                    pd.DataFrame(_sugg_rows)[["Strategy", "Parameter", "Change", "Rationale"]],
                    use_container_width=True,
                    hide_index=True,
                )
            else:
                st.caption("No parameter suggestions from this review.")

            if _report.flags:
                st.subheader("Flags")
                for _flag in _report.flags:
                    st.warning(_flag)

            st.success("Review complete. Result saved to data/last_review.json")

    st.divider()

    # ── Last review section ───────────────────────────────────────────────────
    st.subheader("Last Review")
    _last = _read_last_review()
    if _last:
        _last_ts = _last.get("timestamp", "unknown")
        try:
            _last_ts_fmt = datetime.fromisoformat(_last_ts).strftime("%Y-%m-%d %H:%M UTC")
        except Exception:
            _last_ts_fmt = _last_ts
        st.caption(f"Reviewed at: {_last_ts_fmt}")

        _last_summary = _last.get("summary", "")
        if _last_summary:
            st.info(_last_summary)

        _last_suggs = _last.get("suggestions", [])
        if _last_suggs:
            st.markdown("**Parameter Suggestions**")
            st.dataframe(
                pd.DataFrame([
                    {
                        "Strategy": s.get("strategy", ""),
                        "Parameter": s.get("parameter", ""),
                        "Change": f"{s.get('current_value', '?')} → {s.get('suggested_value', '?')}",
                        "Rationale": s.get("rationale", ""),
                    }
                    for s in _last_suggs
                ]),
                use_container_width=True,
                hide_index=True,
            )
        else:
            st.caption("No parameter suggestions in last review.")

        _last_flags = _last.get("flags", [])
        if _last_flags:
            st.markdown("**Flags**")
            for _flag in _last_flags:
                st.warning(_flag)
    else:
        st.info("No review has been run yet. Click 'Run EOD Review Now' above.")


# ────────────────────────────────────────────────────────────────────────────
# TAB 7: CONTROLS
# ────────────────────────────────────────────────────────────────────────────

with tab_controls:
    st.header("Agent Controls & Risk Limits")

    # ── Agent start/stop ─────────────────────────────────────────────────────
    st.subheader("Agent Process")
    ctrl_running = _agent_is_running()
    c1, c2 = st.columns([1, 3])
    ctrl_color = "#2ecc71" if ctrl_running else "#e74c3c"
    c1.markdown(
        f"<div style='background:{ctrl_color};padding:12px;border-radius:6px;text-align:center'>"
        f"<b style='color:white'>{'RUNNING' if ctrl_running else 'STOPPED'}</b></div>",
        unsafe_allow_html=True,
    )
    with c2:
        if ctrl_running:
            if st.button("Stop Agent", type="secondary", key="ctrl_stop"):
                st.toast(_stop_agent())
                st.rerun()
        else:
            if st.button("Start Agent", type="primary", key="ctrl_start"):
                st.toast(_start_agent())
                st.rerun()

    st.divider()

    # ── Risk limit controls ───────────────────────────────────────────────────
    st.subheader("Risk Limits")
    st.caption("Changes write to config/default.yaml. Restart the agent to apply them.")

    risk_cfg = config.get("risk", {})
    with st.form("risk_limits_form"):
        r1, r2 = st.columns(2)
        new_per_trade = r1.number_input(
            "Per-trade risk (%)", min_value=0.5, max_value=5.0, step=0.25,
            value=float(risk_cfg.get("per_trade_risk_pct", 1.0)),
            help="Maximum % of equity risked on a single trade (stop-loss distance × qty)",
        )
        new_max_pos = r2.number_input(
            "Max open positions", min_value=1, max_value=20, step=1,
            value=int(risk_cfg.get("max_open_positions", 5)),
            help="Hard cap on concurrent open positions across all strategies",
        )
        new_daily = r1.number_input(
            "Daily loss circuit (%)", min_value=1.0, max_value=10.0, step=0.5,
            value=float(risk_cfg.get("daily_loss_circuit_pct", 3.0)),
            help="Halt new entries for the day when P&L falls below this % of start-of-day equity",
        )
        new_dd = r2.number_input(
            "Drawdown circuit (%)", min_value=5.0, max_value=30.0, step=1.0,
            value=float(risk_cfg.get("drawdown_circuit_pct", 10.0)),
            help="Halt agent and trigger EOD review when peak-to-trough drawdown exceeds this %",
        )
        new_capital = r1.number_input(
            "Initial capital (INR)", min_value=50000, max_value=10000000, step=50000,
            value=int(config.get("capital", {}).get("initial_inr", 500000)),
            help="Starting equity for the paper broker (takes effect on next agent restart)",
        )
        new_max_pos_pct = r2.number_input(
            "Max position size (%)", min_value=5.0, max_value=50.0, step=5.0,
            value=float(risk_cfg.get("max_position_pct", 20.0)),
            help="Cap a single position at this % of total equity",
        )

        if st.form_submit_button("Save Risk Limits", type="primary", use_container_width=True):
            msg = _write_config_risk({
                "per_trade_risk_pct": new_per_trade,
                "max_open_positions": int(new_max_pos),
                "daily_loss_circuit_pct": new_daily,
                "drawdown_circuit_pct": new_dd,
                "max_position_pct": new_max_pos_pct,
            })
            # also patch capital
            try:
                import yaml
                text = CONFIG_PATH.read_text(encoding="utf-8")
                cfg_all = yaml.safe_load(text) or {}
                cfg_all.setdefault("capital", {})["initial_inr"] = int(new_capital)
                CONFIG_PATH.write_text(yaml.dump(cfg_all, default_flow_style=False, allow_unicode=True), encoding="utf-8")
            except Exception:
                pass
            st.success(msg)
            st.cache_data.clear()

    st.divider()

    # ── Multi-agent info (Phase 3 placeholder) ───────────────────────────────
    st.subheader("Multi-Agent Architecture")
    st.info(
        "**Coming soon:** Multiple independent paper-trading agents with different strategy profiles.\n\n"
        "Each agent will run as a separate process with its own capital allocation, strategy set, "
        "and risk limits. Results will be aggregated here for comparison.\n\n"
        "**Planned agent profiles:**\n"
        "- `trend_agent` — TREND strategies only (TrendBreakout, EMA, OBV)\n"
        "- `range_agent` — RANGE strategies only (MeanReversion, BbSqueeze)\n"
        "- `short_agent` — Short-selling strategies (SupertrendShort)\n"
        "- `full_agent` — All strategies (current default)\n\n"
        "Track this on GitHub: https://github.com/noah124098-ux/pypoc"
    )

    st.divider()

    # ── Claude AI overlay (Phase 4 placeholder) ──────────────────────────────
    st.subheader("Claude AI Trade Review")
    st.info(
        "**Coming soon:** Claude Opus reviews every signal before placement.\n\n"
        "Flow:\n"
        "1. Strategy generates signal (e.g. TrendBreakout on TATASTEEL)\n"
        "2. Claude receives signal + last 30-day OHLC + regime context + news summary\n"
        "3. Claude scores confidence (0–1) and can veto the trade\n"
        "4. Signal only proceeds if Claude confidence >= threshold AND guardrails pass\n\n"
        "**Guardrails preserved:** Claude's veto is additional filtering, never bypass of "
        "stop-loss, position sizing, or circuit breakers.\n\n"
        "Enable EOD review in config: `llm.enable_eod_review: true`"
    )

    st.divider()

    # ── Notifications ─────────────────────────────────────────────────────────
    with st.expander("🔔 Notifications", expanded=True):
        st.caption(
            "Credentials are stored in session state only — they are never written to disk "
            "unless you click **Save to .env**."
        )

        # ── Telegram ──────────────────────────────────────────────────────────
        st.markdown("**Telegram**")
        tg_col1, tg_col2 = st.columns(2)
        tg_token = tg_col1.text_input(
            "Bot Token",
            value=st.session_state.get("notif_tg_token", os.getenv("TELEGRAM_BOT_TOKEN", "")),
            type="password",
            key="_notif_tg_token_input",
            help="Create a bot via @BotFather and paste the token here",
        )
        tg_chat_id = tg_col2.text_input(
            "Chat ID",
            value=st.session_state.get("notif_tg_chat_id", os.getenv("TELEGRAM_CHAT_ID", "")),
            key="_notif_tg_chat_id_input",
            help="Your Telegram user/group chat ID (e.g. 123456789)",
        )
        st.session_state["notif_tg_token"] = tg_token
        st.session_state["notif_tg_chat_id"] = tg_chat_id

        if st.button("Test Telegram", key="btn_test_telegram"):
            if not tg_token or not tg_chat_id:
                st.error("Telegram: bot token and chat ID are both required.")
            else:
                try:
                    import urllib.request as _urllib_req
                    import urllib.parse as _urllib_parse
                    _tg_msg = "Test from NSE Agent dashboard"
                    _tg_url = (
                        f"https://api.telegram.org/bot{tg_token}/sendMessage?"
                        + _urllib_parse.urlencode({"chat_id": tg_chat_id, "text": _tg_msg})
                    )
                    with _urllib_req.urlopen(_tg_url, timeout=10) as _tg_resp:
                        _tg_body = json.loads(_tg_resp.read().decode())
                    if _tg_body.get("ok"):
                        st.success("Telegram: test message sent successfully.")
                    else:
                        st.error(f"Telegram API error: {_tg_body.get('description', _tg_body)}")
                except Exception as _tg_exc:
                    st.error(f"Telegram send failed: {_tg_exc}")

        st.divider()

        # ── Email ──────────────────────────────────────────────────────────────
        with st.expander("Email", expanded=False):
            em_col1, em_col2 = st.columns(2)
            smtp_host = em_col1.text_input(
                "SMTP Host",
                value=st.session_state.get("notif_smtp_host", os.getenv("SMTP_HOST", "smtp.gmail.com")),
                key="_notif_smtp_host_input",
            )
            smtp_port = em_col2.number_input(
                "SMTP Port",
                min_value=1, max_value=65535, step=1,
                value=int(st.session_state.get(
                    "notif_smtp_port", int(os.getenv("SMTP_PORT", "587"))
                )),
                key="_notif_smtp_port_input",
            )
            smtp_user = em_col1.text_input(
                "SMTP User",
                value=st.session_state.get("notif_smtp_user", os.getenv("SMTP_USER", "")),
                key="_notif_smtp_user_input",
            )
            smtp_pass = em_col2.text_input(
                "SMTP Password",
                value=st.session_state.get("notif_smtp_pass", os.getenv("SMTP_PASS", "")),
                type="password",
                key="_notif_smtp_pass_input",
            )
            smtp_from = em_col1.text_input(
                "From Address",
                value=st.session_state.get("notif_smtp_from", os.getenv("SMTP_FROM", "")),
                key="_notif_smtp_from_input",
            )
            smtp_to = em_col2.text_input(
                "To Address",
                value=st.session_state.get("notif_smtp_to", os.getenv("SMTP_TO", "")),
                key="_notif_smtp_to_input",
            )
            # Persist to session state
            st.session_state["notif_smtp_host"] = smtp_host
            st.session_state["notif_smtp_port"] = int(smtp_port)
            st.session_state["notif_smtp_user"] = smtp_user
            st.session_state["notif_smtp_pass"] = smtp_pass
            st.session_state["notif_smtp_from"] = smtp_from
            st.session_state["notif_smtp_to"] = smtp_to

            if st.button("Send test email", key="btn_test_email"):
                if not all([smtp_host, smtp_user, smtp_pass, smtp_from, smtp_to]):
                    st.error("Email: all fields except port are required.")
                else:
                    try:
                        import smtplib as _smtplib
                        from email.mime.text import MIMEText as _MIMEText
                        _mail = _MIMEText("Test from NSE Agent dashboard")
                        _mail["Subject"] = "NSE Agent — test notification"
                        _mail["From"] = smtp_from
                        _mail["To"] = smtp_to
                        with _smtplib.SMTP(smtp_host, int(smtp_port), timeout=10) as _srv:
                            _srv.ehlo()
                            _srv.starttls()
                            _srv.login(smtp_user, smtp_pass)
                            _srv.sendmail(smtp_from, [smtp_to], _mail.as_string())
                        st.success("Email: test message sent successfully.")
                    except Exception as _em_exc:
                        st.error(f"Email send failed: {_em_exc}")

        st.divider()

        # ── Save to .env ───────────────────────────────────────────────────────
        st.markdown("**Save notification credentials to .env**")
        st.caption(
            "Only TELEGRAM and SMTP keys are written — Angel One / Anthropic keys "
            "in the existing .env are never touched."
        )

        _env_path = Path(".env")

        if st.button("Save to .env", key="btn_save_notif_env"):
            _notif_keys = {
                "TELEGRAM_BOT_TOKEN": st.session_state.get("notif_tg_token", ""),
                "TELEGRAM_CHAT_ID": st.session_state.get("notif_tg_chat_id", ""),
                "SMTP_HOST": st.session_state.get("notif_smtp_host", ""),
                "SMTP_PORT": str(st.session_state.get("notif_smtp_port", 587)),
                "SMTP_USER": st.session_state.get("notif_smtp_user", ""),
                "SMTP_PASS": st.session_state.get("notif_smtp_pass", ""),
                "SMTP_FROM": st.session_state.get("notif_smtp_from", ""),
                "SMTP_TO": st.session_state.get("notif_smtp_to", ""),
            }

            _allowed_notif_prefixes = (
                "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID",
                "SMTP_HOST", "SMTP_PORT", "SMTP_USER", "SMTP_PASS", "SMTP_FROM", "SMTP_TO",
            )

            _create_confirmed = st.session_state.get("_notif_env_create_confirmed", False)

            if not _env_path.exists() and not _create_confirmed:
                st.warning(
                    ".env file does not exist. Click **Save to .env** again to confirm creation."
                )
                st.session_state["_notif_env_create_confirmed"] = True
            else:
                try:
                    # Read existing lines, keeping non-notification keys untouched
                    _existing_lines: list[str] = []
                    if _env_path.exists():
                        _existing_lines = _env_path.read_text(encoding="utf-8").splitlines()

                    # Safety: never touch Angel One / Anthropic lines, only strip notif keys
                    _safe_existing: list[str] = []
                    for _env_line in _existing_lines:
                        _env_stripped = _env_line.strip()
                        _is_notif_line = any(
                            _env_stripped.startswith(f"{_nk}=")
                            or _env_stripped.startswith(f"{_nk} =")
                            for _nk in _allowed_notif_prefixes
                        )
                        if not _is_notif_line:
                            _safe_existing.append(_env_line)

                    # Append notification keys
                    _new_lines = list(_safe_existing)
                    if _new_lines and _new_lines[-1].strip() != "":
                        _new_lines.append("")  # blank separator

                    _new_lines.append("# --- Notification settings (managed by dashboard) ---")
                    for _nk, _nv in _notif_keys.items():
                        if _nv:  # only write non-empty values
                            _new_lines.append(f"{_nk}={_nv}")

                    _env_path.write_text("\n".join(_new_lines) + "\n", encoding="utf-8")
                    st.session_state.pop("_notif_env_create_confirmed", None)
                    st.success(".env updated with notification settings.")
                except Exception as _env_exc:
                    st.error(f"Failed to write .env: {_env_exc}")


# ────────────────────────────────────────────────────────────────────────────
# TAB 8: COSTS
# ────────────────────────────────────────────────────────────────────────────

with tab_costs:
    st.header("Trade Costs & Charge Breakdown")

    # Load execution config for default charge parameters
    exec_cfg = config.get("execution", {})
    _brokerage_per_order = float(exec_cfg.get("brokerage_per_order_inr", 20.0))
    _stt_pct = float(exec_cfg.get("stt_pct", 0.025))
    _exchange_txn_pct = float(exec_cfg.get("exchange_txn_pct", 0.00345))
    _gst_pct = float(exec_cfg.get("gst_pct", 18.0))

    # ── Load all trades ───────────────────────────────────────────────────────
    costs_trades_df = _query_df(conn, "SELECT * FROM trades ORDER BY closed_at DESC")

    if not costs_trades_df.empty and "charges" in costs_trades_df.columns:
        total_charges_alltime = float(costs_trades_df["charges"].sum())
        total_gross_pnl = float(costs_trades_df["pnl"].sum()) if "pnl" in costs_trades_df.columns else 0.0
        total_net_pnl = total_gross_pnl - total_charges_alltime
        avg_charge_per_trade = total_charges_alltime / len(costs_trades_df) if len(costs_trades_df) else 0.0
        charges_pct_of_gross = (total_charges_alltime / abs(total_gross_pnl) * 100
                                if total_gross_pnl != 0 else 0.0)

        # Today's charges
        costs_trades_df["closed_at_dt"] = pd.to_datetime(costs_trades_df["closed_at"], errors="coerce")
        today_str = datetime.utcnow().date()
        today_df = costs_trades_df[costs_trades_df["closed_at_dt"].dt.date == today_str]
        today_charges = float(today_df["charges"].sum()) if not today_df.empty else 0.0

        # ── Top metrics ───────────────────────────────────────────────────────
        mc1, mc2, mc3, mc4 = st.columns(4)
        mc1.metric("Total Charges (all-time)", f"₹{total_charges_alltime:,.0f}")
        mc2.metric("Charges Today", f"₹{today_charges:,.0f}")
        mc3.metric("Avg Charge / Trade", f"₹{avg_charge_per_trade:,.0f}")
        mc4.metric("Charges as % of Gross P&L", f"{charges_pct_of_gross:.1f}%")

        st.markdown(
            f"**Net P&L = Gross ₹{total_gross_pnl:,.0f} − ₹{total_charges_alltime:,.0f} charges = "
            f"₹{total_net_pnl:,.0f}**"
        )

        st.divider()

        # ── Charges per strategy (bar chart) ──────────────────────────────────
        if "strategy" in costs_trades_df.columns:
            st.subheader("Charges by Strategy")
            strat_costs = (
                costs_trades_df.groupby("strategy")
                .agg(total_charges=("charges", "sum"), trades=("charges", "count"),
                     gross_pnl=("pnl", "sum"))
                .reset_index()
            )
            strat_costs["net_pnl"] = strat_costs["gross_pnl"] - strat_costs["total_charges"]
            strat_costs["avg_charge"] = strat_costs["total_charges"] / strat_costs["trades"]

            fig_strat = go.Figure()
            fig_strat.add_trace(go.Bar(
                x=strat_costs["strategy"], y=strat_costs["total_charges"],
                name="Total Charges", marker_color="#e67e22",
                text=strat_costs["total_charges"].apply(lambda v: f"₹{v:,.0f}"),
                textposition="outside",
            ))
            fig_strat.update_layout(
                height=320, margin=dict(l=0, r=0, t=20, b=0),
                yaxis_title="Charges (INR)", xaxis_title="Strategy",
            )
            st.plotly_chart(fig_strat, use_container_width=True)

            st.dataframe(
                strat_costs.rename(columns={
                    "strategy": "Strategy", "trades": "Trades",
                    "total_charges": "Total Charges (₹)", "gross_pnl": "Gross P&L (₹)",
                    "net_pnl": "Net P&L (₹)", "avg_charge": "Avg Charge/Trade (₹)",
                }).style.format({
                    "Total Charges (₹)": "₹{:,.0f}",
                    "Gross P&L (₹)": "₹{:,.0f}",
                    "Net P&L (₹)": "₹{:,.0f}",
                    "Avg Charge/Trade (₹)": "₹{:,.0f}",
                }).applymap(_color_pnl, subset=["Net P&L (₹)", "Gross P&L (₹)"]),
                use_container_width=True, hide_index=True,
            )

        st.divider()

        # ── Charge breakdown pie chart (estimated from config rates) ──────────
        st.subheader("Estimated Charge Component Breakdown")
        st.caption(
            "Breakdown is estimated from config rates applied to average trade size. "
            "Exact per-component values are not stored individually in the database."
        )

        if len(costs_trades_df) > 0 and "qty" in costs_trades_df.columns and "exit_price" in costs_trades_df.columns:
            from core.broker.charges import compute_charges as _compute_charges
            from core.types import Side

            sample = costs_trades_df[["qty", "exit_price"]].dropna()
            if not sample.empty:
                avg_qty = int(sample["qty"].median())
                avg_price = float(sample["exit_price"].median())
                avg_qty = max(avg_qty, 1)
                avg_price = max(avg_price, 1.0)

                bd = _compute_charges(
                    side=Side.SELL,
                    qty=avg_qty,
                    price=avg_price,
                    brokerage_per_order_inr=_brokerage_per_order,
                    stt_pct=_stt_pct,
                    exchange_txn_pct=_exchange_txn_pct,
                    gst_pct=_gst_pct,
                )
                component_total = bd.total if bd.total > 0 else 1.0
                pie_labels = ["Brokerage", "STT", "Exchange Txn", "GST", "SEBI", "Stamp Duty", "Clearing"]
                pie_values_raw = [
                    bd.brokerage, bd.stt, bd.exchange_txn, bd.gst,
                    bd.sebi, bd.stamp_duty, bd.clearing,
                ]
                # Scale to estimated all-time charges
                scale = total_charges_alltime / component_total if component_total else 1.0
                pie_values = [v * scale for v in pie_values_raw]

                fig_pie = px.pie(
                    names=pie_labels, values=pie_values,
                    title=f"Estimated breakdown of ₹{total_charges_alltime:,.0f} total charges",
                    color_discrete_sequence=px.colors.qualitative.Set2,
                )
                fig_pie.update_layout(height=380, margin=dict(l=0, r=0, t=50, b=10))
                st.plotly_chart(fig_pie, use_container_width=True)

                # Summary table
                breakdown_df = pd.DataFrame({
                    "Component": pie_labels,
                    "Estimated Amount (₹)": [f"₹{v:,.0f}" for v in pie_values],
                    "% of Total": [f"{v / total_charges_alltime * 100:.1f}%" if total_charges_alltime else "—"
                                   for v in pie_values],
                })
                st.dataframe(breakdown_df, use_container_width=True, hide_index=True)
            else:
                st.info("Not enough trade data to estimate charge breakdown.")
        else:
            st.info("Not enough trade data for charge breakdown. Need qty and exit_price columns.")

        st.divider()

        # ── Recent 20 trades with charges ─────────────────────────────────────
        st.subheader("Recent 20 Trades — Per-Trade Charges")
        recent_cols = [c for c in
            ["closed_at", "symbol", "strategy", "side", "qty", "entry_price", "exit_price",
             "pnl", "charges", "exit_reason"]
            if c in costs_trades_df.columns]
        recent_20 = costs_trades_df[recent_cols].head(20)
        if "pnl" in recent_20.columns and "charges" in recent_20.columns:
            recent_20 = recent_20.copy()
            recent_20["net_pnl"] = recent_20["pnl"] - recent_20["charges"]
            pnl_cols_20 = [c for c in ["pnl", "net_pnl"] if c in recent_20.columns]
            fmt_map = {c: "₹{:.2f}" for c in ["entry_price", "exit_price", "pnl", "charges", "net_pnl"]
                       if c in recent_20.columns}
            styled_20 = recent_20.style.applymap(_color_pnl, subset=pnl_cols_20).format(fmt_map)
            st.dataframe(styled_20, use_container_width=True, height=350)
        else:
            st.dataframe(recent_20, use_container_width=True, height=350)

    else:
        st.info("No closed trades in the database yet. Run the agent to record trades.")

    st.divider()

    # ── Estimated exit charges for current open positions ─────────────────────
    st.subheader("Estimated Exit Charges — Open Positions")
    open_positions = snap.get("open_positions", [])
    if open_positions:
        from core.broker.charges import compute_charges as _compute_charges_pos
        from core.types import Side as _Side

        pos_charge_rows = []
        total_est_charges = 0.0
        for pos in open_positions:
            try:
                qty = int(pos.get("qty", 0))
                last_price = float(pos.get("last_price") or pos.get("avg_price", 0))
                if qty <= 0 or last_price <= 0:
                    continue
                bd = _compute_charges_pos(
                    side=_Side.SELL,
                    qty=qty,
                    price=last_price,
                    brokerage_per_order_inr=_brokerage_per_order,
                    stt_pct=_stt_pct,
                    exchange_txn_pct=_exchange_txn_pct,
                    gst_pct=_gst_pct,
                )
                total_est_charges += bd.total
                pos_charge_rows.append({
                    "Symbol": pos.get("symbol", "?"),
                    "Strategy": pos.get("strategy", "?"),
                    "Qty": qty,
                    "Last Price (₹)": last_price,
                    "Turnover (₹)": qty * last_price,
                    "Est. Exit Charges (₹)": bd.total,
                    "Brokerage": bd.brokerage,
                    "STT": bd.stt,
                    "Exchange Txn": bd.exchange_txn,
                    "GST": bd.gst,
                })
            except Exception:
                continue

        if pos_charge_rows:
            pos_charges_df = pd.DataFrame(pos_charge_rows)
            fmt_pos = {c: "₹{:,.2f}" for c in pos_charges_df.columns if c not in ("Symbol", "Strategy", "Qty")}
            st.dataframe(
                pos_charges_df.style.format(fmt_pos),
                use_container_width=True, hide_index=True,
            )
            st.metric(
                "Total estimated exit charges if all positions closed now",
                f"₹{total_est_charges:,.0f}",
            )
        else:
            st.info("Could not compute charges — check position data has qty and last_price.")
    else:
        st.info("No open positions to estimate exit charges for.")


# ── auto-refresh ─────────────────────────────────────────────────────────────

if auto_refresh:
    import time
    time.sleep(30)
    st.rerun()
