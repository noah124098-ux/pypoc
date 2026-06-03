"""Supertrend short strategy with quality filters.

Complements the long-only Supertrend by capturing downside moves during VOLATILE
and correcting TREND regimes.

Quality filters (all required to fire):
  1. Supertrend indicator is bearish (direction == -1)
  2. 50-DMA is falling over a 10-bar lookback (confirmed medium-term downtrend)
  3. 10-day return < -3% (stock is actually moving down, not just drifting)
  4. Volume spike: latest volume >= 1.5x 20-day average (institutional distribution)
  5. Fresh signal: 50-DMA was NOT already falling 1 bar ago (avoids re-firing)

These filters target stocks that are in an active distribution phase with
high-conviction directional momentum — sharply reducing false shorts.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

import pandas as pd

from core.strategies.base import IStrategy
from core.strategies.indicators import atr, supertrend_bands, volume_ratio
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
        dma_falling_lookback: int = 10,
        return_lookback: int = 10,
        return_threshold: float = -0.03,
        volume_spike_min: float = 1.5,
        volume_avg_period: int = 20,
    ):
        self.atr_period = atr_period
        self.multiplier = multiplier
        self.target_r_multiple = target_r_multiple
        self.stock_dma_period = stock_dma_period
        self.dma_falling_lookback = dma_falling_lookback
        self.return_lookback = return_lookback
        self.return_threshold = return_threshold
        self.volume_spike_min = volume_spike_min
        self.volume_avg_period = volume_avg_period

    def evaluate(self, symbol: str, candles: pd.DataFrame, regime: Regime) -> Optional[Signal]:
        if not self.supports(regime):
            return None

        min_bars = max(
            self.atr_period,
            self.stock_dma_period + self.dma_falling_lookback,
            self.return_lookback,
            self.volume_avg_period,
        ) + 5
        if len(candles) < min_bars:
            return None

        close = candles["close"]

        # Filter 1: Supertrend must be bearish
        final_upper, final_lower, direction = supertrend_bands(
            candles, self.atr_period, self.multiplier
        )
        if direction[-1] != -1:
            return None  # Supertrend not currently bearish

        # Filter 2: 50-DMA must be falling over the past dma_falling_lookback bars
        dma = close.rolling(self.stock_dma_period).mean()
        dma_now = dma.iloc[-1]
        dma_lookback = dma.iloc[-(self.dma_falling_lookback + 1)]
        if pd.isna(dma_now) or pd.isna(dma_lookback):
            return None
        if dma_now >= dma_lookback:
            return None  # DMA not falling over lookback window

        # Filter 3: 10-day return must be worse than threshold (< -3%)
        if len(close) < self.return_lookback + 2:
            return None
        price_10d_ago = float(close.iloc[-(self.return_lookback + 1)])
        latest_close = float(close.iloc[-1])
        if price_10d_ago <= 0:
            return None
        ten_day_return = (latest_close - price_10d_ago) / price_10d_ago
        if ten_day_return >= self.return_threshold:
            return None  # Not falling fast enough

        # Filter 4: Volume spike — latest volume must be >= volume_spike_min * avg
        vol_rat = volume_ratio(candles, self.volume_avg_period)
        latest_vol_ratio = float(vol_rat.iloc[-1])
        if pd.isna(latest_vol_ratio) or latest_vol_ratio < self.volume_spike_min:
            return None  # No volume confirmation

        # Filter 5: Fresh signal — DMA was NOT already falling 1 bar ago
        # (avoids re-firing every day in a sustained downtrend)
        if len(dma.dropna()) >= self.dma_falling_lookback + 2:
            dma_prev = dma.iloc[-2]
            dma_prev_lookback = dma.iloc[-(self.dma_falling_lookback + 2)]
            if not pd.isna(dma_prev) and not pd.isna(dma_prev_lookback):
                dma_was_falling_yesterday = dma_prev < dma_prev_lookback
                if dma_was_falling_yesterday:
                    return None  # Already firing yesterday — skip

        latest_atr = float(atr(candles, self.atr_period).iloc[-1])
        if pd.isna(latest_atr) or latest_atr <= 0:
            return None

        # For a short: stop above entry (supertrend upper band + buffer), target below
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
                f"50-DMA falling {self.dma_falling_lookback}bar, "
                f"10d_return={ten_day_return:.1%}, "
                f"vol_ratio={latest_vol_ratio:.2f}x, "
                f"line at {final_upper[-1]:.2f}"
            ),
            ts=datetime.utcnow(),
        )
