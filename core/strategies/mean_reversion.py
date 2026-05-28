"""RSI + Bollinger lower-band reversion. Active in RANGE regime."""
from __future__ import annotations

from datetime import datetime
from typing import Optional

import pandas as pd

from core.strategies.base import IStrategy
from core.strategies.indicators import bollinger, rsi
from core.types import Regime, Side, Signal


class MeanReversion(IStrategy):
    name = "mean_reversion"
    regimes = [Regime.RANGE]

    def __init__(
        self,
        rsi_period: int = 14,
        rsi_oversold: float = 30,
        rsi_overbought: float = 70,
        bb_period: int = 20,
        bb_std: float = 2.0,
        stock_dma_period: int = 50,
    ):
        self.rsi_period = rsi_period
        self.rsi_oversold = rsi_oversold
        self.rsi_overbought = rsi_overbought
        self.bb_period = bb_period
        self.bb_std = bb_std
        self.stock_dma_period = stock_dma_period

    def evaluate(self, symbol: str, candles: pd.DataFrame, regime: Regime) -> Optional[Signal]:
        if not self.supports(regime):
            return None
        if len(candles) < max(self.rsi_period, self.bb_period, self.stock_dma_period) + 2:
            return None

        # Per-stock trend filter: only buy if stock's own 50-DMA is flat or rising.
        # A falling 50-DMA means a downtrend -- oversold RSI is continuation, not reversion.
        close_series = candles["close"]
        dma = close_series.rolling(self.stock_dma_period).mean()
        if not pd.isna(dma.iloc[-1]) and not pd.isna(dma.iloc[-6]):
            if dma.iloc[-1] < dma.iloc[-6] * 0.99:  # DMA falling >1% over 5 days
                return None

        r = rsi(candles, self.rsi_period)
        ma, upper, lower = bollinger(candles, self.bb_period, self.bb_std)

        close = close_series.iloc[-1]
        latest_rsi = r.iloc[-1]
        latest_lower = lower.iloc[-1]
        latest_ma = ma.iloc[-1]
        if pd.isna(latest_rsi) or pd.isna(latest_lower):
            return None

        if latest_rsi < self.rsi_oversold and close <= latest_lower * 1.005:
            stop = latest_lower * 0.98
            target = latest_ma
            if target <= close or stop >= close:
                return None
            return Signal(
                symbol=symbol,
                side=Side.BUY,
                strategy=self.name,
                regime=regime,
                entry_price=close,
                stop_loss=stop,
                target=target,
                confidence=0.55,
                rationale=f"RSI {latest_rsi:.1f} oversold + at/below BB lower",
                ts=datetime.utcnow(),
            )
        return None
