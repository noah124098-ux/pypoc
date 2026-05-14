"""Position sizing tests."""
from __future__ import annotations

from datetime import datetime

from core.risk.sizing import position_size
from core.types import Regime, Side, Signal


def _sig(entry: float, stop: float) -> Signal:
    return Signal(
        symbol="X", side=Side.BUY, strategy="t", regime=Regime.TREND,
        entry_price=entry, stop_loss=stop, target=entry * 1.05,
        ts=datetime(2026, 5, 13),
    )


def test_risk_constrained_qty():
    # 100k equity, 1% risk = 1000. Risk per share = 20. Expect qty = 50.
    # Use entry=200 so 50*200 = 10k is well under the 20% (20k) cap.
    qty = position_size(equity=100_000, per_trade_risk_pct=1.0, signal=_sig(200, 180),
                        max_position_pct=20.0)
    assert qty == 50


def test_size_cap_constrained_qty():
    # 100k equity, max 20% = 20k. At entry 1300, max 15 shares regardless of risk budget.
    # Risk per share = 1 -> risk-side would allow 1000, but size cap dominates.
    qty = position_size(equity=100_000, per_trade_risk_pct=1.0, signal=_sig(1300, 1299),
                        max_position_pct=20.0)
    assert qty == 15


def test_zero_risk_per_share_returns_zero():
    qty = position_size(equity=100_000, per_trade_risk_pct=1.0, signal=_sig(100, 100),
                        max_position_pct=20.0)
    assert qty == 0
