"""RSI Oversold Bounce strategy — recovery trades in RANGE/VOLATILE regime.

Fires when:
  1. RSI < rsi_oversold_threshold (default 30) — deeply oversold
  2. Price is above the 200-DMA — structural uptrend intact (no catching falling knives)
  3. Bullish reversal bar — current close > open AND close > previous close
  4. Volume confirmation — current volume >= volume_ratio_min × 20-day average

Designed for W3 (correction + recovery) market conditions where RANGE/VOLATILE
regimes dominate and mean-reversion bounces from oversold extremes are the
primary alpha source.

Works in RANGE and VOLATILE regimes. Complements MeanReversion (which uses
RSI 35 + BB lower band) by operating at more extreme oversold levels (RSI < 30)
and requiring an explicit bullish reversal candle, making it more selective but
higher-confidence in volatile/recovering markets.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

import pandas as pd

from core.strategies.base import IStrategy
from core.strategies.indicators import atr, rsi, volume_ratio
from core.types import Regime, Side, Signal


class RsiBounce(IStrategy):
    name = "rsi_bounce"
    regimes = [Regime.RANGE, Regime.VOLATILE]

    def __init__(
        self,
        rsi_period: int = 14,
        rsi_oversold_threshold: float = 30.0,
        dma_period: int = 200,
        atr_period: int = 14,
        atr_stop_multiplier: float = 1.5,
        target_r_multiple: float = 2.0,
        volume_ratio_min: float = 1.2,
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

        # Filter 2: RSI < oversold threshold
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

        # Filter 4: volume confirmation — current volume >= volume_ratio_min × 20-day avg
        vr = volume_ratio(candles).iloc[-1]
        if pd.isna(vr) or vr < self.volume_ratio_min:
            return None

        # ATR-based stop and target
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
                f"RSI oversold bounce: RSI {latest_rsi:.1f} < {self.rsi_oversold_threshold}, "
                f"price {latest_close:.2f} above 200-DMA {latest_dma:.2f}, "
                f"bullish reversal bar, vol ratio {vr:.2f}x"
            ),
            ts=datetime.utcnow(),
        )
