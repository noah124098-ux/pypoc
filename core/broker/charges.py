"""Realistic Indian equity intraday/delivery charges. Used by paper broker."""
from __future__ import annotations

from dataclasses import dataclass

from core.types import Side


@dataclass
class ChargeBreakdown:
    brokerage: float
    stt: float
    exchange_txn: float
    gst: float
    sebi: float
    stamp_duty: float
    clearing: float

    @property
    def total(self) -> float:
        return self.brokerage + self.stt + self.exchange_txn + self.gst + self.sebi + self.stamp_duty + self.clearing


def compute_charges(
    *,
    side: Side,
    qty: int,
    price: float,
    brokerage_per_order_inr: float,
    stt_pct: float,
    exchange_txn_pct: float,
    gst_pct: float,
    is_short_cover: bool = False,
) -> ChargeBreakdown:
    """Approximation of NSE equity intraday/delivery charges. Good enough for paper accounting."""
    turnover = qty * price
    brokerage = min(brokerage_per_order_inr, turnover * 0.0003)  # capped at flat fee
    # STT: charged on sell side for normal sells; short-covers (BUY to close short) are exempt
    if side == Side.SELL and not is_short_cover:
        stt = turnover * (stt_pct / 100)
    else:
        stt = 0.0
    exchange_txn = turnover * (exchange_txn_pct / 100)
    gst = (brokerage + exchange_txn) * (gst_pct / 100)
    sebi = turnover * 0.000001
    stamp_duty = turnover * 0.00015 if side == Side.BUY else 0.0
    clearing = turnover * 0.0005 / 100  # NSE clearing charges: 0.0005% both sides
    return ChargeBreakdown(brokerage, stt, exchange_txn, gst, sebi, stamp_duty, clearing)
