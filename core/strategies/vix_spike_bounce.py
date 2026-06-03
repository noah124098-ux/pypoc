"""VixSpikeBounce strategy — fear-spike reversal in VOLATILE regime.

Fires after India VIX spikes then retreats, exploiting the "fear spike reversal"
pattern that dominates W3 correction+recovery markets.

Conditions for a signal:
  1. Regime is VOLATILE (elevated VIX — classification already confirms high fear)
  2. RSI < 35 (oversold during the spike — market sold off aggressively)
  3. Price above 200-DMA (structural uptrend intact — no catching falling knives)
  4. Bullish reversal bar — close > open (green candle) AND close > previous close
  5. Volume >= 1.5x 20-day average (high-volume reversal = institutional conviction)

Stop: 2x ATR below entry (wider stop needed in volatile conditions)
Target: 1.5R (conservative, quick profit taking — don't overstay in volatile markets)

Designed to be the primary alpha source during VOLATILE regime in W3-style
correction + recovery markets where fear spikes are followed by sharp bounces.
Differs from RsiBounce (RANGE/VOLATILE, RSI < 30) by:
  - VOLATILE-only (more targeted)
  - Less extreme RSI threshold (35 vs 30) — catches earlier in the bounce
  - Wider stop (2x ATR vs 1.5x ATR) — accommodates volatile price action
  - Lower R-target (1.5R vs 2.0R) — quick profit taking in volatile regimes
  - Stricter volume requirement (1.5x vs 1.2x) — need conviction for entry
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

import pandas as pd

from core.strategies.base import IStrategy
from core.strategies.indicators import atr, rsi, volume_ratio
from core.types import Regime, Side, Signal


class VixSpikeBounce(IStrategy):
    name = "vix_spike_bounce"
    regimes = [Regime.VOLATILE]

    def __init__(
        self,
        rsi_period: int = 14,
        rsi_oversold_threshold: float = 35.0,
        dma_period: int = 200,
        atr_period: int = 14,
        atr_stop_multiplier: float = 2.0,
        target_r_multiple: float = 1.5,
        volume_ratio_min: float = 1.5,
    ):
        self.rsi_period = rsi_period
        self.rsi_oversold_threshold = rsi_oversold_threshold
        self.dma_period = dma_period
        self.atr_period = atr_period
        self.atr_stop_multiplier = atr_stop_multiplier
        self.target_r_multiple = target_r_multiple
        self.volume_ratio_min = volume_ratio_min

    def evaluate(self, symbol: str, candles: pd.DataFrame, regime: Regime) -> Optional[Signal]:
        if not self.supports(regime):
            return None

        min_bars = max(self.rsi_period, self.dma_period, self.atr_period) + 3
        if len(candles) < min_bars:
            return None

        close = candles["close"]
        open_ = candles["open"]

        # Filter 1: price must be above 200-DMA (structural uptrend — no catching knives)
        dma_200 = close.rolling(self.dma_period).mean()
        latest_dma = dma_200.iloc[-1]
        latest_close = close.iloc[-1]
        if pd.isna(latest_dma) or latest_close <= latest_dma:
            return None

        # Filter 2: RSI < oversold threshold (market sold off during the VIX spike)
        rsi_series = rsi(candles, self.rsi_period)
        latest_rsi = rsi_series.iloc[-1]
        if pd.isna(latest_rsi) or latest_rsi >= self.rsi_oversold_threshold:
            return None

        # Filter 3: bullish reversal bar — close > open (green candle) AND close > prev close
        latest_open = open_.iloc[-1]
        prev_close = close.iloc[-2]
        if latest_close <= latest_open:
            return None  # not a green/bullish candle
        if latest_close <= prev_close:
            return None  # not closing higher than yesterday

        # Filter 4: volume confirmation — high-volume reversal signals institutional conviction
        vr = volume_ratio(candles).iloc[-1]
        if pd.isna(vr) or vr < self.volume_ratio_min:
            return None

        # ATR-based stop (wider) and target (conservative)
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
            confidence=0.70,
            rationale=(
                f"VIX spike bounce: RSI {latest_rsi:.1f} < {self.rsi_oversold_threshold}, "
                f"price {latest_close:.2f} above 200-DMA {latest_dma:.2f}, "
                f"bullish reversal bar, vol ratio {vr:.2f}x (>={self.volume_ratio_min}x)"
            ),
            ts=datetime.utcnow(),
        )
