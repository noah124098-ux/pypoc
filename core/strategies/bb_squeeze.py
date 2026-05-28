"""Bollinger Band squeeze breakout — fires when bands narrow then expand.

The squeeze: BB width drops below its N-bar minimum (Keltner-style), then
price breaks out of the upper band. Captures the compression-then-explosion
pattern across TREND and RANGE regimes. More frequent than pure Donchian
because it works in both trending and transitioning markets.

Works in TREND and RANGE regimes.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

import pandas as pd

from core.strategies.base import IStrategy
from core.strategies.indicators import atr
from core.types import Regime, Side, Signal


class BbSqueeze(IStrategy):
    name = "bb_squeeze"
    regimes = [Regime.TREND, Regime.RANGE]

    def __init__(
        self,
        bb_period: int = 20,
        bb_std: float = 2.0,
        squeeze_lookback: int = 20,
        atr_period: int = 14,
        atr_stop_multiplier: float = 1.5,
        target_r_multiple: float = 2.0,
        stock_dma_period: int = 50,
    ):
        self.bb_period = bb_period
        self.bb_std = bb_std
        self.squeeze_lookback = squeeze_lookback
        self.atr_period = atr_period
        self.atr_stop_multiplier = atr_stop_multiplier
        self.target_r_multiple = target_r_multiple
        self.stock_dma_period = stock_dma_period

    def evaluate(self, symbol: str, candles: pd.DataFrame, regime: Regime) -> Optional[Signal]:
        if not self.supports(regime):
            return None
        min_bars = max(self.bb_period, self.squeeze_lookback, self.atr_period) + 3
        if len(candles) < min_bars:
            return None

        close = candles["close"]

        # Per-stock trend filter: only buy if stock's 50-DMA is flat or rising.
        dma = close.rolling(self.stock_dma_period).mean()
        if not pd.isna(dma.iloc[-1]) and not pd.isna(dma.iloc[-6]):
            if dma.iloc[-1] < dma.iloc[-6] * 0.99:
                return None

        ma = close.rolling(self.bb_period).mean()
        sd = close.rolling(self.bb_period).std()
        upper = ma + self.bb_std * sd
        lower = ma - self.bb_std * sd
        bb_width = (upper - lower) / ma

        # Squeeze condition: previous bar was at or near lowest width in lookback.
        prev_width = bb_width.iloc[-2]
        min_width_lookback = bb_width.iloc[-(self.squeeze_lookback + 1):-1].min()
        if pd.isna(prev_width) or pd.isna(min_width_lookback):
            return None
        squeeze_active = prev_width <= min_width_lookback * 1.05  # within 5% of minimum

        if not squeeze_active:
            return None

        # Breakout: close breaks above upper band.
        latest_close = close.iloc[-1]
        latest_upper = upper.iloc[-1]
        prev_close = close.iloc[-2]
        prev_upper = upper.iloc[-2]

        if pd.isna(latest_upper) or pd.isna(prev_upper):
            return None
        if not (prev_close <= prev_upper and latest_close > latest_upper):
            return None  # must be fresh breakout, not already above band

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
                f"BB squeeze breakout: width {prev_width:.4f} was at {self.squeeze_lookback}-bar low, "
                f"now close {latest_close:.2f} > upper {latest_upper:.2f}"
            ),
            ts=datetime.utcnow(),
        )
