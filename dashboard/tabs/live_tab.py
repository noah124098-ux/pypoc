"""Paper Agent tab — live state, Angel One connection, open positions."""
from __future__ import annotations

import html as _html
import os
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
import streamlit as st

from dashboard.components.kpi_row import render_kpi_row, _agent_is_running
from dashboard.utils.charts import safe_html, color_pnl, is_market_hours

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


def render(snap: dict, config: dict, conn) -> None:
    """Render the Paper Agent tab."""
    st.header("Paper Agent — Live State")

    render_kpi_row(snap, config, conn)

    # Snapshot staleness inline caption
    if snap and snap.get("ts"):
        try:
            _live_snap_ts = datetime.fromisoformat(str(snap["ts"]).replace("Z", ""))
            _live_snap_age = (datetime.now() - _live_snap_ts).total_seconds()
            _live_offline_note = " — agent may be offline" if _live_snap_age > 120 else ""
            st.caption(f"Data from {_live_snap_age:.0f}s ago{_live_offline_note}")
        except Exception:
            pass

    # Auto-refresh countdown during market hours
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
        _now_utc = datetime.utcnow()
        _now_ist = _now_utc + timedelta(hours=5, minutes=30)
        st.caption(
            f"Market closed — auto-refresh paused. "
            f"Current IST: {_now_ist.strftime('%H:%M:%S')} | "
            f"Market hours: Mon-Fri 09:15-15:30 IST"
        )

    st.divider()

    # Agent status display (read-only — controls are in the sidebar)
    agent_running = _agent_is_running()
    status_color = "#2ecc71" if agent_running else "#e74c3c"
    status_label = "RUNNING" if agent_running else "STOPPED"
    st.markdown(
        f"<div style='background:{status_color};padding:10px;border-radius:6px;"
        f"display:inline-block;min-width:160px;text-align:center'>"
        f"<b style='color:white'>Agent: {status_label}</b></div>",
        unsafe_allow_html=True,
    )
    st.info("Agent controls are in the sidebar ↑")

    st.divider()

    # Angel One credential form
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
            styled = styled.applymap(color_pnl, subset=pnl_cols)
        st.dataframe(styled, use_container_width=True)
    else:
        st.info("Paper agent has no open positions." + (" (Agent not running)" if not snap else ""))

    st.divider()

    # Market Intelligence: FII/DII Flows
    with st.expander("📊 Institutional Flows (FII/DII)", expanded=False):
        if _FII_AVAILABLE:
            flows = get_fii_dii_flows(days=5)
            sentiment = get_institutional_sentiment()

            if sentiment == "BULLISH":
                st.success("🟢 FII BULLISH — Institutions are net buyers (avg >₹500cr/day)")
            elif sentiment == "BEARISH":
                st.error("🔴 FII BEARISH — Institutions are net sellers (avg <-₹500cr/day)")
                st.warning("FII BEARISH — TREND BUY signals suppressed by institutional sentiment gate")
            else:
                st.info("⚪ FII NEUTRAL — Mixed institutional flows")

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
