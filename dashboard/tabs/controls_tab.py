"""Controls tab — agent controls, risk limits, circuit breakers, notifications."""
from __future__ import annotations

import html as _html
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
import streamlit as st

from dashboard.utils.charts import safe_html, time_ago
from dashboard.utils.db import query_df
from dashboard.utils.snapshot import read_config, write_config_risk

SNAPSHOT_PATH = Path("data/snapshot.json")
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


def render(snap: dict, config: dict, gate: dict, conn) -> None:
    """Render the Agent Controls & Risk Limits tab."""
    st.header("Agent Controls & Risk Limits")

    # Agent start/stop
    st.subheader("Agent Process")
    ctrl_running = _agent_is_running()
    _ctrl_halted = snap.get("halted", False)
    _ctrl_halt_reason = snap.get("halt_reason", "")
    _ctrl_regime = snap.get("regime", "UNKNOWN")
    _ctrl_last_sig_df = query_df(conn, "SELECT ts FROM signals ORDER BY id DESC LIMIT 1")
    _ctrl_last_sig = (
        time_ago(str(_ctrl_last_sig_df["ts"].iloc[0]))
        if not _ctrl_last_sig_df.empty and "ts" in _ctrl_last_sig_df.columns
        else "No signals"
    )

    from dashboard.utils.charts import is_market_hours
    _ctrl_in_market = is_market_hours()

    if _ctrl_halted:
        _ctrl_status_label = "HALTED"
        _ctrl_status_bg = "#c0392b"
        _ctrl_status_detail = f"Halt reason: {safe_html(_ctrl_halt_reason or 'unknown')}"
    elif ctrl_running:
        _ctrl_status_label = "RUNNING"
        _ctrl_status_bg = "#1a7a4a"
        _ctrl_status_detail = (
            f"Regime: {safe_html(_ctrl_regime)} &nbsp;|&nbsp; Last signal: {safe_html(_ctrl_last_sig)} "
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
        st.info("Agent controls are in the sidebar ↑")
        _ctrl_ts = datetime.now().strftime("%H:%M:%S")
        st.caption(f"Status last checked: {_ctrl_ts} — click Refresh (sidebar) to update")

    st.divider()

    # LIVE RISK PARAMETERS
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
            write_config_risk(_risk_updates)
            st.success("Changes queued — agent will apply within 1 second")
            st.cache_data.clear()
        except Exception as _rp_exc:
            st.error(f"Failed to queue risk parameter changes: {_rp_exc}")

    st.divider()

    # CIRCUIT BREAKER STATUS
    st.subheader("Circuit Breaker Status")

    _halted = snap.get("halted", False)
    _halt_reason = snap.get("halt_reason", "")

    if _halted:
        _halt_reason_safe = _html.escape(str(_halt_reason)) if _halt_reason else ""
        st.markdown(
            "<div style='background:#c0392b;padding:14px;border-radius:8px;margin-bottom:12px'>"
            "<b style='color:white;font-size:1.1em'>AGENT HALTED</b>"
            + (f"<br><span style='color:#ffdddd'>{_halt_reason_safe}</span>" if _halt_reason_safe else "")
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

    # SIGNAL COOLDOWN
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

    # Multi-agent management UI
    st.subheader("Agent Instances")

    def _discover_agent_pids() -> list[dict]:
        """Scan data/ for agent*.pid files and return a list of agent info dicts."""
        _data_dir = Path("data")
        _agents_found: list[dict] = []
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

    # Current agent profile card (read-only)
    st.subheader("Current Agent Profile")

    _profile_cfg = read_config()
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
                <td>{safe_html(str(_enabled_count))} enabled</td></tr>
            <tr><td style='padding:3px 0'><b>Per-trade risk</b></td>
                <td>{safe_html(str(_per_trade_pct))}% of equity</td></tr>
            <tr><td style='padding:3px 0'><b>Max positions</b></td>
                <td>{safe_html(str(_max_pos_profile))}</td></tr>
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
                <td><span style='color:{_mode_col_profile};font-weight:bold'>{safe_html(_mode_profile)}</span></td></tr>
            <tr><td style='padding:3px 0'><b>Backtest gate</b></td>
                <td><span style='color:{_gate_col_profile};font-weight:bold'>{_gate_lbl_profile}</span></td></tr>
            <tr><td style='padding:3px 0'><b>Daily loss circuit</b></td>
                <td>-{safe_html(str(_risk_profile.get("daily_loss_circuit_pct", "—")))}%</td></tr>
            <tr><td style='padding:3px 0'><b>Drawdown circuit</b></td>
                <td>-{safe_html(str(_risk_profile.get("drawdown_circuit_pct", "—")))}%</td></tr>
            <tr><td style='padding:3px 0'><b>Max position size</b></td>
                <td>{safe_html(str(_risk_profile.get("max_position_pct", "—")))}% of equity</td></tr>
            </table>
            </div>
            """,
            unsafe_allow_html=True,
        )

    st.divider()

    # Start new agent (placeholder UI)
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
        f"<b style='color:#f39c12'>{safe_html(_selected_profile_name)}</b><br>"
        f"{safe_html(_sel_prof['description'])}<br>"
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

    # Claude AI overlay (Phase 4 placeholder)
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

    # Notifications
    with st.expander("🔔 Notifications", expanded=True):
        st.caption(
            "Credentials are stored in session state only — they are never written to disk "
            "unless you click **Save to .env**."
        )

        # Telegram
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
                    import re as _re
                    import urllib.request as _urllib_req
                    import urllib.parse as _urllib_parse
                    if not _re.fullmatch(r"[0-9]+:[A-Za-z0-9_\-]+", tg_token):
                        st.error("Telegram: invalid bot token format.")
                        raise ValueError("invalid token format")
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

        # Email
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

        # Save to .env
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
                    _existing_lines: list[str] = []
                    if _env_path.exists():
                        _existing_lines = _env_path.read_text(encoding="utf-8").splitlines()

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

                    _new_lines = list(_safe_existing)
                    if _new_lines and _new_lines[-1].strip() != "":
                        _new_lines.append("")

                    _new_lines.append("# --- Notification settings (managed by dashboard) ---")
                    for _nk, _nv in _notif_keys.items():
                        if _nv:
                            _new_lines.append(f"{_nk}={_nv}")

                    _env_path.write_text("\n".join(_new_lines) + "\n", encoding="utf-8")
                    st.session_state.pop("_notif_env_create_confirmed", None)
                    st.success(".env updated with notification settings.")
                except Exception as _env_exc:
                    st.error(f"Failed to write .env: {_env_exc}")
