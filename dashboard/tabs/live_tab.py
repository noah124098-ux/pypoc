"""Paper Agent tab — live command center.

Sections:
  1. TOP STATUS BAR  — 6-column KPI row (equity, day P&L, signal quality,
                        positions, regime badge, market status + countdown)
  2. NIFTY SPARKLINE  — mini inline sparkline of Nifty LTP / 15-min change
  3. ACTIVE FILTERS   — single-line compact filter status
  4. TRADE FEED       — last 5 events from signals + trades (live log)
  5. OPEN POSITIONS   — per-position cards with [Replay] + [Close] quick-actions
  6. ANGEL ONE PANEL  — live broker account view (data-only)
  7. FII/DII FLOWS    — institutional flow expander
"""
from __future__ import annotations

import html as _html
import json
import os
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from dashboard.components.kpi_row import _agent_is_running
from dashboard.design import COLORS, _badge, _metric_card, _section, regime_hex
from dashboard.utils.charts import safe_html, color_pnl, is_market_hours, fmt_inr, time_ago
from dashboard.utils.db import query_df

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

AGENT_PID_PATH = Path("data/agent.pid")
COMMANDS_PATH = Path("data/commands.jsonl")


# ---------------------------------------------------------------------------
# Angel One credentials helpers (unchanged)
# ---------------------------------------------------------------------------

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

        try:
            pos_resp = smart.getPosition()
            if pos_resp.get("status") and pos_resp.get("data"):
                result["positions"] = pos_resp["data"]
        except Exception as e:
            result["positions_error"] = str(e)

        try:
            rms_resp = smart.rmsLimit()
            if rms_resp.get("status") and rms_resp.get("data"):
                result["funds"] = rms_resp["data"]
        except Exception as e:
            result["funds_error"] = str(e)

        try:
            order_resp = smart.orderBook()
            if order_resp.get("status") and order_resp.get("data"):
                result["orders"] = order_resp["data"]
        except Exception as e:
            result["orders_error"] = str(e)

    except Exception as e:
        result["error"] = str(e)
    return result


# ---------------------------------------------------------------------------
# Section 1 — Top status bar (6 KPI columns)
# ---------------------------------------------------------------------------

def _render_top_status_bar(snap: dict, config: dict, conn) -> None:
    """6-column full-width status bar at the very top of the Live tab."""
    capital = config.get("capital", {}).get("initial_inr", 0)

    equity = float(snap.get("equity", capital or 0))
    start_today = float(snap.get("starting_equity_today", equity) or equity)
    day_pnl = equity - start_today
    day_pnl_pct = (day_pnl / start_today * 100) if start_today else 0.0

    n_open = len(snap.get("open_positions", []))
    max_pos = int(config.get("risk", {}).get("max_open_positions", 5))
    regime = snap.get("regime") or snap.get("current_regime") or "UNKNOWN"

    # Rolling 20-trade win rate for Signal Quality
    trades_df = query_df(conn, "SELECT pnl FROM trades ORDER BY closed_at DESC LIMIT 20")
    if not trades_df.empty and "pnl" in trades_df.columns and len(trades_df) > 0:
        n_wins = int((trades_df["pnl"] > 0).sum())
        sig_win_rate = n_wins / len(trades_df) * 100
        sig_label = f"{sig_win_rate:.0f}% ({n_wins}/{len(trades_df)})"
        sig_color = COLORS["profit"] if sig_win_rate >= 50 else (
            COLORS["warning"] if sig_win_rate >= 35 else COLORS["loss"])
    else:
        sig_win_rate = None
        sig_label = "N/A"
        sig_color = COLORS["neutral"]

    # Market status + countdown
    try:
        if _PYTZ_AVAILABLE and _pytz is not None:
            _ist = _pytz.timezone("Asia/Kolkata")
            _now_ist = datetime.now(_ist).replace(tzinfo=None)
        else:
            _now_ist = datetime.utcnow() + timedelta(hours=5, minutes=30)
        _weekday = _now_ist.weekday()
        _t = _now_ist.time()
        _market_open = _now_ist.replace(hour=9, minute=15, second=0, microsecond=0)
        _market_close = _now_ist.replace(hour=15, minute=30, second=0, microsecond=0)
        if _weekday < 5 and _market_open <= _now_ist <= _market_close:
            _mins_to_close = max(0, int((_market_close - _now_ist).total_seconds() // 60))
            _h, _m = divmod(_mins_to_close, 60)
            _countdown = f"{_h}h {_m}m" if _h else f"{_m}m"
            _market_status = "OPEN"
            _market_detail = f"closes in {_countdown}"
            _market_bg = COLORS["profit"]
        elif _weekday < 5 and _now_ist < _market_open:
            _mins_to_open = max(0, int((_market_open - _now_ist).total_seconds() // 60))
            _h, _m = divmod(_mins_to_open, 60)
            _countdown = f"{_h}h {_m}m" if _h else f"{_m}m"
            _market_status = "CLOSED"
            _market_detail = f"opens in {_countdown}"
            _market_bg = COLORS["warning"]
        else:
            _market_status = "CLOSED"
            _market_detail = "weekend" if _weekday >= 5 else "after hours"
            _market_bg = COLORS["neutral"]
    except Exception:
        _market_status = "UNKNOWN"
        _market_detail = ""
        _market_bg = COLORS["neutral"]

    # Yesterday equity delta (approximate: use capital as baseline when no yesterday snapshot)
    equity_delta_str = f"{day_pnl:+,.0f} today" if start_today else None

    c1, c2, c3, c4, c5, c6 = st.columns([1, 1, 1, 1, 1, 1])

    # col1: Equity
    c1.metric(
        "Equity",
        fmt_inr(equity),
        delta=equity_delta_str,
        delta_color="normal" if day_pnl >= 0 else "inverse",
        help="Paper-agent equity from snapshot.json",
    )

    # col2: Day P&L
    c2.metric(
        "Day P&L",
        fmt_inr(day_pnl),
        delta=f"{day_pnl_pct:+.2f}%",
        delta_color="normal" if day_pnl >= 0 else "inverse",
        help="Equity change since market open today",
    )

    # col3: Signal Quality (rolling 20-trade win rate, color coded)
    c3.markdown(
        f"<div style='text-align:center'>"
        f"<small style='color:#888;font-size:0.8em'>Signal Quality (20T)</small><br>"
        f"<span style='color:{sig_color};font-weight:700;font-size:1.15em'>"
        f"{_html.escape(sig_label)}</span>"
        f"</div>",
        unsafe_allow_html=True,
    )

    # col4: Open Positions n/max
    pos_color = COLORS["profit"] if n_open < max_pos else COLORS["warning"]
    c4.markdown(
        f"<div style='text-align:center'>"
        f"<small style='color:#888;font-size:0.8em'>Open Positions</small><br>"
        f"<span style='color:{pos_color};font-weight:700;font-size:1.15em'>"
        f"{n_open} / {max_pos}</span>"
        f"</div>",
        unsafe_allow_html=True,
    )

    # col5: Regime badge
    regime_bg = regime_hex(regime)
    c5.markdown(
        f"<div style='text-align:center'>"
        f"<small style='color:#888;font-size:0.8em'>Regime</small><br>"
        f"<span style='background:{regime_bg};color:white;padding:5px 14px;"
        f"border-radius:14px;font-weight:700;font-size:1.05em;display:inline-block;margin-top:4px'>"
        f"{_html.escape(regime)}</span>"
        f"</div>",
        unsafe_allow_html=True,
    )

    # col6: Market status
    c6.markdown(
        f"<div style='text-align:center'>"
        f"<small style='color:#888;font-size:0.8em'>Market</small><br>"
        f"<span style='background:{_market_bg};color:white;padding:5px 14px;"
        f"border-radius:14px;font-weight:700;font-size:1.05em;display:inline-block;margin-top:4px'>"
        f"{_html.escape(_market_status)}</span>"
        + (f"<br><small style='color:#aaa;font-size:0.75em'>{_html.escape(_market_detail)}</small>" if _market_detail else "")
        + f"</div>",
        unsafe_allow_html=True,
    )

    st.markdown("<hr style='margin:8px 0 4px 0;border-color:#333'>", unsafe_allow_html=True)


# ---------------------------------------------------------------------------
# Section 2 — Nifty mini sparkline widget
# ---------------------------------------------------------------------------

def _render_nifty_sparkline(snap: dict, conn) -> None:
    """Compact Nifty LTP widget with sparkline from equity_snapshots."""
    nifty_ltp = snap.get("nifty_ltp")
    if not nifty_ltp:
        return

    nifty_ltp = float(nifty_ltp)
    vix = snap.get("vix")

    # Try to pull recent nifty_ltp values from equity_snapshots
    nifty_df = query_df(
        conn,
        "SELECT ts, nifty_ltp FROM equity_snapshots WHERE nifty_ltp IS NOT NULL ORDER BY ts DESC LIMIT 30",
    )
    has_history = not nifty_df.empty and len(nifty_df) >= 2

    col_spark, col_info = st.columns([2, 1])

    with col_spark:
        if has_history:
            nifty_df = nifty_df.sort_values("ts").reset_index(drop=True)
            series = nifty_df["nifty_ltp"].tolist()
            first_val = series[0]
            change_pct = (nifty_ltp - first_val) / first_val * 100 if first_val else 0.0
            spark_color = COLORS["profit"] if change_pct >= 0 else COLORS["loss"]
            fig = go.Figure()
            fig.add_trace(go.Scatter(
                y=series,
                mode="lines",
                line=dict(color=spark_color, width=2),
                fill="tozeroy",
                fillcolor=spark_color + "22",
            ))
            fig.update_layout(
                height=60,
                margin=dict(l=0, r=0, t=0, b=0),
                xaxis=dict(visible=False),
                yaxis=dict(visible=False),
                showlegend=False,
                paper_bgcolor="rgba(0,0,0,0)",
                plot_bgcolor="rgba(0,0,0,0)",
            )
            st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})
            st.caption(
                f"Nifty LTP: {nifty_ltp:,.0f}  "
                f"{'▲' if change_pct >= 0 else '▼'} {change_pct:+.2f}% vs session open  "
                + (f"| VIX {vix:.1f}" if vix else "")
            )
        else:
            arrow = ""
            st.markdown(
                f"<span style='font-weight:700;font-size:1.1em'>Nifty: {nifty_ltp:,.0f}</span>"
                + (f" &nbsp; VIX: {float(vix):.1f}" if vix else ""),
                unsafe_allow_html=True,
            )
            st.caption("No intraday history yet — sparkline will appear once data accumulates")

    with col_info:
        # Snapshot age
        if snap.get("ts"):
            try:
                snap_ts = datetime.fromisoformat(str(snap["ts"]).replace("Z", ""))
                snap_age = (datetime.now() - snap_ts).total_seconds()
                offline_note = " — agent may be offline" if snap_age > 120 else ""
                age_color = COLORS["profit"] if snap_age < 30 else (
                    COLORS["warning"] if snap_age < 120 else COLORS["loss"])
                st.markdown(
                    f"<small style='color:{age_color}'>Snapshot: {snap_age:.0f}s ago{offline_note}</small>",
                    unsafe_allow_html=True,
                )
            except Exception:
                pass

        agent_running = _agent_is_running()
        status_color = COLORS["profit"] if agent_running else COLORS["loss"]
        status_label = "Agent RUNNING" if agent_running else "Agent STOPPED"
        halted = snap.get("halted", False)
        if halted:
            status_color = COLORS["warning"]
            halt_reason = _html.escape(str(snap.get("halt_reason", "") or ""))
            status_label = f"Agent HALTED ({halt_reason})" if halt_reason else "Agent HALTED"
        st.markdown(
            f"<small style='color:{status_color};font-weight:600'>{status_label}</small>",
            unsafe_allow_html=True,
        )


# ---------------------------------------------------------------------------
# Section 3 — Active filters compact status line
# ---------------------------------------------------------------------------

def _render_filter_status(snap: dict, config: dict) -> None:
    """Single-line compact filter status replacing verbose badge grid."""
    vix = snap.get("vix")
    breadth = snap.get("breadth_pct")
    risk_cfg = config.get("risk", {})
    vix_gate_threshold = float(risk_cfg.get("vix_gate_threshold", 18.0))

    parts: list[str] = []

    # VIX gate
    _c_warn = COLORS["warning"]
    _c_profit = COLORS["profit"]
    if vix is not None:
        try:
            vix_f = float(vix)
            if vix_f >= vix_gate_threshold:
                parts.append(f"<span style='color:{_c_warn}'>&#9888; VIX gate active (VIX={vix_f:.1f})</span>")
            else:
                parts.append(f"<span style='color:{_c_profit}'>VIX &#10003; ({vix_f:.1f})</span>")
        except Exception:
            parts.append("VIX ?")
    else:
        parts.append("VIX ?")

    # Hurst (pulled from extras if available)
    hurst_ok = snap.get("extras", {}).get("hurst_ok", True)
    parts.append(
        f"<span style='color:{_c_profit}'>Hurst &#10003;</span>"
        if hurst_ok
        else f"<span style='color:{_c_warn}'>Hurst &#9888;</span>"
    )

    # Market breadth
    _c_loss = COLORS["loss"]
    if breadth is not None:
        try:
            breadth_f = float(breadth)
            if breadth_f >= 50:
                parts.append(f"<span style='color:{_c_profit}'>Breadth &#10003; ({breadth_f:.0f}%)</span>")
            else:
                parts.append(f"<span style='color:{_c_warn}'>Breadth &#9888; ({breadth_f:.0f}%)</span>")
        except Exception:
            parts.append("Breadth ?")
    else:
        parts.append("Breadth ?")

    # FII
    if _FII_AVAILABLE:
        try:
            sent = get_institutional_sentiment()
            if sent == "BEARISH":
                parts.append(f"<span style='color:{_c_loss}'>FII &#9888; BEARISH</span>")
            elif sent == "BULLISH":
                parts.append(f"<span style='color:{_c_profit}'>FII &#10003; Bullish</span>")
            else:
                parts.append("<span style='color:#888'>FII Neutral</span>")
        except Exception:
            pass

    line = " &nbsp;|&nbsp; ".join(parts)
    st.markdown(
        f"<div style='padding:4px 0;font-size:0.9em'>"
        f"<span style='color:#888'>Filters: </span>{line}"
        f"</div>",
        unsafe_allow_html=True,
    )


# ---------------------------------------------------------------------------
# Section 4 — Trade feed (last 5 events from signals + trades)
# ---------------------------------------------------------------------------

def _render_trade_feed(conn) -> None:
    """Live event feed: last 5 signals/trades merged and sorted by time."""
    if conn is None:
        st.caption("No database — trade feed unavailable.")
        return

    events: list[dict] = []

    # Recent signals (accepted + rejected)
    sig_df = query_df(
        conn,
        "SELECT ts, symbol, strategy, accepted, rejection_reason, regime "
        "FROM signals ORDER BY id DESC LIMIT 10",
    )
    if not sig_df.empty:
        for _, row in sig_df.iterrows():
            ts_str = str(row.get("ts", ""))
            symbol = str(row.get("symbol", ""))
            strategy = str(row.get("strategy", ""))
            accepted = bool(row.get("accepted", False))
            rejection = str(row.get("rejection_reason") or "")
            regime = str(row.get("regime") or "")
            if accepted:
                dot = "🟢"
                detail = f"{symbol} BUY signal accepted ({strategy}" + (f", {regime}" if regime and regime != "UNKNOWN" else "") + ")"
            else:
                dot = "🔴"
                detail = f"{symbol} signal rejected" + (f" ({rejection})" if rejection else "")
            events.append({"ts": ts_str, "dot": dot, "detail": detail, "sort_key": ts_str})

    # Recent closed trades
    trade_df = query_df(
        conn,
        "SELECT closed_at, symbol, pnl, exit_reason, strategy FROM trades ORDER BY id DESC LIMIT 10",
    )
    if not trade_df.empty:
        for _, row in trade_df.iterrows():
            ts_str = str(row.get("closed_at", ""))
            symbol = str(row.get("symbol", ""))
            pnl = float(row.get("pnl", 0) or 0)
            exit_reason = str(row.get("exit_reason") or "CLOSED")
            strategy = str(row.get("strategy") or "")
            dot = "⚫"
            pnl_str = f"₹{pnl:+,.0f}"
            detail = f"{symbol} order FILLED ({exit_reason}) {pnl_str}" + (f" [{strategy}]" if strategy else "")
            events.append({"ts": ts_str, "dot": dot, "detail": detail, "sort_key": ts_str})

    if not events:
        st.caption("No events yet — feed will populate as the agent runs.")
        return

    # Sort by time descending, take top 5
    events_sorted = sorted(events, key=lambda e: e["sort_key"], reverse=True)[:5]

    rows_html = ""
    for ev in events_sorted:
        ts_display = ev["ts"][11:19] if len(ev["ts"]) >= 19 else ev["ts"][:16]
        detail_safe = _html.escape(ev["detail"])
        rows_html += (
            f"<div style='padding:3px 0;font-family:monospace;font-size:0.88em'>"
            f"{ev['dot']} "
            f"<span style='color:#888'>{_html.escape(ts_display)}</span>"
            f"&nbsp;&nbsp;{detail_safe}"
            f"</div>"
        )

    st.markdown(
        f"<div style='background:#12151e;border:1px solid #2a2f42;border-radius:6px;"
        f"padding:8px 12px;max-height:160px;overflow-y:auto'>"
        f"{rows_html}"
        f"</div>",
        unsafe_allow_html=True,
    )


# ---------------------------------------------------------------------------
# Section 5 — Open positions with quick-action buttons
# ---------------------------------------------------------------------------

def _render_open_positions(snap: dict) -> None:
    """Render open positions with [Replay] and [Close] quick-action buttons."""
    positions_list = snap.get("open_positions", [])

    if not positions_list:
        st.info(
            "Paper agent has no open positions."
            + (" (Agent not running)" if not snap else "")
        )
        return

    for idx, pos in enumerate(positions_list):
        symbol = str(pos.get("symbol", ""))
        side = str(pos.get("side", "BUY"))
        qty = int(pos.get("qty", 0))
        avg_price = float(pos.get("avg_price", 0))
        last_price = float(pos.get("last_price", avg_price))
        unrealized_pnl = float(pos.get("unrealized_pnl", (last_price - avg_price) * qty))
        stop_loss = pos.get("stop_loss")
        target = pos.get("target")
        strategy = str(pos.get("strategy", ""))
        opened_at = str(pos.get("opened_at", ""))

        pnl_color = COLORS["profit"] if unrealized_pnl >= 0 else COLORS["loss"]
        pnl_sign = "+" if unrealized_pnl >= 0 else ""

        with st.container():
            col_info, col_metrics, col_actions = st.columns([2, 2, 1])

            with col_info:
                st.markdown(
                    f"<div style='padding:4px 0'>"
                    f"<span style='font-weight:700;font-size:1.05em'>{_html.escape(symbol)}</span>"
                    f"&nbsp;<span style='color:#888;font-size:0.85em'>{_html.escape(side)} x {qty}</span>"
                    + (f"&nbsp;<span style='color:#aaa;font-size:0.8em'>[{_html.escape(strategy)}]</span>" if strategy else "")
                    + f"<br><span style='color:#666;font-size:0.78em'>avg ₹{avg_price:,.2f} | ltp ₹{last_price:,.2f}"
                    + (f" | opened {opened_at[11:16]}" if len(opened_at) >= 16 else "")
                    + f"</span></div>",
                    unsafe_allow_html=True,
                )

            with col_metrics:
                pnl_display = f"₹{pnl_sign}{unrealized_pnl:,.0f}"
                pnl_pct = (unrealized_pnl / (avg_price * qty) * 100) if avg_price and qty else 0.0
                st.markdown(
                    f"<span style='color:{pnl_color};font-weight:700'>{_html.escape(pnl_display)}"
                    f" ({pnl_pct:+.1f}%)</span>",
                    unsafe_allow_html=True,
                )
                if stop_loss:
                    st.caption(f"SL ₹{float(stop_loss):,.2f}" + (f" | TGT ₹{float(target):,.2f}" if target else ""))

            with col_actions:
                # [Replay] button — sets session state to pre-select symbol in Trade Replay tab
                btn_key_replay = f"_pos_replay_{idx}_{symbol}"
                if st.button("Replay", key=btn_key_replay, help=f"Open Trade Replay for {symbol}", use_container_width=True):
                    st.session_state["replay_preselect_symbol"] = symbol
                    st.info(f"Switch to the Trade Replay tab to review {symbol} trades.", icon="ℹ️")

                # [Close] button — confirmation dialog via session state
                btn_key_close = f"_pos_close_{idx}_{symbol}"
                confirm_key = f"_pos_close_confirm_{idx}_{symbol}"

                if st.session_state.get(confirm_key):
                    # Confirmation stage
                    st.warning(f"Close {symbol}?", icon="⚠️")
                    col_yes, col_no = st.columns(2)
                    if col_yes.button("Yes", key=f"{btn_key_close}_yes", type="primary", use_container_width=True):
                        try:
                            from core.command_queue import enqueue as _enqueue
                            _enqueue("place_paper_order", {
                                "symbol": symbol,
                                "side": "SELL" if side == "BUY" else "BUY",
                                "qty": qty,
                                "order_type": "MARKET",
                                "reason": "manual_close_from_dashboard",
                            })
                            st.success(f"Close order queued for {symbol}.")
                        except Exception as exc:
                            st.error(f"Failed to queue close: {exc}")
                        st.session_state.pop(confirm_key, None)
                        st.rerun()
                    if col_no.button("No", key=f"{btn_key_close}_no", use_container_width=True):
                        st.session_state.pop(confirm_key, None)
                        st.rerun()
                else:
                    if st.button("Close", key=btn_key_close, help=f"Queue manual close for {symbol}", use_container_width=True):
                        st.session_state[confirm_key] = True
                        st.rerun()

        st.markdown("<hr style='margin:4px 0;border-color:#2a2f42'>", unsafe_allow_html=True)


# ---------------------------------------------------------------------------
# Main render
# ---------------------------------------------------------------------------

def render(snap: dict, config: dict, conn) -> None:
    """Render the Paper Agent tab — live command center."""
    st.header("Paper Agent — Live Command Center")

    # ── 1. TOP STATUS BAR ────────────────────────────────────────────────────
    _render_top_status_bar(snap, config, conn)

    # ── 2. NIFTY SPARKLINE + agent status ────────────────────────────────────
    _render_nifty_sparkline(snap, conn)

    st.divider()

    # ── 3. ACTIVE FILTERS (compact, one line) ────────────────────────────────
    _render_filter_status(snap, config)

    st.divider()

    # ── 4. TRADE FEED ─────────────────────────────────────────────────────────
    with st.expander("Live Trade Feed (last 5 events)", expanded=True):
        _render_trade_feed(conn)

    st.divider()

    # ── 5. OPEN POSITIONS WITH QUICK-ACTIONS ─────────────────────────────────
    positions_list = snap.get("open_positions", [])
    st.subheader(f"Open Positions ({len(positions_list)})")
    _render_open_positions(snap)

    st.divider()

    # ── Auto-refresh countdown (market hours) ────────────────────────────────
    _in_market = is_market_hours()
    if _in_market:
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
        try:
            _now_ist = datetime.utcnow() + timedelta(hours=5, minutes=30)
            st.caption(
                f"Market closed — auto-refresh paused. "
                f"Current IST: {_now_ist.strftime('%H:%M:%S')} | "
                f"Market hours: Mon-Fri 09:15-15:30 IST"
            )
        except Exception:
            pass

    st.divider()

    # ── 6. ANGEL ONE ACCOUNT PANEL ───────────────────────────────────────────
    api_key, client_code, password, totp_secret = _load_env_creds()
    creds_present = all([api_key, client_code, password, totp_secret])

    with st.expander(
        "Angel One Account" + (" (connected)" if creds_present else " (not configured)"),
        expanded=False,
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

        # Re-read creds after potential form submit
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
                conn_color = COLORS["profit"] if ao["connected"] else COLORS["loss"]
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
                    f4.metric("M2M Unrealised", f"₹{m2m:,.0f}",
                              delta_color="normal" if m2m >= 0 else "inverse")
                    st.divider()

                # Live positions from Angel One
                positions = ao.get("positions", [])
                st.subheader(f"Angel One Positions ({len(positions)} open)")
                if positions:
                    pos_df = pd.DataFrame(positions)
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
                        styled = styled.applymap(color_pnl, subset=pnl_cols)
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
                            return f"color: {COLORS['profit']}"
                        if str(val).upper() in ("REJECTED", "CANCELLED"):
                            return f"color: {COLORS['loss']}"
                        return f"color: {COLORS['warning']}"

                    ord_styled = ord_df[display_ord].style
                    if "status" in display_ord:
                        ord_styled = ord_styled.applymap(_order_status_style, subset=["status"])
                    st.dataframe(ord_styled, use_container_width=True, height=300)
                else:
                    st.info("No orders today.")

    # ── 7. FII/DII FLOWS ──────────────────────────────────────────────────────
    with st.expander("Institutional Flows (FII/DII)", expanded=False):
        if _FII_AVAILABLE:
            flows = get_fii_dii_flows(days=5)
            sentiment = get_institutional_sentiment()

            if sentiment == "BULLISH":
                st.success("FII BULLISH — Institutions are net buyers (avg >₹500cr/day)")
            elif sentiment == "BEARISH":
                st.error("FII BEARISH — Institutions are net sellers (avg <-₹500cr/day)")
                st.warning("FII BEARISH — TREND BUY signals suppressed by institutional sentiment gate")
            else:
                st.info("FII NEUTRAL — Mixed institutional flows")

            if flows:
                df = pd.DataFrame([{
                    "Date": f.date,
                    "FII Net (₹Cr)": f"{f.fii_net_inr_cr:+,.0f}",
                    "DII Net (₹Cr)": f"{f.dii_net_inr_cr:+,.0f}",
                    "Combined (₹Cr)": f"{f.combined_net:+,.0f}",
                } for f in flows])
                st.dataframe(df, hide_index=True, use_container_width=True)
            else:
                st.caption("No flow data available (market may be closed)")
        else:
            st.caption("FII/DII module not available")
