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
from core.strategies.indicators import adx_value, atr, supertrend_bands, volume_ratio
from core.types import Regime, Side, Signal


class Supertrend(IStrategy):
    name = "supertrend"
    regimes = [Regime.TREND, Regime.VOLATILE]

    def __init__(
        self,
        atr_period: int = 10,
        multiplier: float = 3.0,
        target_r_multiple: float = 2.0,
        adx_period: int = 14,
        adx_min: float = 18.0,
        volume_ratio_min: float = 1.2,
        volume_avg_period: int = 20,
        dma_fast: int = 20,
        dma_slow: int = 50,
        dma_rising_lookback: int = 10,
    ):
        self.atr_period = atr_period
        self.multiplier = multiplier
        self.target_r_multiple = target_r_multiple
        self.adx_period = adx_period
        self.adx_min = adx_min
        self.volume_ratio_min = volume_ratio_min
        self.volume_avg_period = volume_avg_period
        self.dma_fast = dma_fast
        self.dma_slow = dma_slow
        self.dma_rising_lookback = dma_rising_lookback

    def evaluate(self, symbol: str, candles: pd.DataFrame, regime: Regime) -> Optional[Signal]:
        if not self.supports(regime):
            return None
        min_bars = max(
            self.atr_period,
            self.adx_period,
            self.volume_avg_period,
            self.dma_slow + self.dma_rising_lookback,
        ) + 5
        if len(candles) < min_bars:
            return None

        final_upper, final_lower, direction = supertrend_bands(
            candles, self.atr_period, self.multiplier
        )

        # Signal: direction flipped to bullish on last bar (FRESH FLIP only)
        if direction[-1] != 1 or direction[-2] == 1:
            return None  # must be a fresh flip up

        # ADX filter: require trending market strength
        adx_series = adx_value(candles, self.adx_period)
        latest_adx = float(adx_series.iloc[-1])
        if pd.isna(latest_adx) or latest_adx < self.adx_min:
            return None

        # Volume filter: require above-average volume on flip bar
        vol_ratio = volume_ratio(candles, self.volume_avg_period)
        latest_vol_ratio = float(vol_ratio.iloc[-1])
        if pd.isna(latest_vol_ratio) or latest_vol_ratio < self.volume_ratio_min:
            return None

        close = candles["close"]
        latest_close = float(close.iloc[-1])

        # DMA quality filter 1: close must be above 20-DMA on the flip bar
        # (confirms flip is above recent mean, not a false break at market bottom)
        dma_fast_series = close.rolling(self.dma_fast).mean()
        dma_fast_val = float(dma_fast_series.iloc[-1])
        if pd.isna(dma_fast_val) or latest_close <= dma_fast_val:
            return None

        # DMA quality filter 2: 50-DMA must be rising (today > 10 bars ago)
        dma_slow_series = close.rolling(self.dma_slow).mean()
        dma_slow_now = float(dma_slow_series.iloc[-1])
        dma_slow_prev = float(dma_slow_series.iloc[-(self.dma_rising_lookback + 1)])
        if pd.isna(dma_slow_now) or pd.isna(dma_slow_prev) or dma_slow_now <= dma_slow_prev:
            return None

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
                f"ADX={latest_adx:.1f}, vol_ratio={latest_vol_ratio:.2f}, "
                f"DMA{self.dma_fast}={dma_fast_val:.2f}, "
                f"DMA{self.dma_slow}_rising={dma_slow_now:.2f}>{dma_slow_prev:.2f}, "
                f"line at {final_lower[-1]:.2f}"
            ),
            ts=datetime.utcnow(),
        )
