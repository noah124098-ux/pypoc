"""Angel One Account Portfolio tab."""
from __future__ import annotations

import streamlit as st

try:
    from core.data.angelone_portfolio import fetch_live_portfolio as _fetch_live_portfolio, AccountSummary as _AccountSummary
    _AO_PORTFOLIO_AVAILABLE = True
except ImportError:
    _AO_PORTFOLIO_AVAILABLE = False
    _fetch_live_portfolio = None  # type: ignore[assignment]
    _AccountSummary = None  # type: ignore[assignment]


def _load_env_creds() -> tuple[str, str, str, str]:
    """Load Angel One credentials: session-state UI inputs take precedence over .env."""
    import os
    from dotenv import load_dotenv
    load_dotenv(override=False)
    ss = st.session_state
    return (
        ss.get("ao_api_key") or os.getenv("ANGEL_ONE_API_KEY", ""),
        ss.get("ao_client_code") or os.getenv("ANGEL_ONE_CLIENT_CODE", ""),
        ss.get("ao_password") or os.getenv("ANGEL_ONE_PASSWORD", ""),
        ss.get("ao_totp_secret") or os.getenv("ANGEL_ONE_TOTP_SECRET", ""),
    )


def render(snap: dict) -> None:
    """Render the Angel One Account Portfolio tab."""
    st.header("Angel One Account Portfolio")

    # Read-only badge
    st.markdown(
        "<span style='background:#e67e22;color:white;padding:4px 12px;"
        "border-radius:4px;font-weight:bold;font-size:0.85em'>"
        "Read-Only — Orders are placed by the paper agent only</span>",
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
            # Top metrics row
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

            # Positions table
            _pos_list = _port_summary.positions if _port_summary.positions else []
            st.subheader(f"Open Positions ({len(_pos_list)})")
            if _pos_list:
                import pandas as pd
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

            # vs Paper Agent comparison
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

        if _port_auto_refresh:
            st.caption("Click Refresh to update portfolio data.")
            st.button(
                "Refresh Portfolio",
                key="refresh_portfolio",
                on_click=lambda: st.rerun(),
            )
