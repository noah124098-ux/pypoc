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

import plotly.io as pio
import streamlit as st

st.set_page_config(
    page_title="pypoc | NSE Agent",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="expanded",
)

from dashboard.utils.db import db_connect, DB_PATH
from dashboard.utils.snapshot import read_snapshot, read_gate, read_config
from dashboard.components.sidebar import render_sidebar
from dashboard.tabs import (
    live_tab,
    portfolio_tab,
    pnl_tab,
    positions_tab,
    replay_tab,
    regime_tab,
    backtest_tab,
    ai_review_tab,
    controls_tab,
    costs_tab,
)

# ── dark mode CSS injection ────────────────────────────────────────────────────

DARK_CSS = """
<style>
/* Main background */
.stApp { background-color: #0e1117; color: #fafafa; }
/* Sidebar */
[data-testid="stSidebar"] { background-color: #1a1f2e; }
/* Cards and containers */
[data-testid="stMetric"] { background-color: #1e2130; border-radius: 8px; padding: 8px; }
[data-testid="stExpander"] { background-color: #1e2130; }
/* Dataframes */
.stDataFrame { background-color: #1e2130; }
/* Input widgets */
.stTextInput input, .stNumberInput input, .stSelectbox select {
  background-color: #262b3d; color: #fafafa; border-color: #3d4460;
}
/* Buttons */
.stButton button { background-color: #2d3250; color: #fafafa; border-color: #4a5080; }
.stButton button:hover { background-color: #3d4460; }
/* Success/warning/error boxes */
.stSuccess { background-color: #1a3a1a; }
.stWarning { background-color: #3a2a1a; }
.stError { background-color: #3a1a1a; }
/* Tab bar */
.stTabs [data-baseweb="tab-list"] { background-color: #1a1f2e; }
.stTabs [data-baseweb="tab"] { color: #aaaaaa; }
.stTabs [aria-selected="true"] { color: #4fc3f7; border-bottom-color: #4fc3f7; }
/* Headers */
h1, h2, h3, h4 { color: #e0e0e0; }
/* Caption / small text */
.caption { color: #888888; }
/* Form elements */
[data-testid="stForm"] { background-color: #1e2130; border-color: #3d4460; }
/* Badges / code */
code { background-color: #2d3250; color: #4fc3f7; }
/* Dividers */
hr { border-color: #3d4460; }
</style>
"""

LIGHT_CSS = """<style>.stApp { background-color: #ffffff; }</style>"""


def main() -> None:
    """Main entry point - render sidebar then all tabs."""
    _load_progress = st.progress(0, text="Loading dashboard data…")
    snap = read_snapshot()
    _load_progress.progress(30, text="Reading gate & config…")
    gate = read_gate()
    config = read_config()
    _load_progress.progress(70, text="Connecting to database…")

    dark_mode = render_sidebar(snap, config, gate)

    if st.session_state.get("dark_mode", False):
        st.markdown(DARK_CSS, unsafe_allow_html=True)
        pio.templates.default = "plotly_dark"
    else:
        st.markdown(LIGHT_CSS, unsafe_allow_html=True)
        pio.templates.default = "plotly"

    conn = db_connect()
    _load_progress.progress(100, text="Ready.")
    _load_progress.empty()

    (
        tab_live,
        tab_portfolio,
        tab_pnl,
        tab_positions,
        tab_replay,
        tab_regime,
        tab_backtest,
        tab_ai_review,
        tab_controls,
        tab_costs,
    ) = st.tabs([
        "🟢 Paper Agent",
        "🏦 Angel One Account",
        "📊 P&L & Equity",
        "📋 Positions & Signals",
        "🎬 Trade Replay",
        "🌡️ Regime & Market",
        "🔬 Backtest Results",
        "🤖 AI Review",
        "⚙️ Controls",
        "💰 Costs",
    ])

    with tab_live:
        live_tab.render(snap, config, conn)

    with tab_portfolio:
        portfolio_tab.render(snap)

    with tab_pnl:
        pnl_tab.render(snap, config, conn)

    with tab_positions:
        positions_tab.render(snap, config, conn)

    with tab_replay:
        replay_tab.render()

    with tab_regime:
        regime_tab.render(snap, conn)

    with tab_backtest:
        backtest_tab.render(snap, config, gate, conn)

    with tab_ai_review:
        ai_review_tab.render(snap, conn)

    with tab_controls:
        controls_tab.render(snap, config, gate, conn)

    with tab_costs:
        costs_tab.render(snap, config, conn)

    # Auto-refresh
    if st.session_state.get("auto_refresh", False):
        import time
        time.sleep(30)
        st.rerun()


main()
