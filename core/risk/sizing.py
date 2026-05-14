"""Position sizing — 1%-rule with ATR-based stop distance."""
from __future__ import annotations

from core.types import Signal


def position_size(
    *,
    equity: float,
    per_trade_risk_pct: float,
    signal: Signal,
    max_position_pct: float,
) -> int:
    """Quantity such that (entry - stop) * qty <= per_trade_risk_pct% of equity,
    additionally capped by max_position_pct of equity."""
    risk_per_share = signal.risk_per_share
    if risk_per_share <= 0:
        return 0
    risk_budget = equity * (per_trade_risk_pct / 100.0)
    qty_by_risk = int(risk_budget // risk_per_share)
    cap_by_size = int((equity * (max_position_pct / 100.0)) // signal.entry_price)
    return max(0, min(qty_by_risk, cap_by_size))
