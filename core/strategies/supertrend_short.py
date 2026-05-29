"""Supertrend short strategy -- fires when Supertrend flips bearish on a downtrending stock.

Complements the long-only Supertrend by capturing downside moves during VOLATILE
and correcting TREND regimes. Only fires when the stock's own 50-DMA is falling
(confirmed downtrend) -- avoids false shorts in choppy markets.

Works in VOLATILE and TREND regimes.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

import pandas as pd

from core.strategies.base import IStrategy
from core.strategies.indicators import atr
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
        min_bars = max(self.atr_period, self.stock_dma_period) + 5
        if len(candles) < min_bars:
            return None

        close = candles["close"]

        # Only short stocks with a confirmed falling 50-DMA (downtrend).
        dma = close.rolling(self.stock_dma_period).mean()
        if pd.isna(dma.iloc[-1]) or pd.isna(dma.iloc[-6]):
            return None
        if dma.iloc[-1] >= dma.iloc[-6] * 0.99:
            return None  # DMA not falling -- no short

        hl2 = (candles["high"] + candles["low"]) / 2
        a = atr(candles, self.atr_period)

        upper_band = hl2 + self.multiplier * a
        lower_band = hl2 - self.multiplier * a

        n = len(candles)
        final_upper = upper_band.copy()
        final_lower = lower_band.copy()
        supertrend = pd.Series(index=candles.index, dtype=float)
        direction = pd.Series(index=candles.index, dtype=int)

        for i in range(1, n):
            if upper_band.iloc[i] < final_upper.iloc[i - 1] or close.iloc[i - 1] > final_upper.iloc[i - 1]:
                final_upper.iloc[i] = upper_band.iloc[i]
            else:
                final_upper.iloc[i] = final_upper.iloc[i - 1]

            if lower_band.iloc[i] > final_lower.iloc[i - 1] or close.iloc[i - 1] < final_lower.iloc[i - 1]:
                final_lower.iloc[i] = lower_band.iloc[i]
            else:
                final_lower.iloc[i] = final_lower.iloc[i - 1]

            if close.iloc[i] > final_upper.iloc[i - 1]:
                direction.iloc[i] = 1
            elif close.iloc[i] < final_lower.iloc[i - 1]:
                direction.iloc[i] = -1
            else:
                direction.iloc[i] = direction.iloc[i - 1] if i > 0 else 1

            supertrend.iloc[i] = final_lower.iloc[i] if direction.iloc[i] == 1 else final_upper.iloc[i]

        # Signal: direction flipped to bearish on last bar
        if direction.iloc[-1] != -1 or direction.iloc[-2] == -1:
            return None  # must be a fresh flip down

        latest_close = close.iloc[-1]
        latest_atr = a.iloc[-1]
        if pd.isna(latest_atr) or latest_atr <= 0:
            return None

        # For a short: stop above entry (supertrend line + buffer), target below
        stop = float(supertrend.iloc[-1]) + 0.1 * latest_atr
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
                f"Supertrend({self.atr_period},{self.multiplier}) flipped bearish, "
                f"50-DMA falling, line at {supertrend.iloc[-1]:.2f}"
            ),
            ts=datetime.utcnow(),
        )
