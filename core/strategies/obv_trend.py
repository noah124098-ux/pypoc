"""OBV (On-Balance Volume) trend confirmation strategy.

Fires when price makes a new N-day high AND OBV is also at a new N-day high
(volume confirms the breakout). Volume-confirmed breakouts have significantly
higher win rates than price-only breakouts on Nifty 50 stocks.

Works in TREND regime. Complements TrendBreakout with volume quality filter.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

import pandas as pd

from core.strategies.base import IStrategy
from core.strategies.indicators import atr
from core.types import Regime, Side, Signal


class ObvTrend(IStrategy):
    name = "obv_trend"
    regimes = [Regime.TREND]

    def __init__(
        self,
        breakout_period: int = 15,
        atr_period: int = 14,
        atr_stop_multiplier: float = 1.5,
        target_r_multiple: float = 2.5,
    ):
        self.breakout_period = breakout_period
        self.atr_period = atr_period
        self.atr_stop_multiplier = atr_stop_multiplier
        self.target_r_multiple = target_r_multiple

    def evaluate(self, symbol: str, candles: pd.DataFrame, regime: Regime) -> Optional[Signal]:
        if not self.supports(regime):
            return None
        min_bars = max(self.breakout_period, self.atr_period) + 3
        if len(candles) < min_bars:
            return None

        close = candles["close"]
        volume = candles["volume"]

        # Compute OBV
        obv = (volume * close.diff().apply(lambda x: 1 if x > 0 else (-1 if x < 0 else 0))).cumsum()

        latest_close = close.iloc[-1]
        prev_high_n = close.iloc[-(self.breakout_period + 1):-1].max()
        latest_obv = obv.iloc[-1]
        prev_obv_high_n = obv.iloc[-(self.breakout_period + 1):-1].max()

        if pd.isna(prev_high_n) or pd.isna(prev_obv_high_n):
            return None

        # Price must break the N-day high
        if latest_close <= prev_high_n:
            return None

        # OBV must also be at or above its N-day high (volume confirms)
        if latest_obv < prev_obv_high_n:
            return None

        # Only trade stocks in a trending (persistent) regime — autocorr > 0
        if len(close) >= 22:
            _ac = close.iloc[-20:].autocorr(lag=1)
            if not pd.isna(_ac) and _ac <= 0:
                return None  # stock is mean-reverting — breakout will likely fail

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
            confidence=0.7,
            rationale=(
                f"OBV-confirmed {self.breakout_period}d breakout: "
                f"price {latest_close:.2f} > {prev_high_n:.2f}, OBV {latest_obv:.0f} > {prev_obv_high_n:.0f}"
            ),
            ts=datetime.utcnow(),
        )
