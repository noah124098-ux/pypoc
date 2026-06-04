"""VolumeBreakoutConfirm strategy.

Fires when ALL of the following align on the same bar:
1. Stock makes a new 10-day high (Donchian upper breach, looking at yesterday's level
   to avoid look-ahead) — institutional accumulation zone.
2. Volume is > 2.5x the 20-day average — unusually heavy institutional buying.
3. Close is in the upper 20% of the bar's high-low range — strong close, no rejection.
4. Price > 50-DMA — confirmed uptrend; no counter-trend entries.

This is deliberately stricter than TrendBreakout (2.5x volume vs 1.2x, plus the
close-quality and 50-DMA filters). It targets rare, high-conviction institutional
breakouts rather than generic Donchian breaks. Suitable for TREND regime only.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

import pandas as pd

from core.strategies.base import IStrategy
from core.strategies.indicators import atr, donchian
from core.types import Regime, Side, Signal


class VolumeBreakoutConfirm(IStrategy):
    name = "volume_breakout_confirm"
    regimes = [Regime.TREND]

    def __init__(
        self,
        donchian_period: int = 10,
        volume_avg_period: int = 20,
        volume_spike_min: float = 2.5,
        close_quality_pct: float = 0.80,   # close must be in top 20% of bar range
        dma_period: int = 50,
        atr_period: int = 14,
        atr_stop_multiplier: float = 1.5,
        target_r_multiple: float = 2.0,
    ):
        self.donchian_period = donchian_period
        self.volume_avg_period = volume_avg_period
        self.volume_spike_min = volume_spike_min
        self.close_quality_pct = close_quality_pct
        self.dma_period = dma_period
        self.atr_period = atr_period
        self.atr_stop_multiplier = atr_stop_multiplier
        self.target_r_multiple = target_r_multiple

    def evaluate(self, symbol: str, candles: pd.DataFrame, regime: Regime) -> Optional[Signal]:
        if not self.supports(regime):
            return None

        min_bars = max(self.donchian_period, self.volume_avg_period, self.dma_period, self.atr_period) + 3
        if len(candles) < min_bars:
            return None

        close = candles["close"]
        high = candles["high"]
        low = candles["low"]
        volume = candles["volume"]

        latest_close = float(close.iloc[-1])
        latest_high = float(high.iloc[-1])
        latest_low = float(low.iloc[-1])

        # Filter 1: price > 50-DMA (confirmed uptrend)
        dma_series = close.rolling(self.dma_period).mean()
        dma_val = float(dma_series.iloc[-1])
        if pd.isna(dma_val) or latest_close <= dma_val:
            return None

        # Filter 2: close must be above yesterday's Donchian upper (new 10-day high)
        upper, _ = donchian(candles, self.donchian_period)
        prev_upper = float(upper.iloc[-2])   # yesterday's Donchian high — no look-ahead
        if pd.isna(prev_upper) or latest_close <= prev_upper:
            return None

        # Filter 3: volume > 2.5x 20-day average
        vol_avg = volume.rolling(self.volume_avg_period).mean()
        vol_avg_val = float(vol_avg.iloc[-1])
        if pd.isna(vol_avg_val) or vol_avg_val <= 0:
            return None
        vol_ratio = float(volume.iloc[-1]) / vol_avg_val
        if vol_ratio < self.volume_spike_min:
            return None

        # Filter 4: strong close — close in top (1 - close_quality_pct) of bar range
        bar_range = latest_high - latest_low
        if bar_range > 0:
            close_position = (latest_close - latest_low) / bar_range
            if close_position < self.close_quality_pct:
                return None
        # If bar range is ~0 (doji), treat as a non-event and skip
        else:
            return None

        # ATR-based stop and R-multiple target
        latest_atr = float(atr(candles, self.atr_period).iloc[-1])
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
            confidence=0.70,
            rationale=(
                f"Volume breakout: {vol_ratio:.1f}x avg vol, "
                f"close {latest_close:.2f} above Donchian({self.donchian_period}) "
                f"upper {prev_upper:.2f}, "
                f"close quality {close_position:.0%}, "
                f"above DMA{self.dma_period} {dma_val:.2f}"
            ),
            ts=datetime.utcnow(),
        )
