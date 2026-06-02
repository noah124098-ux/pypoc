"""AI Review tab — EOD review, market commentary, news sentiment scorer."""
from __future__ import annotations

import html as _html
import os
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
import streamlit as st

from dashboard.utils.charts import safe_html
from dashboard.utils.db import query_df
from dashboard.utils.snapshot import read_last_review, save_last_review

DB_PATH = Path("data/agent.db")
SNAPSHOT_PATH = Path("data/snapshot.json")

try:
    from core.llm.eod_reviewer import run_eod_review as _run_eod_review, ReviewReport as _ReviewReport
    _EOD_REVIEWER_AVAILABLE = True
except ImportError:
    _EOD_REVIEWER_AVAILABLE = False

try:
    import anthropic as _anthropic_mod  # noqa: F401
    _ANTHROPIC_INSTALLED = True
except ImportError:
    _ANTHROPIC_INSTALLED = False

try:
    from core.llm.news_scorer import score_news
    _NEWS_SCORER_AVAILABLE = True
except ImportError:
    _NEWS_SCORER_AVAILABLE = False


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


def render(snap: dict, conn) -> None:
    """Render the AI End-of-Day Review tab."""
    st.header("AI End-of-Day Review")
    st.caption(
        "Claude analyses today's trades, signals, guardrail rejections, and regime data "
        "and proposes parameter tweaks. Requires ANTHROPIC_API_KEY in the environment."
    )

    # API key check
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

    # Auto-review toggle (session state only)
    auto_eod = st.toggle(
        "Auto-review at EOD",
        value=st.session_state.get("auto_eod_review", False),
        disabled=not _api_key_ok,
        help="Session-only setting. When enabled, the review runs automatically when the page refreshes near market close.",
    )
    st.session_state["auto_eod_review"] = auto_eod

    # Auto-review countdown to next 15:35 IST
    if auto_eod and _api_key_ok:
        _now_utc_ar = datetime.utcnow()
        _now_ist_ar = _now_utc_ar + timedelta(hours=5, minutes=30)
        _eod_review_time = _now_ist_ar.replace(hour=15, minute=35, second=0, microsecond=0)
        if _now_ist_ar >= _eod_review_time:
            _next_eod = _eod_review_time + timedelta(days=1)
        else:
            _next_eod = _eod_review_time
        _secs_to_eod = int((_next_eod - _now_ist_ar).total_seconds())
        _h_to_eod, _rem = divmod(_secs_to_eod, 3600)
        _m_to_eod, _s_to_eod = divmod(_rem, 60)
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
                        save_last_review(_auto_dict)
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

    # SECTION 1: MARKET COMMENTARY WIDGET
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
        _mc_regime = snap.get("regime", "UNKNOWN")
        _mc_vix = snap.get("vix", "n/a")
        _mc_adx = snap.get("adx", "n/a")
        _mc_equity = snap.get("equity", 0)
        _mc_halted = snap.get("halted", False)

        _mc_pos_lines = []
        for _mcp in snap.get("open_positions", []):
            _mcp_sym = _mcp.get("symbol", "?")
            _mcp_side = _mcp.get("side", "?")
            _mcp_qty = _mcp.get("qty", 0)
            _mcp_unr = _mcp.get("unrealized_pnl", 0)
            _mc_pos_lines.append(
                f"  - {_mcp_sym} {_mcp_side} qty={_mcp_qty} unrealized_pnl=₹{_mcp_unr:.0f}"
            )

        if _mc_preset == "Ask about portfolio":
            _mc_trades_q = query_df(
                conn,
                "SELECT symbol, side, pnl, strategy, closed_at FROM trades ORDER BY closed_at DESC LIMIT 20",
            )
        else:
            _mc_trades_q = query_df(
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

Respond in 3-5 sentences. Be direct and actionable. Do not disclaim about data availability.
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
            f"<span style='color:#e8e8e8;line-height:1.6'>{_html.escape(str(_mc_response))}</span>"
            "</div>",
            unsafe_allow_html=True,
        )
    elif _mc_submit and not _mc_question.strip():
        st.warning("Please enter a question before submitting.")

    if not (_api_key_ok and _ANTHROPIC_INSTALLED):
        st.caption("Market commentary requires ANTHROPIC_API_KEY and the `anthropic` package.")

    st.divider()

    # SECTION 2: STRATEGY SIGNALS EXPLAINER
    st.subheader("Strategy Signals Explainer")
    st.caption("Last 10 signals with rationale. For rejected signals, see the guardrail rule and get a plain-English explanation.")

    _se_sig_df = query_df(
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
                    f"<b style='color:#f0f0f0'>{safe_html(_se_sym)}</b> "
                    f"<span style='color:#aaa;font-size:0.85em'>{safe_html(_se_side)} · {safe_html(_se_strat)}</span>",
                    unsafe_allow_html=True,
                )
                _se_c2.markdown(
                    f"<span style='color:#aaa;font-size:0.85em'>Regime: {safe_html(_se_regime)} · "
                    f"₹{float(_se_price or 0):,.0f}</span>",
                    unsafe_allow_html=True,
                )
                _se_c3.markdown(
                    f"<span style='color:#888;font-size:0.8em'>{safe_html(_se_ts)}</span>",
                    unsafe_allow_html=True,
                )
                _se_c4.markdown(
                    f"<span style='color:{_se_status_color};font-weight:700;font-size:0.9em'>"
                    f"{_se_status_label}</span>",
                    unsafe_allow_html=True,
                )

                if _se_rationale:
                    st.markdown(
                        f"<span style='color:#aaa;font-size:0.85em'>Strategy rationale: {_html.escape(str(_se_rationale))}</span>",
                        unsafe_allow_html=True,
                    )

                if not _se_accepted and _se_reason:
                    st.markdown(
                        f"<span style='color:#e74c3c;font-size:0.85em'>Rejected: {_html.escape(str(_se_reason))}</span>",
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
                            f"{_html.escape(str(st.session_state[_se_result_key]))}</span></div>",
                            unsafe_allow_html=True,
                        )

                st.markdown("</div>", unsafe_allow_html=True)
    else:
        st.info("No signals recorded yet. Run the agent to generate signals.")

    st.divider()

    # SECTION 3: PARAMETER SUGGESTIONS
    _ps_last = read_last_review()
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
                f"<b style='color:#f0f0f0'>{safe_html(_ps_strat)}</b><br>"
                f"<span style='color:#aaa;font-size:0.85em'>{safe_html(_ps_param)}</span>",
                unsafe_allow_html=True,
            )
            _ps_col2.markdown(
                f"<span style='color:#e74c3c;font-weight:600'>{safe_html(str(_ps_cur))}</span>"
                f"<span style='color:#aaa'> → </span>"
                f"<span style='color:#2ecc71;font-weight:600'>{safe_html(str(_ps_sug))}</span>",
                unsafe_allow_html=True,
            )
            _ps_col3.markdown(
                f"<span style='color:#aaa;font-size:0.85em'>{safe_html(_ps_rat)}</span>",
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
                    f"<span style='color:#2ecc71;font-size:0.85em'>Applied: {safe_html(_ps_param)} set to {safe_html(str(_ps_sug))}</span>",
                    unsafe_allow_html=True,
                )
            st.markdown("</div>", unsafe_allow_html=True)
    else:
        st.subheader("Parameter Suggestions")
        st.info(
            "No parameter suggestions available. Run an EOD review below to generate suggestions."
        )

    st.divider()

    # SECTION 4: EOD REVIEW (run + last review display)
    st.subheader("Run EOD Review Now")
    if st.button(
        "Run EOD Review Now",
        type="primary",
        disabled=not _api_key_ok,
        help="Calls Claude to analyse today's activity and propose parameter changes.",
    ):
        with st.spinner("Running EOD review with Claude... (may take 15-30 seconds)"):
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
            save_last_review(_review_dict)

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

    # Last review section
    st.subheader("Last Review")
    _last = read_last_review()
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

    # Manual Email Report
    st.subheader("📧 Send Performance Report")
    if st.button("Send EOD Report Now", key="send_email_report"):
        try:
            from core.analytics.performance_report import generate_html_report
            from core.notifications.email_notifier import EmailNotifier
            from core.config import Secrets
            _sec = Secrets.from_env()
            _notifier = EmailNotifier(
                _sec.smtp_host, _sec.smtp_port, _sec.smtp_user,
                _sec.smtp_password, _sec.email_from, _sec.email_to,
            )
            _html_report = generate_html_report("data/agent.db", "data/snapshot.json")
            _snap_for_email = read_last_review() or {}
            ok = _notifier.send_eod_report(
                equity=float(_snap_for_email.get("equity", 0)),
                pnl=float(_snap_for_email.get("realized_pnl", 0)),
                trades=len(_snap_for_email.get("open_positions", [])),
                review_summary=_html_report,
            )
            if ok:
                st.success("Email sent!")
            else:
                st.warning("Email failed — check SMTP credentials in Controls tab")
        except Exception as _email_exc:
            st.warning(f"Email not configured: {_email_exc}")

    st.divider()

    # News Sentiment Scorer
    st.subheader("📰 News Sentiment Scorer")
    st.caption("Score news headlines for selected stocks using Claude Haiku")

    selected_symbols = st.multiselect(
        "Select symbols to score",
        options=["RELIANCE", "INFY", "TCS", "HDFC", "ICICIBANK", "HCLTECH", "WIPRO", "BAJFINANCE", "LTIM", "AXISBANK"],
        default=["RELIANCE", "INFY"],
    )

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
