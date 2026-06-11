"""Realistic Indian equity intraday charges, aligned to Angel One's published rates.

Source: angelone.in/exchange-transaction-charges (verified 2026-06)
  - Brokerage: lower of Rs 20 or 0.1% per executed order, minimum Rs 5
  - STT intraday: 0.025% sell side only
  - NSE exchange txn: 0.0030699% both sides
  - GST: 18% on (brokerage + exchange txn + SEBI)
  - SEBI: Rs 10/crore = 0.0001%
  - Stamp duty intraday: 0.003% buy side only
  - Clearing: Rs 0 (Angel One absorbs)
"""
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
    stamp_duty_pct: float = 0.003,
) -> ChargeBreakdown:
    """Angel One equity intraday charge model."""
    turnover = qty * price
    # Brokerage: lower of flat fee or 0.1% of turnover, with Rs 5 minimum
    brokerage = min(brokerage_per_order_inr, turnover * 0.001)
    brokerage = max(brokerage, 5.0) if turnover > 0 else 0.0
    # STT: sell side only for intraday; short-covers (BUY to close short) exempt
    if side == Side.SELL and not is_short_cover:
        stt = turnover * (stt_pct / 100)
    else:
        stt = 0.0
    exchange_txn = turnover * (exchange_txn_pct / 100)
    sebi = turnover * 0.000001  # Rs 10 per crore
    gst = (brokerage + exchange_txn + sebi) * (gst_pct / 100)
    stamp_duty = turnover * (stamp_duty_pct / 100) if side == Side.BUY else 0.0
    clearing = 0.0  # Angel One: zero clearing charges, all segments
    return ChargeBreakdown(brokerage, stt, exchange_txn, gst, sebi, stamp_duty, clearing)
