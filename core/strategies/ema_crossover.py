"""EMA crossover strategy — fast EMA crosses above slow EMA with volume confirmation.

Works in TREND regime. More frequent signals than Donchian breakout because it
fires on the crossover itself rather than waiting for a 20-day high breakout.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

import pandas as pd

from core.strategies.base import IStrategy
from core.strategies.indicators import atr
from core.types import Regime, Side, Signal


class EmaCrossover(IStrategy):
    name = "ema_crossover"
    regimes = [Regime.TREND]

    def __init__(
        self,
        fast_period: int = 9,
        slow_period: int = 21,
        atr_period: int = 14,
        atr_stop_multiplier: float = 1.5,
        target_r_multiple: float = 2.0,
        volume_confirmation: bool = True,
    ):
        self.fast_period = fast_period
        self.slow_period = slow_period
        self.atr_period = atr_period
        self.atr_stop_multiplier = atr_stop_multiplier
        self.target_r_multiple = target_r_multiple
        self.volume_confirmation = volume_confirmation

    def evaluate(self, symbol: str, candles: pd.DataFrame, regime: Regime) -> Optional[Signal]:
        if not self.supports(regime):
            return None
        min_bars = max(self.slow_period, self.atr_period) + 3
        if len(candles) < min_bars:
            return None

        close = candles["close"]
        fast_ema = close.ewm(span=self.fast_period, adjust=False).mean()
        slow_ema = close.ewm(span=self.slow_period, adjust=False).mean()

        # Crossover: fast was below slow yesterday, is above today.
        prev_cross = fast_ema.iloc[-2] <= slow_ema.iloc[-2]
        curr_cross = fast_ema.iloc[-1] > slow_ema.iloc[-1]
        if not (prev_cross and curr_cross):
            return None

        # Volume confirmation: today's volume above 20-bar average.
        if self.volume_confirmation and len(candles) >= 20:
            avg_vol = candles["volume"].iloc[-20:].mean()
            if avg_vol > 0 and candles["volume"].iloc[-1] < avg_vol:
                return None

        latest_close = close.iloc[-1]
        latest_atr = atr(candles, self.atr_period).iloc[-1]
        if pd.isna(latest_atr) or latest_atr <= 0:
            return None

        stop = latest_close - self.atr_stop_multiplier * latest_atr
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
            confidence=0.65,
            rationale=(
                f"EMA{self.fast_period} {fast_ema.iloc[-1]:.2f} crossed above "
                f"EMA{self.slow_period} {slow_ema.iloc[-1]:.2f}"
            ),
            ts=datetime.utcnow(),
        )
