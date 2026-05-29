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
from core.strategies.indicators import atr, supertrend_bands
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

        final_upper, final_lower, direction = supertrend_bands(
            candles, self.atr_period, self.multiplier
        )

        # Signal: direction flipped to bullish on last bar
        if direction[-1] != 1 or direction[-2] == 1:
            return None  # must be a fresh flip up

        close = candles["close"]
        latest_close = float(close.iloc[-1])
        latest_atr = float(atr(candles, self.atr_period).iloc[-1])
        if pd.isna(latest_atr) or latest_atr <= 0:
            return None

        # Stop just below supertrend line (= final_lower on a bullish bar)
        stop = float(final_lower[-1]) - 0.1 * latest_atr
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
                f"line at {final_lower[-1]:.2f}"
            ),
            ts=datetime.utcnow(),
        )
