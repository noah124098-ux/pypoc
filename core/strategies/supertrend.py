"""Supertrend indicator strategy.

Supertrend is one of the best-performing trend-following indicators on Indian
markets (widely used by NSE algo traders). It generates a clear BUY when
price flips above the supertrend line, and maintains the signal as long as
price stays above it.

Works in TREND and VOLATILE regimes. High accuracy on Nifty 50 daily bars.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

import pandas as pd

from core.strategies.base import IStrategy
from core.strategies.indicators import atr
from core.types import Regime, Side, Signal


class Supertrend(IStrategy):
    name = "supertrend"
    regimes = [Regime.TREND, Regime.VOLATILE]

    def __init__(
        self,
        atr_period: int = 10,
        multiplier: float = 3.0,
        target_r_multiple: float = 2.0,
    ):
        self.atr_period = atr_period
        self.multiplier = multiplier
        self.target_r_multiple = target_r_multiple

    def evaluate(self, symbol: str, candles: pd.DataFrame, regime: Regime) -> Optional[Signal]:
        if not self.supports(regime):
            return None
        if len(candles) < self.atr_period + 5:
            return None

        hl2 = (candles["high"] + candles["low"]) / 2
        a = atr(candles, self.atr_period)

        upper_band = hl2 + self.multiplier * a
        lower_band = hl2 - self.multiplier * a

        # Compute supertrend direction iteratively over last N bars.
        close = candles["close"]
        n = len(candles)

        final_upper = upper_band.copy()
        final_lower = lower_band.copy()
        supertrend = pd.Series(index=candles.index, dtype=float)
        direction = pd.Series(index=candles.index, dtype=int)

        for i in range(1, n):
            # Final upper band
            if upper_band.iloc[i] < final_upper.iloc[i - 1] or close.iloc[i - 1] > final_upper.iloc[i - 1]:
                final_upper.iloc[i] = upper_band.iloc[i]
            else:
                final_upper.iloc[i] = final_upper.iloc[i - 1]

            # Final lower band
            if lower_band.iloc[i] > final_lower.iloc[i - 1] or close.iloc[i - 1] < final_lower.iloc[i - 1]:
                final_lower.iloc[i] = lower_band.iloc[i]
            else:
                final_lower.iloc[i] = final_lower.iloc[i - 1]

            # Direction: 1 = bullish (price > lower band), -1 = bearish
            if close.iloc[i] > final_upper.iloc[i - 1]:
                direction.iloc[i] = 1
            elif close.iloc[i] < final_lower.iloc[i - 1]:
                direction.iloc[i] = -1
            else:
                direction.iloc[i] = direction.iloc[i - 1] if i > 0 else 1

            supertrend.iloc[i] = final_lower.iloc[i] if direction.iloc[i] == 1 else final_upper.iloc[i]

        # Signal: direction flipped to bullish on last bar
        if direction.iloc[-1] != 1 or direction.iloc[-2] == 1:
            return None  # must be a fresh flip up

        latest_close = close.iloc[-1]
        latest_atr = a.iloc[-1]
        if pd.isna(latest_atr) or latest_atr <= 0:
            return None

        # Stop just below supertrend line
        stop = float(supertrend.iloc[-1]) - 0.1 * latest_atr
        if stop >= latest_close:
            return None
        risk = latest_close - stop
        target = latest_close + self.target_r_multiple * risk

        return Signal(
            symbol=symbol,
            side=Side.BUY,
            strategy=self.name,
            regime=regime,
            entry_price=latest_close,
            stop_loss=stop,
            target=target,
            confidence=0.7,
            rationale=(
                f"Supertrend({self.atr_period},{self.multiplier}) flipped bullish, "
                f"line at {supertrend.iloc[-1]:.2f}"
            ),
            ts=datetime.utcnow(),
        )
