"""EOD Claude review module.

Queries today's trades, signals, guardrail rejections, and regime data from SQLite,
builds a structured prompt, and calls Claude to produce a ReviewReport with a
summary, parameter suggestions, and flags.

Usage::

    from core.llm.eod_reviewer import run_eod_review
    report = run_eod_review(db_path="data/agent.db", snapshot_path="data/snapshot.json",
                            model="claude-opus-4-7", api_key=secrets.anthropic_api_key)
    if report:
        print(report.summary)
"""
from __future__ import annotations

import json
import logging
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Public data classes
# ---------------------------------------------------------------------------

@dataclass
class ParameterSuggestion:
    strategy: str
    parameter: str
    current_value: float
    suggested_value: float
    rationale: str


@dataclass
class ReviewReport:
    summary: str
    suggestions: list[ParameterSuggestion]
    flags: list[str]
    raw_response: str


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _connect(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def _rows_to_dicts(rows: list[sqlite3.Row]) -> list[dict[str, Any]]:
    return [dict(r) for r in rows]


def _query_last_24h(db_path: str) -> dict[str, Any]:
    """Return trades, signals, guardrail events, and regime data from the last 24 hours."""
    cutoff = (datetime.utcnow() - timedelta(hours=24)).isoformat()

    result: dict[str, Any] = {
        "trades": [],
        "signals_accepted": [],
        "signals_rejected": [],
        "guardrail_events": [],
        "regime_counts": {},
    }

    if not Path(db_path).exists():
        return result

    try:
        with _connect(db_path) as conn:
            # Trades closed in the last 24h
            rows = conn.execute(
                "SELECT * FROM trades WHERE closed_at >= ? ORDER BY closed_at DESC",
                (cutoff,),
            ).fetchall()
            result["trades"] = _rows_to_dicts(rows)

            # Signals from the last 24h
            rows = conn.execute(
                "SELECT * FROM signals WHERE ts >= ? ORDER BY ts DESC",
                (cutoff,),
            ).fetchall()
            for r in _rows_to_dicts(rows):
                if r.get("accepted"):
                    result["signals_accepted"].append(r)
                else:
                    result["signals_rejected"].append(r)

            # Guardrail events from the last 24h
            rows = conn.execute(
                "SELECT * FROM guardrail_events WHERE ts >= ? ORDER BY ts DESC",
                (cutoff,),
            ).fetchall()
            result["guardrail_events"] = _rows_to_dicts(rows)

            # Regime distribution from the last 24h
            rows = conn.execute(
                "SELECT regime, COUNT(*) as cnt FROM regime_log WHERE ts >= ? GROUP BY regime",
                (cutoff,),
            ).fetchall()
            result["regime_counts"] = {r["regime"]: r["cnt"] for r in rows}

    except sqlite3.Error as exc:
        logger.warning("DB query failed: %s", exc)

    return result


def _read_snapshot(snapshot_path: str) -> dict[str, Any]:
    p = Path(snapshot_path)
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("Failed to read snapshot: %s", exc)
        return {}


def _pnl_by_strategy(trades: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Aggregate P&L and win count per strategy."""
    agg: dict[str, dict[str, Any]] = {}
    for t in trades:
        strat = t.get("strategy") or "unknown"
        if strat not in agg:
            agg[strat] = {"trades": 0, "total_pnl": 0.0, "wins": 0}
        agg[strat]["trades"] += 1
        agg[strat]["total_pnl"] += float(t.get("pnl", 0.0))
        if float(t.get("pnl", 0.0)) > 0:
            agg[strat]["wins"] += 1
    return agg


def _rejection_breakdown(events: list[dict[str, Any]]) -> dict[str, int]:
    breakdown: dict[str, int] = {}
    for e in events:
        rule = e.get("rule") or "unknown"
        breakdown[rule] = breakdown.get(rule, 0) + 1
    return breakdown


def _build_prompt(data: dict[str, Any], snapshot: dict[str, Any]) -> str:
    trades = data["trades"]
    accepted = data["signals_accepted"]
    rejected = data["signals_rejected"]
    guardrails = data["guardrail_events"]
    regime_counts = data["regime_counts"]

    pnl_by_strat = _pnl_by_strategy(trades)
    rejection_rules = _rejection_breakdown(guardrails)
    signal_rejection_reasons = _rejection_breakdown(
        [{"rule": r.get("rejection_reason", "unknown")} for r in rejected]
    )

    total_pnl = sum(float(t.get("pnl", 0.0)) for t in trades)
    equity = snapshot.get("equity", "N/A")
    halted = snapshot.get("halted", False)

    lines = [
        "You are reviewing a day of paper trading on Nifty 50 by an automated regime-aware agent.",
        "Your job is to produce a concise end-of-day assessment and, where helpful, suggest 1-3",
        "parameter changes that could improve performance. Be specific and data-driven.",
        "",
        "=== TODAY'S TRADING SUMMARY ===",
        f"Date (UTC): {datetime.utcnow().date().isoformat()}",
        f"Equity: {equity}",
        f"Agent halted: {halted}",
        f"Total closed P&L today: {total_pnl:.2f} INR across {len(trades)} trade(s)",
        "",
        "--- P&L by strategy ---",
    ]

    if pnl_by_strat:
        for strat, stats in pnl_by_strat.items():
            win_rate = (stats["wins"] / stats["trades"] * 100) if stats["trades"] else 0
            lines.append(
                f"  {strat}: {stats['trades']} trade(s), "
                f"P&L={stats['total_pnl']:.2f}, win_rate={win_rate:.0f}%"
            )
    else:
        lines.append("  (no closed trades today)")

    lines += [
        "",
        f"--- Signals: {len(accepted)} accepted, {len(rejected)} rejected ---",
    ]

    if signal_rejection_reasons:
        lines.append("  Signal rejection breakdown:")
        for reason, cnt in sorted(signal_rejection_reasons.items(), key=lambda x: -x[1]):
            lines.append(f"    {reason}: {cnt}")

    lines += [
        "",
        f"--- Guardrail events: {len(guardrails)} total ---",
    ]
    if rejection_rules:
        lines.append("  Rule breakdown:")
        for rule, cnt in sorted(rejection_rules.items(), key=lambda x: -x[1]):
            lines.append(f"    {rule}: {cnt}")

    lines += [
        "",
        "--- Regime distribution (last 24h) ---",
    ]
    if regime_counts:
        total_regime = sum(regime_counts.values())
        for regime, cnt in sorted(regime_counts.items()):
            pct = cnt / total_regime * 100 if total_regime else 0
            lines.append(f"  {regime}: {cnt} ({pct:.0f}%)")
    else:
        lines.append("  (no regime data)")

    if trades:
        lines += ["", "--- Recent closed trades (up to 10) ---"]
        for t in trades[:10]:
            lines.append(
                f"  {t.get('symbol')} {t.get('side')} qty={t.get('qty')} "
                f"entry={t.get('entry_price')} exit={t.get('exit_price')} "
                f"pnl={t.get('pnl'):.2f} strategy={t.get('strategy')} "
                f"exit_reason={t.get('exit_reason')}"
            )

    lines += [
        "",
        "=== INSTRUCTIONS ===",
        "Respond in valid JSON with exactly this structure:",
        '{',
        '  "summary": "<2-4 sentence summary of the day>",',
        '  "suggestions": [',
        '    {',
        '      "strategy": "<strategy_name>",',
        '      "parameter": "<parameter_name>",',
        '      "current_value": <number>,',
        '      "suggested_value": <number>,',
        '      "rationale": "<why>"',
        '    }',
        '  ],',
        '  "flags": ["<any risk or anomaly worth flagging>"]',
        '}',
        "",
        "Rules:",
        "- suggestions must be an array of 0-3 objects; empty array is fine if nothing to suggest.",
        "- flags must be an array of strings; empty array is fine.",
        "- Do not add extra keys outside the structure above.",
        "- If current_value is unknown, set it to 0.",
        "- Keep each suggestion focused on a single numeric parameter.",
    ]

    return "\n".join(lines)


def _parse_response(raw: str) -> tuple[str, list[ParameterSuggestion], list[str]]:
    """Parse Claude's JSON response. Returns (summary, suggestions, flags)."""
    # Strip markdown code fences if present
    text = raw.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        # Drop first and last fence lines
        inner = []
        in_block = False
        for line in lines:
            if line.startswith("```") and not in_block:
                in_block = True
                continue
            if line.startswith("```") and in_block:
                break
            if in_block:
                inner.append(line)
        text = "\n".join(inner)

    data = json.loads(text)

    summary = str(data.get("summary", ""))
    flags = [str(f) for f in data.get("flags", [])]

    suggestions: list[ParameterSuggestion] = []
    for s in data.get("suggestions", []):
        suggestions.append(
            ParameterSuggestion(
                strategy=str(s.get("strategy", "")),
                parameter=str(s.get("parameter", "")),
                current_value=float(s.get("current_value", 0)),
                suggested_value=float(s.get("suggested_value", 0)),
                rationale=str(s.get("rationale", "")),
            )
        )

    return summary, suggestions, flags


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def run_eod_review(
    db_path: str,
    snapshot_path: str,
    model: str = "claude-opus-4-7",
    api_key: str = "",
    *,
    client: Any = None,  # injectable for tests
) -> Optional[ReviewReport]:
    """Run EOD review using Claude.

    Parameters
    ----------
    db_path:
        Path to the SQLite database written by :class:`core.persistence.store.Store`.
    snapshot_path:
        Path to the JSON runtime snapshot written by the orchestrator.
    model:
        Anthropic model ID to use.
    api_key:
        Anthropic API key. If empty and no *client* is provided, returns ``None``.
    client:
        Optional pre-constructed ``anthropic.Anthropic`` client (useful for testing).

    Returns
    -------
    :class:`ReviewReport` on success, ``None`` if the API key is missing or the
    call fails.
    """
    if not api_key and client is None:
        logger.info("EOD review skipped: no API key and no injected client.")
        return None

    data = _query_last_24h(db_path)
    snapshot = _read_snapshot(snapshot_path)
    prompt = _build_prompt(data, snapshot)

    try:
        if client is None:
            import anthropic as _anthropic
            client = _anthropic.Anthropic(api_key=api_key)

        msg = client.messages.create(
            model=model,
            max_tokens=1024,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = msg.content[0].text

    except Exception as exc:  # network errors, auth errors, etc.
        logger.warning("EOD review API call failed: %s", exc)
        return None

    try:
        summary, suggestions, flags = _parse_response(raw)
    except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
        logger.warning("EOD review response parse failed: %s. Raw: %s", exc, raw[:200])
        # Still return a partial report with raw text so the caller can inspect it
        return ReviewReport(
            summary="(parse error — see raw_response)",
            suggestions=[],
            flags=["response_parse_error"],
            raw_response=raw,
        )

    return ReviewReport(
        summary=summary,
        suggestions=suggestions,
        flags=flags,
        raw_response=raw,
    )
