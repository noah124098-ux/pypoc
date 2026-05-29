"""RSI momentum strategy — pullback to mid-RSI in trending market.

Fires when RSI dips to 40-55 range (healthy pullback in uptrend) while
price is above a rising 50-EMA. Captures the "buy the dip" inside a trend
rather than waiting for extreme oversold (RSI < 30) which is rarer.

Works in TREND regime. Complements TrendBreakout by capturing trend
continuation entries after pullbacks, not just initial breakouts.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

import pandas as pd

from core.strategies.base import IStrategy
from core.strategies.indicators import atr, rsi, volume_ratio
from core.types import Regime, Side, Signal


class RsiMomentum(IStrategy):
    name = "rsi_momentum"
    regimes = [Regime.TREND]

    def __init__(
        self,
        rsi_period: int = 14,
        rsi_pullback_low: float = 40.0,
        rsi_pullback_high: float = 55.0,
        trend_ema_period: int = 50,
        atr_period: int = 14,
        atr_stop_multiplier: float = 1.5,
        target_r_multiple: float = 2.0,
        volume_confirm_ratio: float = 1.0,
    ):
        self.rsi_period = rsi_period
        self.rsi_pullback_low = rsi_pullback_low
        self.rsi_pullback_high = rsi_pullback_high
        self.trend_ema_period = trend_ema_period
        self.atr_period = atr_period
        self.atr_stop_multiplier = atr_stop_multiplier
        self.target_r_multiple = target_r_multiple
        self.volume_confirm_ratio = volume_confirm_ratio

    def evaluate(self, symbol: str, candles: pd.DataFrame, regime: Regime) -> Optional[Signal]:
        if not self.supports(regime):
            return None
        min_bars = max(self.rsi_period, self.trend_ema_period, self.atr_period) + 3
        if len(candles) < min_bars:
            return None

        close = candles["close"]
        latest_close = close.iloc[-1]

        trend_ema = close.ewm(span=self.trend_ema_period, adjust=False).mean()
        if latest_close <= trend_ema.iloc[-1]:
            return None  # price below trend EMA — not an uptrend

        # EMA must be rising (bullish trend filter)
        if trend_ema.iloc[-1] <= trend_ema.iloc[-5]:
            return None

        rsi_series = rsi(candles, self.rsi_period)
        latest_rsi = rsi_series.iloc[-1]
        if pd.isna(latest_rsi):
            return None
        if not (self.rsi_pullback_low <= latest_rsi <= self.rsi_pullback_high):
            return None

        # RSI must be recovering (rising from the pullback low)
        prev_rsi = rsi_series.iloc[-2]
        if not pd.isna(prev_rsi) and latest_rsi <= prev_rsi:
            return None  # still falling — wait for upturn

        vr = volume_ratio(candles).iloc[-1]
        if pd.isna(vr) or vr < self.volume_confirm_ratio:
            return None

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
            confidence=0.6,
            rationale=(
                f"RSI pullback {latest_rsi:.1f} in [{self.rsi_pullback_low},{self.rsi_pullback_high}], "
                f"price {latest_close:.2f} above EMA{self.trend_ema_period} {trend_ema.iloc[-1]:.2f}"
            ),
            ts=datetime.utcnow(),
        )
