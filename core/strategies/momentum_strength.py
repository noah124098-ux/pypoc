"""MomentumStrength strategy — RSI momentum zone with trend confirmation.

Fires when 20-day RSI is in the momentum zone (55-70, not overbought), price is
above a rising 50-DMA, and volume confirms with 1.2x above average. This targets
the "strong trend continuation" setup: RSI above 55 means the market is already
trending, not just recovering from oversold; the 50-DMA rising filter ensures the
trend has structural support.

Works in TREND regime. Complements RsiMomentum (which catches dip-buy setups at
RSI 40-55) by capturing the higher-conviction momentum phase at RSI 55-70.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

import pandas as pd

from core.strategies.base import IStrategy
from core.strategies.indicators import atr, rsi, volume_ratio
from core.types import Regime, Side, Signal


class MomentumStrength(IStrategy):
    name = "momentum_strength"
    regimes = [Regime.TREND]

    def __init__(
        self,
        rsi_period: int = 20,
        rsi_momentum_low: float = 55.0,
        rsi_momentum_high: float = 70.0,
        dma_period: int = 50,
        atr_period: int = 14,
        atr_stop_multiplier: float = 1.5,
        target_r_multiple: float = 2.5,
        volume_confirm_ratio: float = 1.2,
    ):
        self.rsi_period = rsi_period
        self.rsi_momentum_low = rsi_momentum_low
        self.rsi_momentum_high = rsi_momentum_high
        self.dma_period = dma_period
        self.atr_period = atr_period
        self.atr_stop_multiplier = atr_stop_multiplier
        self.target_r_multiple = target_r_multiple
        self.volume_confirm_ratio = volume_confirm_ratio

    def evaluate(self, symbol: str, candles: pd.DataFrame, regime: Regime) -> Optional[Signal]:
        if not self.supports(regime):
            return None

        min_bars = max(self.rsi_period, self.dma_period, self.atr_period) + 5
        if len(candles) < min_bars:
            return None

        close = candles["close"]
        latest_close = close.iloc[-1]

        # 50-DMA filter: price must be above a rising 50-DMA
        dma = close.rolling(self.dma_period).mean()
        latest_dma = dma.iloc[-1]
        if pd.isna(latest_dma):
            return None
        if latest_close <= latest_dma:
            return None  # price below 50-DMA — not in a trend

        # DMA must be rising (structural uptrend, not just a temporary spike)
        if len(dma.dropna()) < 6:
            return None
        if dma.iloc[-1] <= dma.iloc[-6]:
            return None  # 50-DMA flat or falling — trend lacks momentum

        # RSI must be in the momentum zone [55, 70]
        rsi_series = rsi(candles, self.rsi_period)
        latest_rsi = rsi_series.iloc[-1]
        if pd.isna(latest_rsi):
            return None
        if not (self.rsi_momentum_low <= latest_rsi <= self.rsi_momentum_high):
            return None

        # Volume confirmation: today's volume > 1.2x 20-day average
        vr = volume_ratio(candles).iloc[-1]
        if pd.isna(vr) or vr < self.volume_confirm_ratio:
            return None

        latest_atr = atr(candles, self.atr_period).iloc[-1]
        if pd.isna(latest_atr) or latest_atr <= 0:
            return None

        stop = latest_close - self.atr_stop_multiplier * latest_atr
        risk = latest_close - stop
        if risk <= 0:
            return None
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
                f"Momentum zone: RSI{self.rsi_period} {latest_rsi:.1f} in "
                f"[{self.rsi_momentum_low},{self.rsi_momentum_high}], "
                f"price {latest_close:.2f} above rising DMA{self.dma_period} {latest_dma:.2f}, "
                f"vol ratio {vr:.2f}x"
            ),
            ts=datetime.utcnow(),
        )
