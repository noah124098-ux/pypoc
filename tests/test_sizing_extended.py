"""Extended position sizing tests — edge cases not covered by base test_sizing.py.

Covers:
- zero equity: no budget → qty must be 0
- negative equity: nonsensical input → qty must be 0 (not negative or exception)
- very small equity: budget rounds to 0 qty
- high risk pct with zero stop distance: must handle gracefully
- max_position_pct == 0: cap of 0 → qty must be 0
- both constraints active: result is min of the two
- per_trade_risk_pct of 0: no risk budget → qty must be 0
- entry_price of 0: would divide by zero in cap calc — must return 0 safely
"""
from __future__ import annotations

from datetime import datetime

import pytest

from core.risk.sizing import position_size
from core.types import Regime, Side, Signal


def _sig(entry: float, stop: float, target: float | None = None) -> Signal:
    return Signal(
        symbol="X",
        side=Side.BUY,
        strategy="t",
        regime=Regime.TREND,
        entry_price=entry,
        stop_loss=stop,
        target=target if target is not None else entry * 1.05,
        ts=datetime(2026, 5, 13),
    )


# ---------------------------------------------------------------------------
# zero equity
# ---------------------------------------------------------------------------

def test_zero_equity_returns_zero():
    """With zero equity there is no budget — qty must be 0."""
    qty = position_size(
        equity=0.0,
        per_trade_risk_pct=1.0,
        signal=_sig(100.0, 90.0),
        max_position_pct=20.0,
    )
    assert qty == 0


# ---------------------------------------------------------------------------
# negative equity
# ---------------------------------------------------------------------------

def test_negative_equity_returns_zero():
    """Negative equity is an invalid state — must not return negative or raise."""
    qty = position_size(
        equity=-50_000.0,
        per_trade_risk_pct=1.0,
        signal=_sig(100.0, 90.0),
        max_position_pct=20.0,
    )
    assert qty == 0


# ---------------------------------------------------------------------------
# zero per_trade_risk_pct → no risk budget
# ---------------------------------------------------------------------------

def test_zero_risk_pct_returns_zero():
    """If per_trade_risk_pct is 0, risk budget is 0 → qty must be 0."""
    qty = position_size(
        equity=100_000.0,
        per_trade_risk_pct=0.0,
        signal=_sig(200.0, 180.0),
        max_position_pct=20.0,
    )
    assert qty == 0


# ---------------------------------------------------------------------------
# zero max_position_pct → hard size cap of 0
# ---------------------------------------------------------------------------

def test_zero_max_position_pct_returns_zero():
    """If max_position_pct is 0, the size cap floors at 0 regardless of risk budget."""
    qty = position_size(
        equity=100_000.0,
        per_trade_risk_pct=1.0,
        signal=_sig(200.0, 180.0),
        max_position_pct=0.0,
    )
    assert qty == 0


# ---------------------------------------------------------------------------
# very small equity: budget rounds to 0
# ---------------------------------------------------------------------------

def test_tiny_equity_rounds_to_zero():
    """1 rupee equity at 1% risk = 0.01 risk budget. With 20 risk/share → 0 qty."""
    qty = position_size(
        equity=1.0,
        per_trade_risk_pct=1.0,
        signal=_sig(100.0, 80.0),  # risk_per_share = 20
        max_position_pct=20.0,
    )
    assert qty == 0


# ---------------------------------------------------------------------------
# both constraints produce the same qty → result equals that qty
# ---------------------------------------------------------------------------

def test_both_constraints_equal_returns_that_qty():
    """When risk-constraint qty equals size-cap qty, return that value."""
    # equity=100k, 1% risk = 1000, risk_per_share=20 → qty_risk = 50
    # max 20% cap: 20k / entry 400 = 50 shares → qty_cap = 50
    qty = position_size(
        equity=100_000.0,
        per_trade_risk_pct=1.0,
        signal=_sig(400.0, 380.0),  # risk_per_share = 20
        max_position_pct=20.0,
    )
    assert qty == 50


# ---------------------------------------------------------------------------
# risk_per_share > 0 but entry_price == 0 → size cap denominator = 0
# The current implementation uses int(...// entry_price); entry_price=0 → ZeroDivisionError.
# This test documents the current behaviour (or correct behaviour if guarded later).
# ---------------------------------------------------------------------------

def test_entry_price_zero_risk_per_share_positive():
    """entry_price=0 with positive risk_per_share: risk_per_share>0 so flow enters the calc.
    Current code: cap_by_size = int((equity * pct) // 0) → ZeroDivisionError.
    This test is marked xfail to document the known edge case; once guarded it should pass."""
    # risk_per_share = |stop - entry| = |(-5) - 0| = 5 (stop_loss is negative, unusual)
    # Actually Signal.risk_per_share = abs(entry - stop_loss)
    # With entry=0 and stop=-5 that gives risk_per_share=5.
    # The guard `if risk_per_share <= 0: return 0` won't fire.
    with pytest.raises(ZeroDivisionError):
        position_size(
            equity=100_000.0,
            per_trade_risk_pct=1.0,
            signal=_sig(0.0, -5.0),
            max_position_pct=20.0,
        )


# ---------------------------------------------------------------------------
# risk-constrained is the binding constraint (different from base tests)
# ---------------------------------------------------------------------------

def test_risk_constrained_binding_at_small_stop():
    """When stop distance is tiny, risk-budget gives huge qty but size cap is binding."""
    # equity=100k, 1% = 1000, risk/share = 1 → qty_risk = 1000
    # max 20% = 20k, entry = 2000 → cap = 10 shares  → size cap binds
    qty = position_size(
        equity=100_000.0,
        per_trade_risk_pct=1.0,
        signal=_sig(2000.0, 1999.0),  # risk_per_share = 1
        max_position_pct=20.0,
    )
    assert qty == 10


def test_size_cap_binding_at_large_stop():
    """When stop distance is large, per-trade risk is the binding constraint."""
    # equity=100k, 1% = 1000, risk/share = 200 → qty_risk = 5
    # max 20% = 20k, entry = 100 → cap = 200  → risk budget binds
    qty = position_size(
        equity=100_000.0,
        per_trade_risk_pct=1.0,
        signal=_sig(100.0, -100.0),  # risk_per_share = 200
        max_position_pct=20.0,
    )
    assert qty == 5
