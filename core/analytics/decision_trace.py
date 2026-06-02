"""Decision trace — reconstruct the signal → order pipeline for a given signal.

Historical guardrail checks are not stored in the DB (they are ephemeral), so
this module does two things:

1.  Reads the persisted signal row from SQLite and its associated trade row (if
    it was accepted) to reconstruct the *sizing* arithmetic.
2.  Builds a *static* checklist of the guardrail rules that would have fired,
    populated with whatever runtime values can be recovered from the DB.  Rules
    whose inputs cannot be recovered are shown as "N/A".

The result is a plain dict so the dashboard can render it without importing any
trading-engine internals.
"""
from __future__ import annotations

import sqlite3
from typing import Any


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def trace_signal_decision(signal_id: int, db_path: str) -> dict:
    """Reconstruct the full decision trace for a given signal.

    Returns a dict with:
    {
      "signal": { symbol, strategy, side, entry_price, stop_loss, target,
                  regime, confidence, rationale, ts },
      "sizing": { equity_at_time, per_trade_risk_pct, raw_qty, final_qty },
      "guardrail_checks": [
        { "rule": "max_open_positions", "passed": bool | None,
          "detail": "2/5 positions open" },
        ...
      ],
      "final_decision": "ACCEPTED" | "REJECTED" | "UNKNOWN",
      "rejection_reason": null | "rule: detail"
    }

    Returns {} if the signal is not found or the DB is unavailable.
    """
    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row

        sig = conn.execute(
            "SELECT * FROM signals WHERE id=?", (signal_id,)
        ).fetchone()

        if sig is None:
            conn.close()
            return {}

        sig_dict = dict(sig)

        # Try to find the associated trade (accepted signals only)
        trade = None
        if sig_dict.get("accepted"):
            trade = conn.execute(
                """
                SELECT * FROM trades
                WHERE symbol=?
                  AND replace(opened_at,'T',' ') >= replace(?,       'T',' ')
                  AND replace(opened_at,'T',' ') <= datetime(replace(?,   'T',' '), '+5 minutes')
                ORDER BY opened_at ASC LIMIT 1
                """,
                (sig_dict["symbol"], sig_dict["ts"], sig_dict["ts"]),
            ).fetchone()

        conn.close()
    except Exception:
        return {}

    # ── Signal section ───────────────────────────────────────────────────────
    entry_price = float(sig_dict.get("entry_price") or 0.0)
    stop_loss   = float(sig_dict.get("stop_loss")   or 0.0)
    target      = float(sig_dict.get("target")      or 0.0)
    confidence  = float(sig_dict.get("confidence")  or 0.0)

    signal_out = {
        "symbol":    sig_dict.get("symbol", ""),
        "strategy":  sig_dict.get("strategy", ""),
        "side":      sig_dict.get("side", ""),
        "entry_price": entry_price,
        "stop_loss": stop_loss,
        "target":    target,
        "regime":    sig_dict.get("regime", ""),
        "confidence": confidence,
        "rationale": sig_dict.get("rationale", ""),
        "ts":        sig_dict.get("ts", ""),
    }

    # ── Sizing section ───────────────────────────────────────────────────────
    # We cannot perfectly recover equity-at-time from the DB without an
    # equity_snapshots table, so we show what we can reconstruct.
    risk_per_share = abs(entry_price - stop_loss) if stop_loss and entry_price else 0.0
    final_qty = 0
    if trade:
        try:
            final_qty = int(trade["qty"])
        except (TypeError, KeyError):
            pass

    # Back-calculate the implied equity from qty + 1% risk rule (best-effort)
    implied_equity: float | None = None
    if risk_per_share > 0 and final_qty > 0:
        # risk_budget = equity * per_trade_risk_pct / 100
        # qty = int(risk_budget / risk_per_share)  =>  equity ≈ qty * risk_per_share / (pct/100)
        # We assume the default 1% risk
        implied_equity = round(final_qty * risk_per_share / 0.01, 2)

    sizing_out = {
        "equity_at_time": implied_equity,        # None = not recoverable
        "per_trade_risk_pct": 1.0,               # default; not stored per-signal
        "risk_per_share": round(risk_per_share, 2),
        "raw_qty": final_qty,                    # qty as executed (or 0)
        "final_qty": final_qty,
    }

    # ── Guardrail checks (reconstructed) ─────────────────────────────────────
    # For accepted signals the checks all passed; we render them as "PASSED"
    # with whatever detail we can derive.  For rejected signals we know which
    # rule fired from rejection_reason.
    accepted = bool(sig_dict.get("accepted", False))
    rejection_reason = sig_dict.get("rejection_reason") or None

    checks = _build_guardrail_checklist(
        sig_dict=sig_dict,
        trade=dict(trade) if trade else None,
        accepted=accepted,
        rejection_reason=rejection_reason,
        entry_price=entry_price,
        stop_loss=stop_loss,
        final_qty=final_qty,
    )

    return {
        "signal":           signal_out,
        "sizing":           sizing_out,
        "guardrail_checks": checks,
        "final_decision":   "ACCEPTED" if accepted else "REJECTED",
        "rejection_reason": rejection_reason,
    }


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _build_guardrail_checklist(
    *,
    sig_dict: dict,
    trade: dict | None,
    accepted: bool,
    rejection_reason: str | None,
    entry_price: float,
    stop_loss: float,
    final_qty: int,
) -> list[dict]:
    """Return a list of guardrail check dicts.

    Each dict:  { "rule": str, "passed": bool | None, "detail": str }

    passed=True  — check passed (order allowed)
    passed=False — check failed (order rejected)
    passed=None  — cannot determine from stored data
    """
    # Identify which rule fired (if any)
    fired_rule = None
    if rejection_reason:
        # rejection_reason format: "rule: detail"  or just the rule name
        fired_rule = rejection_reason.split(":")[0].strip()

    def _check(rule: str, detail: str) -> dict[str, Any]:
        if fired_rule == rule:
            return {"rule": rule, "passed": False, "detail": detail}
        if not accepted and fired_rule and fired_rule != rule:
            # A different rule fired before this one; this one is "not reached"
            # (earlier rules in the pipeline take precedence).  We don't know
            # if it would have passed, so mark None only for rules AFTER the
            # failing rule in the pipeline ordering.
            pass
        return {"rule": rule, "passed": True if accepted else None, "detail": detail}

    checks: list[dict] = []

    # 1. global_halt — agent not halted
    checks.append(_check("global_halt", "agent not halted"))

    # 2. qty_positive
    if final_qty > 0:
        checks.append(_check("qty_positive", f"qty = {final_qty}"))
    else:
        checks.append(_check("qty_positive", "qty = 0 (not computed)"))

    # 3. stop_loss_required
    side = sig_dict.get("side", "BUY").upper()
    if stop_loss and entry_price:
        if side == "BUY":
            ok = stop_loss < entry_price
        else:
            ok = stop_loss > entry_price
        detail = f"stop ₹{stop_loss:,.2f} vs entry ₹{entry_price:,.2f}"
        if fired_rule == "stop_loss_required":
            checks.append({"rule": "stop_loss_required", "passed": False, "detail": detail})
        else:
            checks.append({"rule": "stop_loss_required", "passed": ok, "detail": detail})
    else:
        checks.append(_check("stop_loss_required", "stop_loss = N/A"))

    # 4. market_window
    ts = sig_dict.get("ts", "")
    time_part = ts[11:16] if len(ts) >= 16 else "N/A"
    checks.append(_check("market_window", f"signal at {time_part} IST"))

    # 5. stale_data
    checks.append(_check("stale_data", "tick freshness at signal time — N/A"))

    # 6. black_swan_nifty / black_swan_vix
    checks.append(_check("black_swan_nifty", "Nifty 15-min move — N/A"))
    checks.append(_check("black_swan_vix",   "VIX 15-min jump — N/A"))

    # 7. max_open_positions — can't recover exact count; show placeholder
    checks.append(_check("max_open_positions", "open positions at signal time — N/A"))

    # 8. per_trade_risk
    risk_per_share = abs(entry_price - stop_loss) if stop_loss and entry_price else 0.0
    if risk_per_share > 0 and final_qty > 0:
        risk_amount = risk_per_share * final_qty
        detail = f"risk ₹{risk_amount:,.2f} (qty {final_qty} x ₹{risk_per_share:.2f}/share)"
    else:
        detail = "risk amount — N/A"
    checks.append(_check("per_trade_risk", detail))

    # 9. max_position_size
    if entry_price and final_qty > 0:
        position_value = entry_price * final_qty
        detail = f"position value ₹{position_value:,.0f}"
    else:
        detail = "position value — N/A"
    checks.append(_check("max_position_size", detail))

    # 10. liquidity
    checks.append(_check("liquidity", "ADV check at signal time — N/A"))

    # 11. spread
    checks.append(_check("spread", "bid-ask spread at signal time — N/A"))

    # 12. daily_loss_circuit
    checks.append(_check("daily_loss_circuit", "day P&L at signal time — N/A"))

    # 13. drawdown_circuit
    checks.append(_check("drawdown_circuit", "drawdown at signal time — N/A"))

    # 14. signal_cooldown
    checks.append(_check("signal_cooldown", f"{sig_dict.get('symbol','')} cooldown — N/A"))

    # 15. duplicate_position
    checks.append(_check("duplicate_position", "same-symbol position check — N/A"))

    # For a rejected signal, mark all rules after the fired rule as "not_reached"
    if not accepted and fired_rule:
        _mark_unreached(checks, fired_rule)

    return checks


# Rule pipeline order — must match Guardrails.check() call order
_RULE_ORDER = [
    "global_halt",
    "qty_positive",
    "stop_loss_required",
    "market_window",
    "stale_data",
    "black_swan_nifty",
    "black_swan_vix",
    "max_open_positions",
    "per_trade_risk",
    "max_position_size",
    "liquidity",
    "spread",
    "daily_loss_circuit",
    "drawdown_circuit",
    "signal_cooldown",
    "duplicate_position",
]


def _mark_unreached(checks: list[dict], fired_rule: str) -> None:
    """For rules that appear after the fired rule in the pipeline, set
    passed=None and append '(not reached)' to the detail string."""
    try:
        cutoff = _RULE_ORDER.index(fired_rule)
    except ValueError:
        return
    for check in checks:
        try:
            pos = _RULE_ORDER.index(check["rule"])
        except ValueError:
            continue
        if pos > cutoff and check["passed"] is None:
            check["detail"] = check["detail"].rstrip() + " (not reached)"
