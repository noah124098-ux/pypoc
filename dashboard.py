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

try:
    from core.llm.news_scorer import score_news
    _NEWS_SCORER_AVAILABLE = True
except ImportError:
    _NEWS_SCORER_AVAILABLE = False

try:
    from core.data.angelone_portfolio import fetch_live_portfolio as _fetch_live_portfolio, AccountSummary as _AccountSummary
    _AO_PORTFOLIO_AVAILABLE = True
except ImportError:
    _AO_PORTFOLIO_AVAILABLE = False
    _fetch_live_portfolio = None  # type: ignore[assignment]
    _AccountSummary = None  # type: ignore[assignment]

try:
    from core.data.nse_fii_dii import get_fii_dii_flows, get_institutional_sentiment
    _FII_AVAILABLE = True
except ImportError:
    _FII_AVAILABLE = False

try:
    from core.data.nse_pcr import get_nifty_pcr as _get_nifty_pcr
    _PCR_AVAILABLE = True
except ImportError:
    _PCR_AVAILABLE = False
    _get_nifty_pcr = None  # type: ignore[assignment]

try:
    from core.data.nse_vix import get_india_vix as _get_india_vix
    _VIX_MODULE_AVAILABLE = True
except ImportError:
    _VIX_MODULE_AVAILABLE = False
    _get_india_vix = None  # type: ignore[assignment]


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


def _time_ago(ts_str: str) -> str:
    """Return a human-readable 'X ago' string from an ISO timestamp."""
    if not ts_str:
        return "unknown"
    try:
        opened = datetime.fromisoformat(str(ts_str).replace("Z", "+00:00").replace("+00:00", ""))
        delta = datetime.utcnow() - opened
        total_secs = int(delta.total_seconds())
        if total_secs < 60:
            return f"{total_secs}s ago"
        if total_secs < 3600:
            return f"{total_secs // 60}m ago"
        if total_secs < 86400:
            return f"{total_secs // 3600}h {(total_secs % 3600) // 60}m ago"
        return f"{total_secs // 86400}d ago"
    except Exception:
        return str(ts_str)[:16]


def _fmt_inr(value: float) -> str:
    """Format a number in Indian number system (lakhs/crores) with ₹ prefix.

    Examples:  500000 → ₹5,00,000   1234567 → ₹12,34,567   999 → ₹999
    """
    try:
        val = int(round(abs(value)))
        s = str(val)
        if len(s) <= 3:
            formatted = s
        elif len(s) <= 5:
            formatted = s[:-3] + "," + s[-3:]
        else:
            # First group of 3 from right, then groups of 2
            last3 = s[-3:]
            rest = s[:-3]
            groups = []
            while len(rest) > 2:
                groups.append(rest[-2:])
                rest = rest[:-2]
            groups.append(rest)
            groups.reverse()
            formatted = ",".join(groups) + "," + last3
        sign = "-" if value < 0 else ""
        return f"₹{sign}{formatted}"
    except Exception:
        return f"₹{value:,.0f}"


def _is_market_hours() -> bool:
    """Return True if current time (IST) is within NSE market hours (09:15–15:30 Mon–Fri)."""
    try:
        now_utc = datetime.utcnow()
        # IST = UTC + 5:30
        now_ist = now_utc + timedelta(hours=5, minutes=30)
        if now_ist.weekday() >= 5:  # Saturday=5, Sunday=6
            return False
        market_open = now_ist.replace(hour=9, minute=15, second=0, microsecond=0)
        market_close = now_ist.replace(hour=15, minute=30, second=0, microsecond=0)
        return market_open <= now_ist <= market_close
    except Exception:
        return False


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

    st.sidebar.divider()
    st.sidebar.subheader("Market Pulse")

    # VIX
    try:
        vix_val = snap.get("vix", 0) if snap else 0
        if vix_val and vix_val > 0:
            vix_color = "🟢" if vix_val < 15 else ("🟡" if vix_val < 20 else "🔴")
            st.sidebar.metric(
                f"{vix_color} India VIX",
                f"{vix_val:.1f}",
                help="<15 calm, 15-20 normal, >20 volatile",
            )
    except Exception:
        pass

    # PCR
    try:
        if _PCR_AVAILABLE and _get_nifty_pcr is not None:
            pcr = _get_nifty_pcr()
            if pcr:
                pcr_color = "🟢" if pcr > 1.0 else ("🔴" if pcr < 0.7 else "🟡")
                st.sidebar.metric(
                    f"{pcr_color} PCR",
                    f"{pcr:.2f}",
                    help="Put-Call Ratio. >1.0 bullish, <0.7 bearish",
                )
    except Exception:
        pass

    # FII sentiment
    try:
        if _FII_AVAILABLE:
            sent = get_institutional_sentiment()
            if sent:
                sent_display = {"BULLISH": "🟢 FII Buy", "BEARISH": "🔴 FII Sell"}.get(sent, "⚪ Neutral")
                st.sidebar.metric("FII Sentiment", sent_display)
    except Exception:
        pass

    st.divider()
    if st.button("Refresh now"):
        st.cache_data.clear()
        st.rerun()


# ── tabs ─────────────────────────────────────────────────────────────────────

conn = _db_connect()
tab_live, tab_portfolio, tab_pnl, tab_positions, tab_regime, tab_backtest, tab_ai_review, tab_controls, tab_costs = st.tabs([
    "🔴 Live Account",
    "📊 Live Account",
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

    # ── KPI rows ─────────────────────────────────────────────────────────────
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
    _kpi_trades_df = _query_df(
        conn,
        "SELECT pnl FROM trades ORDER BY closed_at DESC LIMIT 30",
    )
    if not _kpi_trades_df.empty and "pnl" in _kpi_trades_df.columns:
        _kpi_n_trades_30 = len(_kpi_trades_df)
        _kpi_wins_30 = int((_kpi_trades_df["pnl"] > 0).sum())
        _kpi_win_rate = _kpi_wins_30 / _kpi_n_trades_30 * 100 if _kpi_n_trades_30 else 0.0
        # Trend arrow: compare win rate of last 10 vs trades 11–30
        _kpi_win_recent = int((_kpi_trades_df.head(10)["pnl"] > 0).sum())
        _kpi_win_older = int((_kpi_trades_df.tail(20)["pnl"] > 0).sum())
        _kpi_win_trend = "↑" if (_kpi_win_recent / 10) > (_kpi_win_older / max(_kpi_n_trades_30 - 10, 1)) else "↓"
    else:
        _kpi_n_trades_30 = 0
        _kpi_win_rate = 0.0
        _kpi_win_trend = "—"

    # Trades today count
    _kpi_trades_today_df = _query_df(
        conn,
        "SELECT id FROM trades WHERE date(closed_at) = date('now')",
    )
    _kpi_trades_today = len(_kpi_trades_today_df)

    # Last signal time
    _kpi_last_sig_df = _query_df(conn, "SELECT ts FROM signals ORDER BY id DESC LIMIT 1")
    if not _kpi_last_sig_df.empty and "ts" in _kpi_last_sig_df.columns:
        _kpi_last_sig_str = str(_kpi_last_sig_df["ts"].iloc[0])
        _kpi_last_sig_ago = _time_ago(_kpi_last_sig_str)
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

    # ── Row 1: 6-column primary KPI row ──────────────────────────────────────
    _kr1, _kr2, _kr3, _kr4, _kr5, _kr6 = st.columns(6)

    # col1: Equity with delta vs yesterday start
    _kpi_equity_delta = f"{_kpi_equity - float(capital or _kpi_equity):+,.0f} total" if capital else None
    _kr1.metric(
        "Equity",
        _fmt_inr(_kpi_equity),
        delta=_kpi_equity_delta,
        help="Current paper-agent equity from snapshot.json",
    )

    # col2: Today P&L ₹ + %
    _kr2.metric(
        "Today P&L",
        _fmt_inr(_kpi_day_pnl),
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

    # ── Row 2: 4-column secondary KPI row ────────────────────────────────────
    _ks1, _ks2, _ks3, _ks4 = st.columns(4)

    # col1: Peak Equity
    _ks1.metric(
        "Peak Equity",
        _fmt_inr(_kpi_peak),
        help="All-time high equity recorded in snapshot",
    )

    # col2: Max Drawdown % from peak — red if >5%
    _ks2.metric(
        "Drawdown from Peak",
        f"-{_kpi_dd_pct:.2f}%",
        delta=f"peak {_fmt_inr(_kpi_peak)}",
        delta_color="off",
        help="Current drawdown from all-time peak equity. Red if >5%.",
    )
    if _kpi_dd_pct > 5.0:
        _ks2.markdown(
            f"<small style='color:#e74c3c;font-weight:600'>⚠ Drawdown >5%</small>",
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

    # ── Auto-refresh countdown during market hours ────────────────────────────
    _in_market = _is_market_hours()
    if _in_market:
        # Use a session counter to implement countdown without blocking
        _refresh_key = "live_tab_refresh_counter"
        _count = st.session_state.get(_refresh_key, 30)
        _countdown_placeholder = st.empty()
        _countdown_placeholder.caption(f"Auto-refreshing in {_count}s (market hours active)")
        if _count <= 1:
            st.session_state[_refresh_key] = 30
            import time as _time_mod
            _time_mod.sleep(0.5)
            st.rerun()
        else:
            st.session_state[_refresh_key] = _count - 1
            import time as _time_mod
            _time_mod.sleep(1)
            st.rerun()
    else:
        _now_utc = datetime.utcnow()
        _now_ist = _now_utc + timedelta(hours=5, minutes=30)
        st.caption(
            f"Market closed — auto-refresh paused. "
            f"Current IST: {_now_ist.strftime('%H:%M:%S')} | "
            f"Market hours: Mon–Fri 09:15–15:30 IST"
        )

    st.divider()

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

    st.divider()

    # ── Market Intelligence: FII/DII Flows ───────────────────────────────────
    with st.expander("📊 Institutional Flows (FII/DII)", expanded=False):
        if _FII_AVAILABLE:
            flows = get_fii_dii_flows(days=5)
            sentiment = get_institutional_sentiment()

            # Sentiment badge
            if sentiment == "BULLISH":
                st.success("🟢 FII BULLISH — Institutions are net buyers (avg >₹500cr/day)")
            elif sentiment == "BEARISH":
                st.error("🔴 FII BEARISH — Institutions are net sellers (avg <-₹500cr/day)")
                st.warning("⚠️ TREND BUY signals suppressed by institutional sentiment gate")
            else:
                st.info("⚪ FII NEUTRAL — Mixed institutional flows")

            # Flows table
            if flows:
                df = pd.DataFrame([{
                    "Date": f.date, "FII Net (₹Cr)": f"{f.fii_net_inr_cr:+,.0f}",
                    "DII Net (₹Cr)": f"{f.dii_net_inr_cr:+,.0f}",
                    "Combined (₹Cr)": f"{f.combined_net:+,.0f}"
                } for f in flows])
                st.dataframe(df, hide_index=True, use_container_width=True)
            else:
                st.caption("No flow data available (market may be closed)")
        else:
            st.caption("FII/DII module not available")


# ────────────────────────────────────────────────────────────────────────────
# TAB 2: LIVE PORTFOLIO (Angel One real account — structured view)
# ────────────────────────────────────────────────────────────────────────────

with tab_portfolio:
    st.header("Live Account Portfolio")

    # Read-only badge
    st.markdown(
        "<span style='background:#e67e22;color:white;padding:4px 12px;"
        "border-radius:4px;font-weight:bold;font-size:0.85em'>"
        "⚠️ Read-Only — Orders are placed by the paper agent only</span>",
        unsafe_allow_html=True,
    )
    st.caption("This view is read-only. No orders are placed from this tab.")

    st.divider()

    # Credential check
    _port_api_key, _port_client_code, _port_password, _port_totp = _load_env_creds()
    _port_creds_ok = all([_port_api_key, _port_client_code, _port_password, _port_totp])

    if not _port_creds_ok:
        st.info(
            "Connect your Angel One account in the **Controls tab → Angel One Credentials** "
            "(or in the 🔴 Live Account tab above)."
        )
    else:
        # Refresh controls
        _port_col1, _port_col2 = st.columns([1, 4])
        _port_do_refresh = _port_col1.button("Refresh", key="portfolio_refresh")
        _port_auto_refresh = _port_col2.checkbox(
            "Auto-refresh every 60s", value=False, key="portfolio_auto_refresh"
        )

        # Session state cache key — invalidated on manual refresh
        _port_cache_key = "ao_portfolio_cache"
        if _port_do_refresh:
            st.session_state.pop(_port_cache_key, None)
            st.cache_data.clear()

        # Fetch or use cached result
        if _AO_PORTFOLIO_AVAILABLE and _fetch_live_portfolio is not None:
            _port_summary = st.session_state.get(_port_cache_key)
            if _port_summary is None:
                with st.spinner("Fetching live portfolio..."):
                    try:
                        _port_summary = _fetch_live_portfolio(
                            api_key=_port_api_key,
                            client_code=_port_client_code,
                            password=_port_password,
                            totp_secret=_port_totp,
                        )
                    except Exception as _port_exc:
                        _port_summary = None
                        st.warning(f"Could not fetch live portfolio: {_port_exc}")
                st.session_state[_port_cache_key] = _port_summary
        else:
            _port_summary = None
            st.warning(
                "core.data.angelone_portfolio could not be imported. "
                "Ensure SmartApi and pyotp are installed: `pip install smartapi-python pyotp`"
            )

        if _port_summary is not None and getattr(_port_summary, "net_value", None) is not None:
            # ── Top metrics row ───────────────────────────────────────────────
            st.subheader("Account Summary")
            _pm1, _pm2, _pm3, _pm4 = st.columns(4)
            _pm1.metric("Net Value", f"₹{_port_summary.net_value:,.0f}")
            _pm2.metric("Available Cash", f"₹{_port_summary.available_cash:,.0f}")
            _pm3.metric("Used Margin", f"₹{_port_summary.used_margin:,.0f}")

            _pnl_today = _port_summary.total_pnl_today
            _pnl_color = "normal" if _pnl_today >= 0 else "inverse"
            _pm4.metric(
                "Today's P&L",
                f"₹{_pnl_today:,.0f}",
                delta=f"{'▲' if _pnl_today >= 0 else '▼'} {abs(_pnl_today):,.0f}",
                delta_color=_pnl_color,
            )

            st.divider()

            # ── Positions table ───────────────────────────────────────────────
            _pos_list = _port_summary.positions if _port_summary.positions else []
            st.subheader(f"Open Positions ({len(_pos_list)})")
            if _pos_list:
                _pos_rows = [
                    {
                        "Symbol": p.symbol,
                        "Qty": p.qty,
                        "Avg Price": p.avg_price,
                        "LTP": p.ltp,
                        "P&L": p.pnl,
                        "Day Change %": p.day_change_pct,
                    }
                    for p in _pos_list
                ]
                _pos_df = pd.DataFrame(_pos_rows)

                def _pnl_col_style(val):
                    try:
                        v = float(val)
                    except (TypeError, ValueError):
                        return ""
                    return "color: green; font-weight: bold" if v > 0 else (
                        "color: red; font-weight: bold" if v < 0 else "color: gray"
                    )

                _pos_styled = (
                    _pos_df.style
                    .applymap(_pnl_col_style, subset=["P&L"])
                    .format({
                        "Avg Price": "₹{:.2f}",
                        "LTP": "₹{:.2f}",
                        "P&L": "₹{:.2f}",
                        "Day Change %": "{:.2f}%",
                    })
                )
                st.dataframe(_pos_styled, use_container_width=True)
            else:
                st.info("No open positions in the live Angel One account.")

            st.divider()

            # ── vs Paper Agent comparison ─────────────────────────────────────
            st.subheader("vs Paper Agent")
            _snap_equity = float(snap.get("equity", 0.0))
            _live_net = _port_summary.net_value
            if _snap_equity > 0 and _live_net > 0:
                _diff = _live_net - _snap_equity
                _diff_pct = _diff / _snap_equity * 100
                _cmp1, _cmp2, _cmp3 = st.columns(3)
                _cmp1.metric("Paper Agent Equity", f"₹{_snap_equity:,.0f}")
                _cmp2.metric("Live Account Net Value", f"₹{_live_net:,.0f}")
                _diff_color = "normal" if _diff >= 0 else "inverse"
                _cmp3.metric(
                    "Difference (Live − Paper)",
                    f"₹{_diff:,.0f}",
                    delta=f"{_diff_pct:+.2f}%",
                    delta_color=_diff_color,
                )
                st.caption(
                    "Paper agent equity comes from data/snapshot.json. "
                    "Live account net value comes from Angel One RMS in real time."
                )
            else:
                st.info(
                    "Paper agent equity or live account value unavailable for comparison. "
                    "Run the paper agent to populate snapshot.json."
                )

        elif _port_summary is not None:
            st.warning("Could not fetch live portfolio. Check credentials.")

        # Auto-refresh via session flag (60s rerun)
        if _port_auto_refresh:
            import time as _time
            _time.sleep(60)
            st.rerun()


# ────────────────────────────────────────────────────────────────────────────
# TAB 3: P&L & EQUITY
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

    # ── Equity curve ─────────────────────────────────────────────────────────
    eq_df = _query_df(conn, "SELECT ts, equity FROM equity_snapshots ORDER BY ts")

    # Query trades for entry/exit markers
    _trades_for_markers = _query_df(
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

        # CAGR: use timestamp span
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

        # ── Summary metrics above chart ───────────────────────────────────────
        st.subheader("Equity Curve — Paper Agent")
        _ret_color = "green" if _total_return_pct >= 0 else "red"
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

        # ── Build figure: equity + drawdown panel ─────────────────────────────
        _fig_eq = make_subplots(
            rows=2,
            cols=1,
            shared_xaxes=True,
            row_heights=[0.72, 0.28],
            vertical_spacing=0.06,
            specs=[[{"secondary_y": True}], [{"secondary_y": False}]],
        )

        # Equity curve (green line + light fill)
        _fig_eq.add_trace(
            go.Scatter(
                x=eq_df["ts"],
                y=eq_df["equity"],
                mode="lines",
                name="Equity",
                line=dict(color="#2ecc71", width=2.5),
                fill="tozeroy",
                fillcolor="rgba(46,204,113,0.08)",
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

        # Nifty benchmark reference line (constant at starting equity).
        # Only drawn when nifty_ltp is available in snapshot as a visual anchor.
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
                    # Map each trade timestamp to nearest equity value
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

                    # Entry markers -- green triangles up
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
                                    color="#27ae60",
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

                    # Exit markers -- red triangles down
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
                                    color="#e74c3c",
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

        # Drawdown panel (red fill, negative direction)
        _fig_eq.add_trace(
            go.Scatter(
                x=eq_df["ts"],
                y=-eq_df["dd_pct"],
                mode="lines",
                name="Drawdown %",
                line=dict(color="#e74c3c", width=1.5),
                fill="tozeroy",
                fillcolor="rgba(231,76,60,0.18)",
                hovertemplate="<b>%{x|%Y-%m-%d %H:%M}</b><br>Drawdown: %{y:.2f}%<extra></extra>",
            ),
            row=2,
            col=1,
        )

        # Axis and layout
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
        # Re-sort descending for display (most recent first)
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
                return ["background-color: rgba(46,204,113,0.15)"] * len(row)
            if val < 0:
                return ["background-color: rgba(231,76,60,0.15)"] * len(row)
            return [""] * len(row)

        _display_slice = _sorted_trades[display_cols].head(100)
        styled = _display_slice.style.apply(_row_bg, axis=1)
        styled = styled.applymap(_color_pnl, subset=[c for c in pnl_cols if c in _display_slice.columns])
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

    # ── Halt banner ───────────────────────────────────────────────────────────
    _pos_halted = snap.get("halted", False)
    _pos_halt_reason = snap.get("halt_reason", "")
    if _pos_halted:
        st.markdown(
            f"<div style='background:#c0392b;color:white;padding:12px 16px;border-radius:8px;"
            f"font-weight:bold;font-size:1.05em;margin-bottom:12px'>"
            f"AGENT HALTED: {_pos_halt_reason or 'No reason recorded'}</div>",
            unsafe_allow_html=True,
        )

    # ── Summary metrics row ───────────────────────────────────────────────────
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

            # Derived metrics
            _pnl_pct = (_unr / (_avg_price * _qty) * 100) if (_avg_price * _qty) != 0 else 0.0
            _rr_ratio = (
                abs(_target - _avg_price) / abs(_avg_price - _stop)
                if _stop and _target and abs(_avg_price - _stop) > 0
                else None
            )
            _portfolio_pct = (_pos_value / _current_equity * 100) if _current_equity else 0.0

            # Progress bar: price position between stop and target
            _progress_val: float | None = None
            if _stop and _target and _stop != _target:
                _progress_val = (_last_price - _stop) / (_target - _stop)
                _progress_val = max(0.0, min(1.0, _progress_val))

            _side_color = "#1a7a4a" if _side == "BUY" else "#7a1a1a"
            _pnl_color = "#2ecc71" if _unr >= 0 else "#e74c3c"
            _strat_color = _STRATEGY_COLORS.get(_strategy.lower(), "#555")

            # Card container
            with st.container():
                st.markdown(
                    f"<div style='border:1px solid #333;border-radius:10px;padding:14px 18px;"
                    f"margin-bottom:14px;background:#1a1a1a'>",
                    unsafe_allow_html=True,
                )

                # Header row: Symbol + Strategy badge + Side badge
                _hc1, _hc2, _hc3 = st.columns([3, 2, 1])
                _hc1.markdown(
                    f"<span style='font-size:1.3em;font-weight:700;color:#f0f0f0'>{_sym}</span>",
                    unsafe_allow_html=True,
                )
                _hc2.markdown(
                    f"<span style='background:{_strat_color};color:white;padding:3px 10px;"
                    f"border-radius:12px;font-size:0.8em;font-weight:600'>{_strategy}</span>",
                    unsafe_allow_html=True,
                )
                _hc3.markdown(
                    f"<span style='background:{_side_color};color:white;padding:3px 10px;"
                    f"border-radius:12px;font-size:0.8em;font-weight:700'>{_side}</span>",
                    unsafe_allow_html=True,
                )

                # Row 1: Entry | Current | P&L ₹ | P&L %
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

                # Row 2: Stop Loss | Target | Risk:Reward
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

                # Row 3: Qty | Position Value | % of Portfolio | Opened
                _r3c1, _r3c2, _r3c3, _r3c4 = st.columns(4)
                _r3c1.metric("Quantity", f"{_qty:,}")
                _r3c2.metric("Position Value", f"₹{_pos_value:,.0f}")
                _r3c3.metric("% of Portfolio", f"{_portfolio_pct:.1f}%")
                _r3c4.metric("Opened", _time_ago(_opened_at))

                # Progress bar: price between stop and target
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

    st.divider()

    # ── Strategy Performance Attribution ─────────────────────────────────────
    st.header("Strategy Performance Attribution")

    _bt_trades = _query_df(
        conn,
        "SELECT strategy, pnl, charges, closed_at, opened_at, regime FROM trades ORDER BY closed_at",
    )

    if _bt_trades.empty:
        st.info("No closed trades in the database yet. Run the agent or a backtest first.")
    else:
        _bt_trades["pnl_net"] = _bt_trades["pnl"] - _bt_trades["charges"]
        _bt_trades["closed_at"] = pd.to_datetime(_bt_trades["closed_at"], errors="coerce")
        _bt_trades["opened_at"] = pd.to_datetime(_bt_trades["opened_at"], errors="coerce")

        # ── 1. Strategy Performance Table ────────────────────────────────────
        st.subheader("Strategy Performance Table")

        def _pf_color(val: float) -> str:
            """Return CSS color string for profit factor cell."""
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

        # Bar chart: Net P&L by strategy (horizontal)
        _pnl_bar_colors = [
            "#2ecc71" if v >= 0 else "#e74c3c"
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

        # ── 2. Monthly P&L Heatmap (enhanced via core.analytics.metrics) ────────
        st.subheader("Monthly P&L Heatmap")

        # Try to load TradeRecord objects for the rich analytics API
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

        # Prefer compute_monthly_pnl if we have TradeRecord objects; fall back to raw SQL agg
        _use_rich_hm = bool(_analytics_trades)
        if _use_rich_hm:
            try:
                _rich_monthly = _compute_monthly_pnl(_analytics_trades)  # indexed by YYYY-MM
            except Exception:
                _use_rich_hm = False

        if not _monthly_pnl_raw.empty:
            _years_sorted = sorted(_monthly_pnl_raw["year"].unique())
            _month_labels = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
                             "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
            # Build z / text / hover matrices: rows = years, cols = months 1–12
            _hm_matrix: list = []
            _hm_text: list = []          # primary cell label: ₹ P&L
            _hm_hover: list = []         # full tooltip: P&L + trades + win%
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
                        # Determine win_rate from rich API if available
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

        # ── 2b. Strategy Attribution Chart (compute_strategy_attribution) ────────
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
                        "#2ecc71" if v >= 0 else "#e74c3c"
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

        # ── 2c. Regime Performance Radar + Trade Count Bar ───────────────────────
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
                            # Close the radar polygon
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
                            "TREND": "#2ecc71",
                            "RANGE": "#3498db",
                            "VOLATILE": "#e74c3c",
                            "UNKNOWN": "#95a5a6",
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

                    # Summary metrics row
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

        # ── 2d. Extended Metrics Table (compute_extended_metrics) ────────────────
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
                            "#2ecc71" if _is_good is True
                            else ("#e74c3c" if _is_good is False else "#f0f0f0")
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

        # ── 3. Trade Distribution Charts ─────────────────────────────────────
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
                        "#2ecc71" if v >= 0 else "#e74c3c"
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
                    color_discrete_map={"Win": "#2ecc71", "Loss": "#e74c3c"},
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

        # ── 4. Regime Performance Table ───────────────────────────────────────
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
                        "TREND": "rgba(46,204,113,0.12)",
                        "RANGE": "rgba(52,152,219,0.12)",
                        "VOLATILE": "rgba(231,76,60,0.12)",
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


# ────────────────────────────────────────────────────────────────────────────
# TAB 6: AI REVIEW
# ────────────────────────────────────────────────────────────────────────────


def _call_claude_haiku(prompt: str, api_key: str, system: str = "") -> str:
    """Call claude-haiku-4-5 and return the text response. Returns error string on failure."""
    try:
        import anthropic as _ant
        client = _ant.Anthropic(api_key=api_key)
        msgs = [{"role": "user", "content": prompt}]
        kwargs: dict = {"model": "claude-haiku-4-5", "max_tokens": 1024, "messages": msgs}
        if system:
            kwargs["system"] = system
        resp = client.messages.create(**kwargs)
        return resp.content[0].text if resp.content else "(empty response)"
    except Exception as _ce:
        return f"Claude call failed: {_ce}"


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

    # ── Auto-review countdown to next 15:35 IST ───────────────────────────────
    if auto_eod and _api_key_ok:
        _now_utc_ar = datetime.utcnow()
        _now_ist_ar = _now_utc_ar + timedelta(hours=5, minutes=30)
        _eod_review_time = _now_ist_ar.replace(hour=15, minute=35, second=0, microsecond=0)
        if _now_ist_ar >= _eod_review_time:
            # After 15:35 — next review is tomorrow
            _next_eod = _eod_review_time + timedelta(days=1)
        else:
            _next_eod = _eod_review_time
        _secs_to_eod = int((_next_eod - _now_ist_ar).total_seconds())
        _h_to_eod, _rem = divmod(_secs_to_eod, 3600)
        _m_to_eod, _s_to_eod = divmod(_rem, 60)
        # Trigger auto-review if within 60s of 15:35 on a weekday
        _is_weekday_ar = _now_ist_ar.weekday() < 5
        _near_eod = abs((_now_ist_ar - _eod_review_time).total_seconds()) < 60
        if _is_weekday_ar and _near_eod and _api_key_ok:
            st.info("Auto-EOD review triggering now (15:35 IST)...")
            with st.spinner("Running auto EOD review..."):
                try:
                    _auto_report = _run_eod_review(
                        db_path=str(DB_PATH),
                        snapshot_path=str(SNAPSHOT_PATH),
                        api_key=_anthropic_api_key,
                    )
                    if _auto_report:
                        _auto_dict = {
                            "timestamp": datetime.utcnow().isoformat(),
                            "summary": _auto_report.summary,
                            "suggestions": [
                                {
                                    "strategy": s.strategy,
                                    "parameter": s.parameter,
                                    "current_value": s.current_value,
                                    "suggested_value": s.suggested_value,
                                    "rationale": s.rationale,
                                }
                                for s in _auto_report.suggestions
                            ],
                            "flags": _auto_report.flags,
                        }
                        _save_last_review(_auto_dict)
                        st.success("Auto-review complete.")
                except Exception as _auto_exc:
                    st.warning(f"Auto-review failed: {_auto_exc}")
        else:
            _next_label = "today" if _now_ist_ar < _eod_review_time else "tomorrow"
            st.info(
                f"Auto-review enabled. Next review: {_next_label} at 15:35 IST "
                f"(in {_h_to_eod}h {_m_to_eod}m {_s_to_eod}s)"
            )

    st.divider()

    # ════════════════════════════════════════════════════════════════════════════
    # SECTION 1: MARKET COMMENTARY WIDGET
    # ════════════════════════════════════════════════════════════════════════════
    st.subheader("Market Commentary")
    st.caption(
        "Ask Claude about a specific stock or your overall portfolio. "
        "Uses the current regime, recent trades, and open positions as context."
    )

    _mc_nifty50_syms = [
        "RELIANCE", "TCS", "HDFCBANK", "INFY", "ICICIBANK", "HINDUNILVR", "ITC",
        "KOTAKBANK", "LT", "SBIN", "AXISBANK", "BAJFINANCE", "BHARTIARTL", "ASIANPAINT",
        "MARUTI", "HCLTECH", "WIPRO", "ULTRACEMCO", "NESTLEIND", "POWERGRID",
        "TITAN", "TECHM", "NTPC", "JSWSTEEL", "SUNPHARMA", "TATASTEEL", "TATAMOTORS",
        "ADANIENT", "ADANIPORTS", "BAJAJFINSV", "BPCL", "BRITANNIA", "CIPLA",
        "COALINDIA", "DIVISLAB", "DRREDDY", "EICHERMOT", "GRASIM", "HEROMOTOCO",
        "HINDALCO", "INDUSINDBK", "M&M", "ONGC", "SBILIFE", "TATACONSUM",
        "BAJAJ-AUTO", "HDFCLIFE", "SHRIRAMFIN", "BEL", "TRENT",
    ]
    _mc_open_syms = [
        p.get("symbol", "") for p in snap.get("open_positions", []) if p.get("symbol")
    ]
    _mc_sym_options = sorted(set(_mc_open_syms + _mc_nifty50_syms))

    _mc_preset_col, _mc_sym_col = st.columns([2, 2])
    _mc_preset = _mc_preset_col.selectbox(
        "Preset question",
        options=[
            "Custom question...",
            "Ask about portfolio",
            "What is your view on {stock} given today's regime?",
            "Should I hold or exit {stock} given current conditions?",
            "What risks does {stock} face in this regime?",
        ],
        key="mc_preset_sel",
    )
    _mc_selected_sym = _mc_sym_col.selectbox(
        "Stock (for stock questions)",
        options=_mc_sym_options,
        index=0,
        key="mc_sym_sel",
    )

    # Build default question from preset
    if _mc_preset == "Ask about portfolio":
        _mc_default_q = "Analyse my overall portfolio health: positions, regime alignment, and risk exposure."
    elif "{stock}" in _mc_preset:
        _mc_default_q = _mc_preset.replace("{stock}", _mc_selected_sym)
    else:
        _mc_default_q = ""

    _mc_question = st.text_area(
        "Your question to Claude:",
        value=_mc_default_q,
        height=80,
        key="mc_question_input",
        placeholder="e.g. What is your view on RELIANCE given today's TREND regime?",
    )

    _mc_submit = st.button(
        "Ask Claude",
        type="primary",
        disabled=not (_api_key_ok and _ANTHROPIC_INSTALLED),
        key="mc_ask_btn",
    )

    if _mc_submit and _mc_question.strip():
        # Build rich context
        _mc_regime = snap.get("regime", "UNKNOWN")
        _mc_vix = snap.get("vix", "n/a")
        _mc_adx = snap.get("adx", "n/a")
        _mc_equity = snap.get("equity", 0)
        _mc_halted = snap.get("halted", False)

        # Open positions summary
        _mc_pos_lines = []
        for _mcp in snap.get("open_positions", []):
            _mcp_sym = _mcp.get("symbol", "?")
            _mcp_side = _mcp.get("side", "?")
            _mcp_qty = _mcp.get("qty", 0)
            _mcp_unr = _mcp.get("unrealized_pnl", 0)
            _mc_pos_lines.append(
                f"  - {_mcp_sym} {_mcp_side} qty={_mcp_qty} unrealized_pnl=₹{_mcp_unr:.0f}"
            )

        # Recent trades for the selected stock (or all if portfolio question)
        if _mc_preset == "Ask about portfolio":
            _mc_trades_q = _query_df(
                conn,
                "SELECT symbol, side, pnl, strategy, closed_at FROM trades ORDER BY closed_at DESC LIMIT 20",
            )
        else:
            _mc_trades_q = _query_df(
                conn,
                "SELECT symbol, side, pnl, strategy, closed_at FROM trades "
                "WHERE symbol = ? ORDER BY closed_at DESC LIMIT 10",
                params=(_mc_selected_sym,),
            )

        _mc_trades_lines = []
        if not _mc_trades_q.empty:
            for _, _mtr in _mc_trades_q.iterrows():
                _mc_trades_lines.append(
                    f"  - {_mtr.get('closed_at', '')[:10]} {_mtr.get('symbol', '')} "
                    f"{_mtr.get('side', '')} pnl=₹{float(_mtr.get('pnl', 0)):.0f} "
                    f"via {_mtr.get('strategy', '')}"
                )

        _mc_context = f"""You are a concise, expert NSE trading analyst.

Current market context:
- Regime: {_mc_regime}
- India VIX: {_mc_vix}
- ADX: {_mc_adx}
- Agent equity: ₹{_mc_equity:,.0f}
- Agent halted: {_mc_halted}

Open positions ({len(_mc_pos_lines)} total):
{chr(10).join(_mc_pos_lines) if _mc_pos_lines else "  None"}

Recent trades:
{chr(10).join(_mc_trades_lines) if _mc_trades_lines else "  No recent trades"}

Respond in 3–5 sentences. Be direct and actionable. Do not disclaim about data availability.
"""

        with st.spinner("Asking Claude..."):
            _mc_response = _call_claude_haiku(
                prompt=_mc_question.strip(),
                api_key=_anthropic_api_key,
                system=_mc_context,
            )

        st.markdown(
            "<div style='background:#1e2836;border:1px solid #3498db;border-radius:8px;"
            "padding:14px 18px;margin-top:8px'>"
            "<small style='color:#3498db;font-weight:600'>Claude says:</small><br>"
            f"<span style='color:#e8e8e8;line-height:1.6'>{_mc_response}</span>"
            "</div>",
            unsafe_allow_html=True,
        )
    elif _mc_submit and not _mc_question.strip():
        st.warning("Please enter a question before submitting.")

    if not (_api_key_ok and _ANTHROPIC_INSTALLED):
        st.caption("Market commentary requires ANTHROPIC_API_KEY and the `anthropic` package.")

    st.divider()

    # ════════════════════════════════════════════════════════════════════════════
    # SECTION 2: STRATEGY SIGNALS EXPLAINER (last 10 signals with rejection detail)
    # ════════════════════════════════════════════════════════════════════════════
    st.subheader("Strategy Signals Explainer")
    st.caption("Last 10 signals with rationale. For rejected signals, see the guardrail rule and get a plain-English explanation.")

    _se_sig_df = _query_df(
        conn,
        "SELECT id, ts, symbol, side, strategy, regime, entry_price, accepted, rejection_reason, rationale "
        "FROM signals ORDER BY id DESC LIMIT 10",
    )

    if not _se_sig_df.empty:
        for _, _se_row in _se_sig_df.iterrows():
            _se_accepted = bool(_se_row.get("accepted", 1))
            _se_sym = str(_se_row.get("symbol", "?"))
            _se_side = str(_se_row.get("side", "?"))
            _se_strat = str(_se_row.get("strategy", "?"))
            _se_regime = str(_se_row.get("regime", "?"))
            _se_ts = str(_se_row.get("ts", ""))[:16]
            _se_price = _se_row.get("entry_price", 0)
            _se_reason = str(_se_row.get("rejection_reason", "") or "")
            _se_rationale = str(_se_row.get("rationale", "") or "")
            _se_sig_id = int(_se_row.get("id", 0))

            _se_border = "#2ecc71" if _se_accepted else "#e74c3c"
            _se_status_label = "ACCEPTED" if _se_accepted else "REJECTED"
            _se_status_color = "#2ecc71" if _se_accepted else "#e74c3c"

            with st.container():
                st.markdown(
                    f"<div style='border:1px solid {_se_border};border-radius:8px;"
                    f"padding:10px 14px;margin-bottom:8px;background:#1a1a1a'>",
                    unsafe_allow_html=True,
                )
                _se_c1, _se_c2, _se_c3, _se_c4 = st.columns([2, 2, 2, 1])
                _se_c1.markdown(
                    f"<b style='color:#f0f0f0'>{_se_sym}</b> "
                    f"<span style='color:#aaa;font-size:0.85em'>{_se_side} · {_se_strat}</span>",
                    unsafe_allow_html=True,
                )
                _se_c2.markdown(
                    f"<span style='color:#aaa;font-size:0.85em'>Regime: {_se_regime} · "
                    f"₹{float(_se_price or 0):,.0f}</span>",
                    unsafe_allow_html=True,
                )
                _se_c3.markdown(
                    f"<span style='color:#888;font-size:0.8em'>{_se_ts}</span>",
                    unsafe_allow_html=True,
                )
                _se_c4.markdown(
                    f"<span style='color:{_se_status_color};font-weight:700;font-size:0.9em'>"
                    f"{_se_status_label}</span>",
                    unsafe_allow_html=True,
                )

                if _se_rationale:
                    st.markdown(
                        f"<span style='color:#aaa;font-size:0.85em'>Strategy rationale: {_se_rationale}</span>",
                        unsafe_allow_html=True,
                    )

                if not _se_accepted and _se_reason:
                    st.markdown(
                        f"<span style='color:#e74c3c;font-size:0.85em'>Rejected: {_se_reason}</span>",
                        unsafe_allow_html=True,
                    )
                    _se_explain_key = f"se_explain_{_se_sig_id}"
                    _se_result_key = f"se_explain_result_{_se_sig_id}"
                    if st.button(
                        "Explain this rejection",
                        key=_se_explain_key,
                        disabled=not (_api_key_ok and _ANTHROPIC_INSTALLED),
                    ):
                        with st.spinner("Claude explaining..."):
                            _se_prompt = (
                                f"A trading signal was rejected by a guardrail. "
                                f"Explain in plain English (2-3 sentences) why this is a sensible protection:\n\n"
                                f"Symbol: {_se_sym}, Side: {_se_side}, Strategy: {_se_strat}, "
                                f"Regime: {_se_regime}\nRejection rule: {_se_reason}"
                            )
                            _se_explanation = _call_claude_haiku(
                                prompt=_se_prompt, api_key=_anthropic_api_key
                            )
                            st.session_state[_se_result_key] = _se_explanation
                    if st.session_state.get(_se_result_key):
                        st.markdown(
                            f"<div style='background:#1e2836;border-left:3px solid #3498db;"
                            f"padding:8px 12px;margin-top:4px;border-radius:4px'>"
                            f"<small style='color:#3498db'>Claude:</small> "
                            f"<span style='color:#ddd;font-size:0.9em'>"
                            f"{st.session_state[_se_result_key]}</span></div>",
                            unsafe_allow_html=True,
                        )

                st.markdown("</div>", unsafe_allow_html=True)
    else:
        st.info("No signals recorded yet. Run the agent to generate signals.")

    st.divider()

    # ════════════════════════════════════════════════════════════════════════════
    # SECTION 3: PARAMETER SUGGESTIONS (from last_review.json) with Apply buttons
    # ════════════════════════════════════════════════════════════════════════════
    _ps_last = _read_last_review()
    _ps_suggs = _ps_last.get("suggestions", []) if _ps_last else []

    if _ps_suggs:
        _ps_ts = _ps_last.get("timestamp", "")
        try:
            _ps_ts_fmt = datetime.fromisoformat(_ps_ts).strftime("%Y-%m-%d %H:%M UTC")
        except Exception:
            _ps_ts_fmt = _ps_ts

        st.subheader(f"Parameter Suggestions from Last Review ({_ps_ts_fmt})")
        st.caption(
            "Each suggestion was generated by Claude's EOD review. "
            "Click 'Apply This Change' to queue it via the command queue — "
            "the running agent applies it within 1 second."
        )

        for _ps_idx, _ps_s in enumerate(_ps_suggs):
            _ps_strat = str(_ps_s.get("strategy", "?"))
            _ps_param = str(_ps_s.get("parameter", "?"))
            _ps_cur = _ps_s.get("current_value", "?")
            _ps_sug = _ps_s.get("suggested_value", "?")
            _ps_rat = str(_ps_s.get("rationale", ""))

            _ps_card_bg = "#1e2836"
            st.markdown(
                f"<div style='background:{_ps_card_bg};border:1px solid #f39c12;"
                f"border-radius:8px;padding:12px 16px;margin-bottom:10px'>",
                unsafe_allow_html=True,
            )
            _ps_col1, _ps_col2, _ps_col3 = st.columns([2, 2, 2])
            _ps_col1.markdown(
                f"<b style='color:#f0f0f0'>{_ps_strat}</b><br>"
                f"<span style='color:#aaa;font-size:0.85em'>{_ps_param}</span>",
                unsafe_allow_html=True,
            )
            _ps_col2.markdown(
                f"<span style='color:#e74c3c;font-weight:600'>{_ps_cur}</span>"
                f"<span style='color:#aaa'> → </span>"
                f"<span style='color:#2ecc71;font-weight:600'>{_ps_sug}</span>",
                unsafe_allow_html=True,
            )
            _ps_col3.markdown(
                f"<span style='color:#aaa;font-size:0.85em'>{_ps_rat}</span>",
                unsafe_allow_html=True,
            )

            _ps_apply_key = f"ps_apply_{_ps_idx}_{_ps_param}"
            _ps_applied_key = f"ps_applied_{_ps_idx}_{_ps_param}"
            if not st.session_state.get(_ps_applied_key):
                if st.button(
                    f"Apply This Change  ({_ps_cur} → {_ps_sug})",
                    key=_ps_apply_key,
                    type="primary",
                ):
                    try:
                        from core.command_queue import enqueue as _ps_enqueue
                        _ps_enqueue("update_risk_param", {"param": _ps_param, "value": _ps_sug})
                        st.session_state[_ps_applied_key] = True
                        st.success(
                            f"Queued: {_ps_param} = {_ps_sug} "
                            f"(was {_ps_cur}). Agent will apply within 1 second."
                        )
                        st.rerun()
                    except Exception as _ps_exc:
                        st.error(f"Failed to queue change: {_ps_exc}")
            else:
                st.markdown(
                    f"<span style='color:#2ecc71;font-size:0.85em'>Applied: {_ps_param} set to {_ps_sug}</span>",
                    unsafe_allow_html=True,
                )
            st.markdown("</div>", unsafe_allow_html=True)
    else:
        st.subheader("Parameter Suggestions")
        st.info(
            "No parameter suggestions available. Run an EOD review below to generate suggestions."
        )

    st.divider()

    # ════════════════════════════════════════════════════════════════════════════
    # SECTION 4: EOD REVIEW (run + last review display)
    # ════════════════════════════════════════════════════════════════════════════
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

    st.divider()

    # ── News Sentiment Scorer ─────────────────────────────────────────────────
    st.subheader("📰 News Sentiment Scorer")
    st.caption("Score news headlines for selected stocks using Claude Haiku")

    selected_symbols = st.multiselect(
        "Select symbols to score",
        options=["RELIANCE", "INFY", "TCS", "HDFC", "ICICIBANK", "HCLTECH", "WIPRO", "BAJFINANCE", "LTIM", "AXISBANK"],
        default=["RELIANCE", "INFY"],
    )

    # Text area for each selected symbol's headlines
    headlines_input = {}
    for sym in selected_symbols:
        headlines_input[sym] = st.text_area(
            f"Headlines for {sym} (one per line):",
            height=80,
            key=f"headlines_{sym}",
        )

    if st.button("Score Sentiment", disabled=not _NEWS_SCORER_AVAILABLE):
        if not os.getenv("ANTHROPIC_API_KEY", ""):
            st.warning("Set ANTHROPIC_API_KEY in .env to use news scoring")
        else:
            api_key = os.getenv("ANTHROPIC_API_KEY", "")
            results = {}
            with st.spinner("Scoring with Claude Haiku..."):
                for sym, raw in headlines_input.items():
                    lines = [l.strip() for l in raw.splitlines() if l.strip()]
                    if lines:
                        score = score_news(sym, lines, api_key=api_key)
                        results[sym] = score

            if results:
                for sym, score in results.items():
                    if score:
                        color = "🟢" if score.score > 0.2 else ("🔴" if score.score < -0.2 else "⚪")
                        st.metric(f"{color} {sym}", f"{score.score:+.2f}", f"confidence {score.confidence:.0%}")
                        st.caption(score.summary)
            else:
                st.info("No headlines were entered. Add at least one headline per symbol.")

    if not _NEWS_SCORER_AVAILABLE:
        st.caption("core.llm.news_scorer could not be imported. Ensure the anthropic package is installed.")


# ────────────────────────────────────────────────────────────────────────────
# TAB 7: CONTROLS
# ────────────────────────────────────────────────────────────────────────────

with tab_controls:
    st.header("Agent Controls & Risk Limits")

    # ── Agent start/stop ─────────────────────────────────────────────────────
    st.subheader("Agent Process")
    ctrl_running = _agent_is_running()
    _ctrl_halted = snap.get("halted", False)
    _ctrl_halt_reason = snap.get("halt_reason", "")
    _ctrl_regime = snap.get("regime", "UNKNOWN")
    _ctrl_last_sig_df = _query_df(conn, "SELECT ts FROM signals ORDER BY id DESC LIMIT 1")
    _ctrl_last_sig = (
        _time_ago(str(_ctrl_last_sig_df["ts"].iloc[0]))
        if not _ctrl_last_sig_df.empty and "ts" in _ctrl_last_sig_df.columns
        else "No signals"
    )
    _ctrl_in_market = _is_market_hours()

    # Status badge: RUNNING / HALTED / STOPPED, with real-time details
    if _ctrl_halted:
        _ctrl_status_label = "HALTED"
        _ctrl_status_bg = "#c0392b"
        _ctrl_status_detail = f"Halt reason: {_ctrl_halt_reason or 'unknown'}"
    elif ctrl_running:
        _ctrl_status_label = "RUNNING"
        _ctrl_status_bg = "#1a7a4a"
        _ctrl_status_detail = (
            f"Regime: {_ctrl_regime} &nbsp;|&nbsp; Last signal: {_ctrl_last_sig} "
            f"&nbsp;|&nbsp; Market: {'OPEN' if _ctrl_in_market else 'CLOSED'}"
        )
    else:
        _ctrl_status_label = "STOPPED"
        _ctrl_status_bg = "#555"
        _ctrl_status_detail = "Agent process is not running."

    _ctrl_c1, _ctrl_c2 = st.columns([2, 2])
    _ctrl_c1.markdown(
        f"<div style='background:{_ctrl_status_bg};padding:14px 18px;border-radius:8px'>"
        f"<b style='color:white;font-size:1.15em'>{_ctrl_status_label}</b><br>"
        f"<span style='color:#ccc;font-size:0.85em'>{_ctrl_status_detail}</span>"
        f"</div>",
        unsafe_allow_html=True,
    )
    with _ctrl_c2:
        if ctrl_running:
            if st.button("Stop Agent", type="secondary", key="ctrl_stop", use_container_width=True):
                st.toast(_stop_agent())
                st.rerun()
        else:
            if st.button("Start Agent", type="primary", key="ctrl_start", use_container_width=True):
                st.toast(_start_agent())
                st.rerun()
        _ctrl_ts = datetime.now().strftime("%H:%M:%S")
        st.caption(f"Status last checked: {_ctrl_ts} — click Refresh (sidebar) to update")

    st.divider()

    # ── LIVE RISK PARAMETERS ──────────────────────────────────────────────────
    st.subheader("Live Risk Parameters")
    st.caption(
        "Changes are queued via the command queue — the running agent applies them within 1 second. "
        "Config file is also updated so restarts retain the new values."
    )

    risk_cfg = config.get("risk", {})
    _cur_per_trade = float(risk_cfg.get("per_trade_risk_pct", 1.0))
    _cur_max_pos = int(risk_cfg.get("max_open_positions", 5))
    _cur_daily = float(risk_cfg.get("daily_loss_circuit_pct", 3.0))
    _cur_dd = float(risk_cfg.get("drawdown_circuit_pct", 10.0))
    _cur_spread = float(risk_cfg.get("max_spread_pct", 0.3))

    with st.form("risk_params"):
        rp_col1, rp_col2 = st.columns(2)

        new_per_trade = rp_col1.slider(
            "Per-trade risk (%)",
            min_value=0.25, max_value=2.0, step=0.25,
            value=float(min(max(_cur_per_trade, 0.25), 2.0)),
            help="Maximum % of equity risked on a single trade (stop-loss distance x qty)",
        )
        new_max_pos = rp_col2.slider(
            "Max open positions",
            min_value=1, max_value=8, step=1,
            value=int(min(max(_cur_max_pos, 1), 8)),
            help="Hard cap on concurrent open positions across all strategies",
        )
        new_daily = rp_col1.slider(
            "Daily loss circuit (%)",
            min_value=1.0, max_value=5.0, step=0.5,
            value=float(min(max(_cur_daily, 1.0), 5.0)),
            help="Halt new entries for the day when day P&L falls below this % of start-of-day equity",
        )
        new_dd = rp_col2.slider(
            "Drawdown circuit (%)",
            min_value=5.0, max_value=20.0, step=1.0,
            value=float(min(max(_cur_dd, 5.0), 20.0)),
            help="Halt agent and trigger EOD review when peak-to-trough drawdown exceeds this %",
        )
        new_spread = rp_col1.number_input(
            "Max spread (%)",
            min_value=0.1, max_value=1.0, step=0.05,
            value=float(min(max(_cur_spread, 0.1), 1.0)),
            format="%.2f",
            help="Reject orders if bid-ask spread exceeds this % of price",
        )

        # Current vs proposed side-by-side preview
        with st.expander("Current vs Proposed values", expanded=False):
            _preview_data = {
                "Parameter": [
                    "per_trade_risk_pct", "max_open_positions",
                    "daily_loss_circuit_pct", "drawdown_circuit_pct", "max_spread_pct",
                ],
                "Current": [_cur_per_trade, _cur_max_pos, _cur_daily, _cur_dd, _cur_spread],
                "Proposed": [new_per_trade, new_max_pos, new_daily, new_dd, new_spread],
            }
            _preview_df = pd.DataFrame(_preview_data)
            _preview_df["Changed"] = _preview_df["Current"] != _preview_df["Proposed"]
            st.dataframe(
                _preview_df.style.applymap(
                    lambda v: "color: #f39c12; font-weight: bold" if v else "",
                    subset=["Changed"],
                ),
                hide_index=True,
                use_container_width=True,
            )

        _rp_submitted = st.form_submit_button(
            "Apply Risk Parameters", type="primary", use_container_width=True
        )

    # Concentration warning (outside form so it stays visible after submit)
    _concentration = new_per_trade * new_max_pos
    if _concentration > 10.0:
        st.warning(
            f"Concentration warning: per_trade_risk ({new_per_trade}%) x max_positions "
            f"({new_max_pos}) = {_concentration:.1f}% — exceeds 10% concentrated risk threshold."
        )

    if _rp_submitted:
        try:
            from core.command_queue import enqueue as _enqueue_cmd
            _risk_updates = {
                "per_trade_risk_pct": new_per_trade,
                "max_open_positions": int(new_max_pos),
                "daily_loss_circuit_pct": new_daily,
                "drawdown_circuit_pct": new_dd,
                "max_spread_pct": new_spread,
            }
            for _param_key, _param_val in _risk_updates.items():
                _enqueue_cmd("update_risk_param", {"param": _param_key, "value": _param_val})
            # Also persist to config file so restarts retain the values
            _write_config_risk(_risk_updates)
            st.success("Changes queued — agent will apply within 1 second")
            st.cache_data.clear()
        except Exception as _rp_exc:
            st.error(f"Failed to queue risk parameter changes: {_rp_exc}")

    st.divider()

    # ── CIRCUIT BREAKER STATUS ────────────────────────────────────────────────
    st.subheader("Circuit Breaker Status")

    _halted = snap.get("halted", False)
    _halt_reason = snap.get("halt_reason", "")

    # Agent halt/resume banner
    if _halted:
        st.markdown(
            "<div style='background:#c0392b;padding:14px;border-radius:8px;margin-bottom:12px'>"
            "<b style='color:white;font-size:1.1em'>AGENT HALTED</b>"
            + (f"<br><span style='color:#ffdddd'>{_halt_reason}</span>" if _halt_reason else "")
            + "</div>",
            unsafe_allow_html=True,
        )
        if st.button("Resume Agent", type="primary", key="ctrl_resume_agent"):
            try:
                from core.command_queue import enqueue as _enqueue_cmd
                _enqueue_cmd("resume_agent", {"reason": "manual resume from dashboard"})
                st.success("Resume command queued — agent will resume within 1 second.")
                st.rerun()
            except Exception as _resume_exc:
                st.error(f"Failed to queue resume: {_resume_exc}")
    else:
        st.markdown(
            "<div style='background:#1a7a4a;padding:14px;border-radius:8px;margin-bottom:12px'>"
            "<b style='color:white;font-size:1.1em'>AGENT ACTIVE</b>"
            "</div>",
            unsafe_allow_html=True,
        )
        _halt_reason_input = st.text_input(
            "Halt reason (optional)",
            value="",
            key="ctrl_halt_reason_input",
            placeholder="e.g. manual halt for risk review",
        )
        if st.button("Halt Agent", type="secondary", key="ctrl_halt_agent"):
            try:
                from core.command_queue import enqueue as _enqueue_cmd
                _enqueue_cmd("halt_agent", {
                    "reason": _halt_reason_input or "manual halt from dashboard"
                })
                st.warning("Halt command queued — agent will halt within 1 second.")
                st.rerun()
            except Exception as _halt_exc:
                st.error(f"Failed to queue halt: {_halt_exc}")

    st.markdown("**Daily Loss Circuit**")
    _start_today_equity = float(snap.get("starting_equity_today", 0.0) or 0.0)
    _current_equity_ctrl = float(snap.get("equity", 0.0) or 0.0)
    _daily_circuit_pct = float(risk_cfg.get("daily_loss_circuit_pct", 3.0))
    if _start_today_equity > 0:
        _day_pnl_pct_ctrl = (_current_equity_ctrl - _start_today_equity) / _start_today_equity * 100
        _daily_usage = abs(min(_day_pnl_pct_ctrl, 0.0)) / _daily_circuit_pct * 100
        st.markdown(
            f"Day P&L: **{_day_pnl_pct_ctrl:+.2f}%** / circuit at **-{_daily_circuit_pct:.1f}%** "
            f"({_daily_usage:.0f}% of circuit consumed)",
        )
        st.progress(
            min(_daily_usage / 100, 1.0),
            text=f"Daily loss: {_daily_usage:.0f}% of -{_daily_circuit_pct:.1f}% circuit",
        )
        if _daily_usage >= 80:
            st.warning("Daily loss circuit approaching trigger level.")
    else:
        st.caption("Daily P&L data unavailable (agent not running or snapshot empty).")

    st.markdown("**Drawdown Circuit**")
    _peak_equity_ctrl = float(snap.get("peak_equity", 0.0) or 0.0)
    _dd_circuit_pct = float(risk_cfg.get("drawdown_circuit_pct", 10.0))
    if _peak_equity_ctrl > 0 and _current_equity_ctrl > 0:
        _current_dd_pct = max((_peak_equity_ctrl - _current_equity_ctrl) / _peak_equity_ctrl * 100, 0.0)
        _dd_usage = _current_dd_pct / _dd_circuit_pct * 100
        st.markdown(
            f"Current drawdown: **{_current_dd_pct:.2f}%** / circuit at **{_dd_circuit_pct:.1f}%** "
            f"({_dd_usage:.0f}% of circuit consumed)",
        )
        st.progress(
            min(_dd_usage / 100, 1.0),
            text=f"Drawdown: {_dd_usage:.0f}% of {_dd_circuit_pct:.1f}% circuit",
        )
        if _dd_usage >= 80:
            st.warning("Drawdown circuit approaching trigger level.")
    else:
        st.caption("Drawdown data unavailable (agent not running or snapshot empty).")

    st.divider()

    # ── SIGNAL COOLDOWN ───────────────────────────────────────────────────────
    st.subheader("Signal Cooldown")
    _exec_cfg = config.get("execution", {})
    _cooldown_minutes = int(_exec_cfg.get("signal_cooldown_minutes", 30))
    st.caption(
        f"Current cooldown setting: **{_cooldown_minutes} minutes** after exit, "
        f"the same symbol cannot be re-entered."
    )

    _last_exit_by_symbol: dict = snap.get("last_exit_by_symbol", {})
    if _last_exit_by_symbol:
        _now_utc = datetime.utcnow()
        _cooldown_rows = []
        for _sym, _exit_ts_str in _last_exit_by_symbol.items():
            try:
                _exit_dt = datetime.fromisoformat(str(_exit_ts_str).rstrip("Z"))
                _elapsed = (_now_utc - _exit_dt).total_seconds() / 60.0
                _remaining = max(_cooldown_minutes - _elapsed, 0.0)
                _expires_at = _exit_dt + timedelta(minutes=_cooldown_minutes)
                _status = "Active" if _remaining > 0 else "Expired"
                _cooldown_rows.append({
                    "Symbol": _sym,
                    "Last Exit": _exit_dt.strftime("%H:%M:%S"),
                    "Cooldown Expires": _expires_at.strftime("%H:%M:%S"),
                    "Remaining (min)": f"{_remaining:.1f}" if _remaining > 0 else "0",
                    "Status": _status,
                })
            except Exception:
                _cooldown_rows.append({
                    "Symbol": _sym,
                    "Last Exit": str(_exit_ts_str),
                    "Cooldown Expires": "?",
                    "Remaining (min)": "?",
                    "Status": "Unknown",
                })

        def _cooldown_status_style(val: str) -> str:
            if val == "Active":
                return "color: #e74c3c; font-weight: bold"
            if val == "Expired":
                return "color: #2ecc71"
            return ""

        _cd_df = pd.DataFrame(_cooldown_rows)
        st.dataframe(
            _cd_df.style.applymap(_cooldown_status_style, subset=["Status"]),
            use_container_width=True,
            hide_index=True,
        )
    else:
        st.info(
            "No recent exits recorded in snapshot — cooldown tracking data will appear here "
            "once the agent has closed positions."
        )

    st.divider()

    # ── Multi-agent management UI ────────────────────────────────────────────
    st.subheader("Agent Instances")

    # Helper: discover all agent PID files in data/
    def _discover_agent_pids() -> list[dict]:
        """Scan data/ for agent*.pid files and return a list of agent info dicts."""
        _data_dir = Path("data")
        _agents_found: list[dict] = []
        # Primary agent (agent.pid)
        _primary_pid_path = _data_dir / "agent.pid"
        if _primary_pid_path.exists():
            try:
                _pid = int(_primary_pid_path.read_text().strip())
                import psutil as _psutil_ag
                _alive = _psutil_ag.pid_exists(_pid)
                _started = "—"
                if _alive:
                    try:
                        _p = _psutil_ag.Process(_pid)
                        _started = datetime.fromtimestamp(_p.create_time()).strftime("%Y-%m-%d %H:%M")
                    except Exception:
                        _started = "unknown"
                _agents_found.append({
                    "agent_id": "default",
                    "pid": _pid,
                    "profile": "Full (all strategies)",
                    "port": 8501,
                    "alive": _alive,
                    "started": _started,
                    "is_current": True,
                })
            except Exception:
                pass
        # Additional agents (agent_<id>.pid pattern)
        for _extra_pid_path in sorted(_data_dir.glob("agent_*.pid")):
            try:
                _ag_id = _extra_pid_path.stem.replace("agent_", "")
                _pid = int(_extra_pid_path.read_text().strip())
                import psutil as _psutil_ag
                _alive = _psutil_ag.pid_exists(_pid)
                _started = "—"
                if _alive:
                    try:
                        _p = _psutil_ag.Process(_pid)
                        _started = datetime.fromtimestamp(_p.create_time()).strftime("%Y-%m-%d %H:%M")
                    except Exception:
                        pass
                _profile_name = "Unknown"
                _port = 8501
                _meta_path = _data_dir / f"agent_{_ag_id}_meta.json"
                if _meta_path.exists():
                    try:
                        _meta = json.loads(_meta_path.read_text(encoding="utf-8"))
                        _profile_name = _meta.get("profile", "Unknown")
                        _port = _meta.get("port", 8501)
                    except Exception:
                        pass
                _agents_found.append({
                    "agent_id": _ag_id,
                    "pid": _pid,
                    "profile": _profile_name,
                    "port": _port,
                    "alive": _alive,
                    "started": _started,
                    "is_current": False,
                })
            except Exception:
                continue
        return _agents_found

    def _agent_equity_today_ctrl(agent_id: str) -> tuple[float, float]:
        """Return (equity, today_pnl) for a given agent_id from its snapshot file."""
        _snap_p = SNAPSHOT_PATH if agent_id == "default" else Path(f"data/snapshot_{agent_id}.json")
        if not _snap_p.exists():
            return 0.0, 0.0
        try:
            _s = json.loads(_snap_p.read_text(encoding="utf-8"))
            _eq = float(_s.get("equity", 0.0))
            _start = float(_s.get("starting_equity_today", _eq) or _eq)
            return _eq, _eq - _start
        except Exception:
            return 0.0, 0.0

    _ctrl_agents = _discover_agent_pids()

    if _ctrl_agents:
        _ctrl_agent_rows = []
        for _cag in _ctrl_agents:
            _cag_eq, _cag_pnl = _agent_equity_today_ctrl(_cag["agent_id"])
            # Read mode from snapshot; fall back to config
            _cag_snap_p = SNAPSHOT_PATH if _cag["agent_id"] == "default" else Path(f"data/snapshot_{_cag['agent_id']}.json")
            _cag_mode = "PAPER"
            if _cag_snap_p.exists():
                try:
                    _cag_s = json.loads(_cag_snap_p.read_text(encoding="utf-8"))
                    _cag_mode = str(_cag_s.get("mode", config.get("mode", "paper"))).upper()
                except Exception:
                    _cag_mode = str(config.get("mode", "paper")).upper()
            _ctrl_agent_rows.append({
                "Agent ID": _cag["agent_id"] + (" (this)" if _cag["is_current"] else ""),
                "Mode": _cag_mode,
                "Profile": _cag["profile"],
                "Started": _cag["started"],
                "Status": "RUNNING" if _cag["alive"] else "STOPPED",
                "Equity (INR)": f"{_cag_eq:,.0f}" if _cag_eq else "—",
                "Today P&L": f"{_cag_pnl:+,.0f}" if _cag_pnl != 0.0 else "—",
            })
        _ctrl_agents_df = pd.DataFrame(_ctrl_agent_rows)

        def _ctrl_status_style(val: str) -> str:
            if "RUNNING" in str(val):
                return "background-color:#1a7a4a;color:white;font-weight:bold"
            return "background-color:#7a1a1a;color:white;font-weight:bold"

        def _ctrl_mode_style(val: str) -> str:
            if str(val).upper() == "LIVE":
                return "color:#e74c3c;font-weight:bold"
            return "color:#3498db;font-weight:bold"

        def _ctrl_pnl_style(val: str) -> str:
            s = str(val)
            if s.startswith("+") or (s and s[0].isdigit() and float(s.replace(",", "") or 0) > 0):
                return "color:#2ecc71;font-weight:bold"
            if s.startswith("-"):
                return "color:#e74c3c;font-weight:bold"
            return ""

        st.dataframe(
            _ctrl_agents_df.style
            .applymap(_ctrl_status_style, subset=["Status"])
            .applymap(_ctrl_mode_style, subset=["Mode"])
            .applymap(_ctrl_pnl_style, subset=["Today P&L"]),
            use_container_width=True,
            hide_index=True,
        )
        st.caption(
            f"Showing {len(_ctrl_agents)} agent instance(s). "
            "Current agent is highlighted with '(this)'. "
            "Equity and Today P&L are read from per-agent snapshot files."
        )
    else:
        st.info("No agent PID files found in data/. Start the agent to see it listed here.")

    st.divider()

    # ── Current agent profile card (read-only) ────────────────────────────────
    st.subheader("Current Agent Profile")

    _profile_cfg = _read_config()
    _risk_profile = _profile_cfg.get("risk", {})
    _cap_profile = _profile_cfg.get("capital", {})
    _strategies_cfg = _profile_cfg.get("strategies", {})

    _enabled_count: int | str = sum(
        1 for _sv in _strategies_cfg.values()
        if isinstance(_sv, dict) and _sv.get("enabled", True)
    ) if _strategies_cfg else "—"

    _per_trade_pct = _risk_profile.get("per_trade_risk_pct", "—")
    _max_pos_profile = _risk_profile.get("max_open_positions", "—")
    _initial_inr_profile = int(_cap_profile.get("initial_inr", 0))
    _mode_profile = _profile_cfg.get("mode", "paper").upper()
    _mode_col_profile = "#e74c3c" if _mode_profile == "LIVE" else "#3498db"
    _gate_col_profile = "#2ecc71" if gate.get("passed") else "#e74c3c"
    _gate_lbl_profile = "PASSED" if gate.get("passed") else "FAILED"

    _prof_c1, _prof_c2 = st.columns(2)
    with _prof_c1:
        st.markdown(
            f"""
            <div style='background:#1e2836;padding:16px;border-radius:8px;border:1px solid #2ecc71'>
            <b style='color:#2ecc71;font-size:1.05em'>Agent Configuration</b><br><br>
            <table style='width:100%;color:#ddd;border-collapse:collapse'>
            <tr><td style='padding:3px 0'><b>Universe</b></td>
                <td>Nifty 50 (50 symbols)</td></tr>
            <tr><td style='padding:3px 0'><b>Active Strategies</b></td>
                <td>{_enabled_count} enabled</td></tr>
            <tr><td style='padding:3px 0'><b>Per-trade risk</b></td>
                <td>{_per_trade_pct}% of equity</td></tr>
            <tr><td style='padding:3px 0'><b>Max positions</b></td>
                <td>{_max_pos_profile}</td></tr>
            <tr><td style='padding:3px 0'><b>Starting capital</b></td>
                <td>&#8377;{_initial_inr_profile:,}</td></tr>
            </table>
            </div>
            """,
            unsafe_allow_html=True,
        )
    with _prof_c2:
        st.markdown(
            f"""
            <div style='background:#1e2836;padding:16px;border-radius:8px;border:1px solid #3498db'>
            <b style='color:#3498db;font-size:1.05em'>Gate &amp; Mode</b><br><br>
            <table style='width:100%;color:#ddd;border-collapse:collapse'>
            <tr><td style='padding:3px 0'><b>Mode</b></td>
                <td><span style='color:{_mode_col_profile};font-weight:bold'>{_mode_profile}</span></td></tr>
            <tr><td style='padding:3px 0'><b>Backtest gate</b></td>
                <td><span style='color:{_gate_col_profile};font-weight:bold'>{_gate_lbl_profile}</span></td></tr>
            <tr><td style='padding:3px 0'><b>Daily loss circuit</b></td>
                <td>-{_risk_profile.get("daily_loss_circuit_pct", "—")}%</td></tr>
            <tr><td style='padding:3px 0'><b>Drawdown circuit</b></td>
                <td>-{_risk_profile.get("drawdown_circuit_pct", "—")}%</td></tr>
            <tr><td style='padding:3px 0'><b>Max position size</b></td>
                <td>{_risk_profile.get("max_position_pct", "—")}% of equity</td></tr>
            </table>
            </div>
            """,
            unsafe_allow_html=True,
        )

    st.divider()

    # ── Start new agent (placeholder UI for intended multi-agent capability) ──
    st.subheader("Start New Agent Instance")
    st.caption(
        "Multiple agents are not yet supported in the execution layer. "
        "This UI shows the intended multi-agent capability for a future phase."
    )

    _AGENT_PROFILES = {
        "Conservative": {
            "description": "1% risk, 3 positions max, RANGE + VOLATILE regimes only",
            "per_trade_risk_pct": 1.0,
            "max_open_positions": 3,
            "regimes": ["RANGE", "VOLATILE"],
        },
        "Aggressive": {
            "description": "2% risk, 5 positions, all regimes",
            "per_trade_risk_pct": 2.0,
            "max_open_positions": 5,
            "regimes": ["TREND", "RANGE", "VOLATILE"],
        },
        "Defensive": {
            "description": "0.5% risk, 2 positions, RANGE / VOLATILE only",
            "per_trade_risk_pct": 0.5,
            "max_open_positions": 2,
            "regimes": ["RANGE", "VOLATILE"],
        },
        "Full (default)": {
            "description": "Standard config — all strategies, all regimes",
            "per_trade_risk_pct": float(_risk_profile.get("per_trade_risk_pct", 1.0)),
            "max_open_positions": int(_risk_profile.get("max_open_positions", 5)),
            "regimes": ["TREND", "RANGE", "VOLATILE"],
        },
    }

    _ma_col1, _ma_col2, _ma_col3 = st.columns(3)
    _selected_profile_name = _ma_col1.selectbox(
        "Agent Profile",
        options=list(_AGENT_PROFILES.keys()),
        index=0,
        key="new_agent_profile",
    )
    _selected_port = _ma_col2.selectbox(
        "Dashboard Port",
        options=[8502, 8503, 8504, 8505],
        index=0,
        key="new_agent_port",
    )
    _sel_prof = _AGENT_PROFILES[_selected_profile_name]
    _ma_col3.markdown(
        f"<div style='padding:10px;background:#1e2836;border-radius:6px;"
        f"font-size:0.85em;color:#aaa;height:100%'>"
        f"<b style='color:#f39c12'>{_selected_profile_name}</b><br>"
        f"{_sel_prof['description']}<br>"
        f"Risk: {_sel_prof['per_trade_risk_pct']}% &nbsp;|&nbsp; "
        f"Max pos: {_sel_prof['max_open_positions']}</div>",
        unsafe_allow_html=True,
    )

    if st.button("Start New Agent", type="primary", key="btn_start_new_agent"):
        st.warning(
            "Multiple agents not yet supported — coming soon. "
            f"Selected profile: **{_selected_profile_name}** on port **{_selected_port}**. "
            "The execution layer will be extended in a future phase to run isolated agent "
            "processes with separate SQLite databases, snapshot files, and PID tracking."
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
