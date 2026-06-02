"""Sidebar component for the NSE Trading Agent dashboard."""
from __future__ import annotations

import os
import signal
import subprocess
import sys
from datetime import datetime, time, timedelta
from pathlib import Path

import streamlit as st

from dashboard.design import COLORS, regime_hex
from dashboard.utils.charts import safe_html, time_ago
from dashboard.utils.db import db_connect, query_df
from dashboard.utils.snapshot import read_config, read_gate, read_snapshot

AGENT_PID_PATH = Path("data/agent.pid")

try:
    import pytz as _pytz
    _PYTZ_AVAILABLE = True
except ImportError:
    _pytz = None  # type: ignore[assignment]
    _PYTZ_AVAILABLE = False

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
            cwd=str(Path(__file__).parent.parent.parent),
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


def render_sidebar(snap: dict, config: dict, gate: dict) -> bool:
    """Render the full sidebar. Returns dark_mode bool."""
    with st.sidebar:
        dark_mode = st.sidebar.toggle("🌙 Dark Mode", key="dark_mode", value=False)
        st.title("pypoc | NSE Agent")
        auto_refresh = st.toggle("Auto-refresh (30s)", key="auto_refresh", value=False)
        if auto_refresh:
            st.caption(f"Last refresh: {datetime.now().strftime('%H:%M:%S')}")

        # Snapshot staleness indicator (top of sidebar for quick status)
        if snap and snap.get("ts"):
            try:
                _snap_ts = datetime.fromisoformat(str(snap["ts"]).replace("Z", ""))
                _snap_age_seconds = (datetime.now() - _snap_ts).total_seconds()
                if _snap_age_seconds < 30:
                    st.sidebar.success(f"🟢 Live — {_snap_age_seconds:.0f}s ago")
                elif _snap_age_seconds < 120:
                    st.sidebar.warning(f"🟡 Stale — {_snap_age_seconds:.0f}s ago")
                else:
                    st.sidebar.error(f"🔴 Offline — {_snap_age_seconds / 60:.0f}m ago")
            except Exception:
                st.sidebar.caption("Snapshot timestamp unavailable")
        else:
            st.sidebar.caption("No snapshot data")

        # NSE market hours indicator with countdown
        try:
            if _PYTZ_AVAILABLE and _pytz is not None:
                _ist = _pytz.timezone("Asia/Kolkata")
                _now_ist = datetime.now(_ist)
            else:
                # Fallback: UTC + 5:30
                _now_ist_naive = datetime.utcnow() + timedelta(hours=5, minutes=30)
                _now_ist = _now_ist_naive  # type: ignore[assignment]

            _is_weekday = _now_ist.weekday() < 5
            _market_open_time = time(9, 15)
            _market_close_time = time(15, 30)
            _now_time = _now_ist.time()
            if hasattr(_now_time, "replace"):
                _now_time_naive = _now_time.replace(tzinfo=None)
            else:
                _now_time_naive = _now_time
            _in_market_hours = _market_open_time <= _now_time_naive <= _market_close_time

            def _fmt_countdown(total_minutes: int) -> str:
                """Format total minutes as 'Xh Ym' or 'Ym'."""
                if total_minutes >= 60:
                    h, m = divmod(total_minutes, 60)
                    return f"{h}h {m}m"
                return f"{total_minutes}m"

            if _is_weekday and _in_market_hours:
                # Countdown to close
                try:
                    _now_for_close = datetime(_now_ist.year, _now_ist.month, _now_ist.day,
                                              _now_time_naive.hour, _now_time_naive.minute,
                                              _now_time_naive.second)
                    _close_for_calc = _now_for_close.replace(hour=15, minute=30, second=0)
                    _mins_to_close = max(0, int((_close_for_calc - _now_for_close).total_seconds() // 60))
                    st.sidebar.success(f"📈 Market OPEN — closes in {_fmt_countdown(_mins_to_close)}")
                except Exception:
                    st.sidebar.success("📈 Market OPEN")
            elif _is_weekday:
                # Countdown to open (only if before 09:15)
                try:
                    _now_for_open = datetime(_now_ist.year, _now_ist.month, _now_ist.day,
                                             _now_time_naive.hour, _now_time_naive.minute,
                                             _now_time_naive.second)
                    _open_for_calc = _now_for_open.replace(hour=9, minute=15, second=0)
                    _mins_to_open = int((_open_for_calc - _now_for_open).total_seconds() // 60)
                    if _mins_to_open > 0:
                        st.sidebar.info(f"💤 Market CLOSED — opens in {_fmt_countdown(_mins_to_open)}")
                    else:
                        st.sidebar.info("💤 Market CLOSED (after hours)")
                except Exception:
                    st.sidebar.info("💤 Market CLOSED (weekday)")
            else:
                st.sidebar.info("📅 Weekend — Market CLOSED")
        except Exception:
            pass

        # ── Section: Market Pulse ─────────────────────────────────────────────
        with st.sidebar.expander("📡 Market Pulse", expanded=True):
            # VIX with sparkline from recent equity_snapshots
            try:
                vix_val = snap.get("vix", 0) if snap else 0
                if vix_val and vix_val > 0:
                    vix_color = "🟢" if vix_val < 15 else ("🟡" if vix_val < 20 else "🔴")
                    st.metric(
                        f"{vix_color} India VIX",
                        f"{vix_val:.1f}",
                        help="<15 calm, 15-20 normal, >20 volatile",
                    )
                    # VIX sparkline: query last 10 vix readings from equity_snapshots
                    try:
                        import plotly.graph_objects as _go_vix
                        _vix_conn = db_connect()
                        _vix_df = query_df(
                            _vix_conn,
                            "SELECT ts, vix FROM equity_snapshots WHERE vix IS NOT NULL "
                            "ORDER BY ts DESC LIMIT 10",
                        )
                        if not _vix_df.empty and len(_vix_df) >= 2:
                            _vix_df = _vix_df.sort_values("ts").reset_index(drop=True)
                            _vix_series = _vix_df["vix"].tolist()
                            _spark_color = COLORS["profit"] if vix_val < 15 else (COLORS["warning"] if vix_val < 20 else COLORS["loss"])
                            _fig_spark = _go_vix.Figure()
                            _fig_spark.add_trace(_go_vix.Scatter(
                                y=_vix_series,
                                mode="lines",
                                line=dict(color=_spark_color, width=2),
                                fill="tozeroy",
                                fillcolor=_spark_color.replace(")", ",0.2)").replace("rgb", "rgba")
                                if _spark_color.startswith("rgb") else _spark_color + "33",
                            ))
                            _fig_spark.update_layout(
                                height=60,
                                margin=dict(l=0, r=0, t=0, b=0),
                                xaxis=dict(visible=False),
                                yaxis=dict(visible=False),
                                showlegend=False,
                                paper_bgcolor="rgba(0,0,0,0)",
                                plot_bgcolor="rgba(0,0,0,0)",
                            )
                            st.plotly_chart(_fig_spark, use_container_width=True,
                                            config={"displayModeBar": False})
                            st.caption(
                                f"VIX range (last {len(_vix_series)}): "
                                f"{min(_vix_series):.1f} – {max(_vix_series):.1f}"
                            )
                    except Exception:
                        pass  # sparkline is best-effort
            except Exception:
                pass

            # PCR
            try:
                if _PCR_AVAILABLE and _get_nifty_pcr is not None:
                    pcr = _get_nifty_pcr()
                    if pcr:
                        pcr_color = "🟢" if pcr > 1.0 else ("🔴" if pcr < 0.7 else "🟡")
                        st.metric(
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
                        st.metric("FII Sentiment", sent_display)
            except Exception:
                pass

            # Signal quality from last 30 trades
            try:
                _sb_conn = db_connect()
                _sb_trades_df = query_df(
                    _sb_conn,
                    "SELECT pnl FROM trades ORDER BY closed_at DESC LIMIT 30",
                )
                if not _sb_trades_df.empty and "pnl" in _sb_trades_df.columns and len(_sb_trades_df) > 0:
                    _sb_n = len(_sb_trades_df)
                    _sb_wins = int((_sb_trades_df["pnl"] > 0).sum())
                    _sb_win_rate = _sb_wins / _sb_n * 100
                    _sb_sig_color = "🟢" if _sb_win_rate > 50 else ("🟡" if _sb_win_rate > 35 else "🔴")
                    st.metric(
                        f"{_sb_sig_color} Signal Quality",
                        f"{_sb_win_rate:.0f}% win",
                        help=f"Last {_sb_n} trades win rate",
                    )
                else:
                    st.caption("No trades yet — signal quality N/A")
            except Exception:
                _sb_conn = None

            # Active filters status
            st.caption("Active Filters:")
            st.text("✅ VIX<18 trend gate")
            st.text("✅ Hurst H>0.5 persistence")
            st.text("✅ Market breadth 50%")

            # Last accepted signal
            try:
                if _sb_conn is not None:
                    _sb_last_sig_df = query_df(
                        _sb_conn,
                        "SELECT symbol, strategy, ts FROM signals WHERE accepted = 1 ORDER BY id DESC LIMIT 1",
                    )
                    if not _sb_last_sig_df.empty:
                        _sb_sym = str(_sb_last_sig_df["symbol"].iloc[0])
                        _sb_strat = str(_sb_last_sig_df["strategy"].iloc[0])
                        _sb_ts = str(_sb_last_sig_df["ts"].iloc[0])
                        _sb_ago = time_ago(_sb_ts)
                        st.caption(f"Last signal: {_sb_sym} {_sb_strat} {_sb_ago}")
                    else:
                        st.caption("Last signal: none recorded")
            except Exception:
                pass

        # ── Section: Agent Controls ───────────────────────────────────────────
        with st.sidebar.expander("⚙️ Agent Controls", expanded=True):
            mode = config.get("mode", "unknown").upper()
            mode_color = COLORS["loss"] if mode == "LIVE" else COLORS["profit"]
            st.markdown(f"**Mode:** <span style='color:{mode_color}'>{safe_html(mode)}</span>", unsafe_allow_html=True)

            capital = config.get("capital", {}).get("initial_inr", 0)
            st.markdown(f"**Capital:** ₹{capital:,.0f}")

            gate_passed = gate.get("passed", False)
            gate_color = COLORS["profit"] if gate_passed else COLORS["loss"]
            gate_label = "PASSED" if gate_passed else "FAILED"
            st.markdown(f"**Gate:** <span style='color:{gate_color}'>{gate_label}</span>", unsafe_allow_html=True)
            if gate.get("timestamp"):
                ts = gate["timestamp"][:19].replace("T", " ")
                st.caption(f"Last run: {ts}")

            risk_cfg = config.get("risk", {})
            st.markdown("**Risk config**")
            st.caption(f"Per-trade risk: {risk_cfg.get('per_trade_risk_pct', '-')}%")
            st.caption(f"Max positions: {risk_cfg.get('max_open_positions', '-')}")
            st.caption(f"Daily loss circuit: -{risk_cfg.get('daily_loss_circuit_pct', '-')}%")
            st.caption(f"Drawdown circuit: -{risk_cfg.get('drawdown_circuit_pct', '-')}%")

            st.divider()
            _running = _agent_is_running()
            _run_color = COLORS["profit"] if _running else COLORS["loss"]
            _run_label = "RUNNING" if _running else "STOPPED"
            st.markdown(f"**Agent:** <span style='color:{_run_color}'>{_run_label}</span>", unsafe_allow_html=True)

            # Show uptime if the agent process is alive
            if snap.get("pid"):
                try:
                    import psutil as _psutil_up
                    import time as _time_up
                    _uptime_proc = _psutil_up.Process(int(snap["pid"]))
                    _uptime_secs = _time_up.time() - _uptime_proc.create_time()
                    if _uptime_secs > 3600:
                        _uptime_str = f"{_uptime_secs / 3600:.1f}h"
                    else:
                        _uptime_str = f"{_uptime_secs / 60:.0f}m"
                    st.caption(f"Uptime: {_uptime_str}")
                except Exception:
                    pass

            # Start/Stop buttons
            if _running:
                if st.button("Stop Agent", type="secondary", key="sb_stop_agent", use_container_width=True):
                    st.toast(_stop_agent())
                    st.rerun()
            else:
                if st.button("Start Agent", type="primary", key="sb_start_agent", use_container_width=True):
                    st.toast(_start_agent())
                    st.rerun()

        # ── Section: Quick Stats ──────────────────────────────────────────────
        with st.sidebar.expander("📊 Quick Stats", expanded=False):
            try:
                _qs_conn = db_connect()

                # Trades this week
                _qs_week_df = query_df(
                    _qs_conn,
                    "SELECT id FROM trades WHERE closed_at >= datetime('now', '-7 days')",
                )
                st.metric("Trades this week", len(_qs_week_df))

                # Best performing strategy (last 7 days)
                _qs_strat_df = query_df(
                    _qs_conn,
                    "SELECT strategy, SUM(pnl) as total_pnl FROM trades "
                    "WHERE closed_at >= datetime('now', '-7 days') "
                    "GROUP BY strategy ORDER BY total_pnl DESC LIMIT 1",
                )
                if not _qs_strat_df.empty and "strategy" in _qs_strat_df.columns:
                    _qs_best_strat = str(_qs_strat_df["strategy"].iloc[0])
                    _qs_best_pnl = float(_qs_strat_df["total_pnl"].iloc[0])
                    st.metric(
                        "Best strategy (7d)",
                        _qs_best_strat,
                        delta=f"₹{_qs_best_pnl:+,.0f}",
                        delta_color="normal" if _qs_best_pnl >= 0 else "inverse",
                    )
                else:
                    st.caption("Best strategy (7d): N/A")

                # Market breadth placeholder
                _qs_breadth = snap.get("market_breadth_pct", None)
                if _qs_breadth is not None:
                    try:
                        st.metric("Market breadth", f"{float(_qs_breadth):.1f}%")
                    except Exception:
                        st.caption("Market breadth: N/A")
                else:
                    st.caption("Market breadth: N/A (placeholder)")

                # Today's rejected signals
                _qs_rejected_df = query_df(
                    _qs_conn,
                    "SELECT id FROM signals WHERE accepted = 0 AND date(ts) = date('now')",
                )
                st.metric("Rejected signals today", len(_qs_rejected_df))

            except Exception:
                st.caption("Stats unavailable")

        st.sidebar.divider()
        st.sidebar.caption("Keyboard shortcuts: R = refresh, D = dark mode")
        if st.button("Refresh now"):
            st.cache_data.clear()
            st.rerun()

        # Auto-refresh: rerun after 30s if toggle is on
        if auto_refresh:
            import time as _sidebar_time
            _sidebar_time.sleep(30)
            st.rerun()

    return dark_mode
