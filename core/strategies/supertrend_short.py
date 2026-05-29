"""Supertrend short strategy -- fires when Supertrend is bearish on a downtrending stock.

Complements the long-only Supertrend by capturing downside moves during VOLATILE
and correcting TREND regimes. Only fires when the stock's own 50-DMA is falling
(confirmed downtrend) -- avoids false shorts in choppy markets.

Entry is triggered on the first day both conditions align: Supertrend bearish
AND 50-DMA just confirmed falling (was rising the day before). This fires exactly
once per downtrend confirmation event rather than every day.

Works in VOLATILE and TREND regimes.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

import pandas as pd

from core.strategies.base import IStrategy
from core.strategies.indicators import atr, supertrend_bands
from core.types import Regime, Side, Signal


class SupertrendShort(IStrategy):
    name = "supertrend_short"
    regimes = [Regime.VOLATILE, Regime.TREND]

    def __init__(
        self,
        atr_period: int = 10,
        multiplier: float = 3.0,
        target_r_multiple: float = 2.0,
        stock_dma_period: int = 50,
    ):
        self.atr_period = atr_period
        self.multiplier = multiplier
        self.target_r_multiple = target_r_multiple
        self.stock_dma_period = stock_dma_period

    def evaluate(self, symbol: str, candles: pd.DataFrame, regime: Regime) -> Optional[Signal]:
        if not self.supports(regime):
            return None
        min_bars = max(self.atr_period, self.stock_dma_period) + 21
        if len(candles) < min_bars:
            return None

        close = candles["close"]

        # Only short stocks with a confirmed falling 50-DMA (downtrend).
        # Compare current DMA to 20 bars ago (one calendar month): if it's not declining
        # even slightly, skip. The 50-DMA is slow -- using a 20-bar lookback gives it
        # enough time to actually move while still filtering out sideways stocks.
        dma = close.rolling(self.stock_dma_period).mean()
        if pd.isna(dma.iloc[-1]) or pd.isna(dma.iloc[-21]):
            return None
        dma_falling_today = dma.iloc[-1] < dma.iloc[-21]
        if not dma_falling_today:
            return None  # DMA not falling over past month -- no short

        # Fire on the *first day* both conditions are simultaneously true within a
        # bearish run. This avoids re-firing every day during a sustained downtrend
        # while still allowing entry after the DMA-confirmation lag.
        # Condition: direction[-1]==-1 (bearish now) AND yesterday's DMA check
        # was not falling (i.e., today is the first confirmed day in the decline).
        final_upper, final_lower, direction = supertrend_bands(
            candles, self.atr_period, self.multiplier
        )
        if direction[-1] != -1:
            return None  # Supertrend not currently bearish

        # Check if yesterday the 50-DMA was still rising (dma[-2] >= dma[-22]).
        # If it was already falling yesterday, this isn't a fresh confirmation.
        if len(dma.dropna()) >= 23 and not pd.isna(dma.iloc[-22]):
            dma_was_falling_yesterday = dma.iloc[-2] < dma.iloc[-22]
            if dma_was_falling_yesterday:
                return None  # DMA already falling yesterday -- not a fresh signal

        latest_close = float(close.iloc[-1])
        latest_atr = float(atr(candles, self.atr_period).iloc[-1])
        if pd.isna(latest_atr) or latest_atr <= 0:
            return None

        # For a short: stop above entry (supertrend line + buffer), target below
        stop = float(final_upper[-1]) + 0.1 * latest_atr
        if stop <= latest_close:
            return None
        risk = stop - latest_close
        target = latest_close - self.target_r_multiple * risk

        return Signal(
            symbol=symbol,
            side=Side.SELL,
            strategy=self.name,
            regime=regime,
            entry_price=latest_close,
            stop_loss=stop,
            target=target,
            confidence=0.65,
            rationale=(
                f"Supertrend({self.atr_period},{self.multiplier}) bearish, "
                f"50-DMA falling, line at {final_upper[-1]:.2f}"
            ),
            ts=datetime.utcnow(),
        )
