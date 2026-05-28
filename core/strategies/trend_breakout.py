"""Donchian breakout with ATR-based stop and R-multiple target. Active in TREND regime."""
from __future__ import annotations

from datetime import datetime
from typing import Optional

import pandas as pd

from core.strategies.base import IStrategy
from core.strategies.indicators import atr, donchian, volume_ratio
from core.types import Regime, Side, Signal


class TrendBreakout(IStrategy):
    name = "trend_breakout"
    regimes = [Regime.TREND]

    def __init__(
        self,
        donchian_period: int = 20,
        atr_period: int = 14,
        atr_stop_multiplier: float = 2.0,
        target_r_multiple: float = 2.5,
        volume_confirm_ratio: float = 1.0,
    ):
        self.donchian_period = donchian_period
        self.atr_period = atr_period
        self.atr_stop_multiplier = atr_stop_multiplier
        self.target_r_multiple = target_r_multiple
        self.volume_confirm_ratio = volume_confirm_ratio

    def evaluate(self, symbol: str, candles: pd.DataFrame, regime: Regime) -> Optional[Signal]:
        if not self.supports(regime):
            return None
        if len(candles) < max(self.donchian_period, self.atr_period) + 2:
            return None

        upper, _ = donchian(candles, self.donchian_period)
        a = atr(candles, self.atr_period)

        latest_close = candles["close"].iloc[-1]
        prev_upper = upper.iloc[-2]   # avoid look-ahead — use yesterday's level
        latest_atr = a.iloc[-1]
        if pd.isna(prev_upper) or pd.isna(latest_atr) or latest_atr <= 0:
            return None

        vr = volume_ratio(candles).iloc[-1]
        if pd.isna(vr) or vr < self.volume_confirm_ratio:
            return None

        if latest_close > prev_upper:
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
                confidence=0.6,
                rationale=f"Donchian breakout above {prev_upper:.2f}, ATR {latest_atr:.2f}",
                ts=datetime.utcnow(),
            )
        return None
