"""NR7 (narrowest range in 7 bars) breakout. Active in VOLATILE regime as a compression-then-burst play."""
from __future__ import annotations

from datetime import datetime
from typing import Optional

import pandas as pd

from core.strategies.base import IStrategy
from core.strategies.indicators import atr
from core.types import Regime, Side, Signal


class VolatilityCompression(IStrategy):
    name = "volatility_compression"
    regimes = [Regime.VOLATILE]

    def __init__(self, nr_lookback: int = 7):
        self.nr_lookback = nr_lookback

    def evaluate(self, symbol: str, candles: pd.DataFrame, regime: Regime) -> Optional[Signal]:
        if not self.supports(regime):
            return None
        if len(candles) < self.nr_lookback + 5:
            return None

        ranges = candles["high"] - candles["low"]
        last_n = ranges.iloc[-self.nr_lookback:]
        if last_n.iloc[-1] != last_n.min():
            return None  # most recent bar must be the narrowest

        a = atr(candles, period=14).iloc[-1]
        prev_high = candles["high"].iloc[-2]
        close = candles["close"].iloc[-1]

        if pd.isna(a) or a <= 0:
            return None
        if close > prev_high:
            stop = close - 1.5 * a
            target = close + 2.0 * (close - stop)
            return Signal(
                symbol=symbol,
                side=Side.BUY,
                strategy=self.name,
                regime=regime,
                entry_price=close,
                stop_loss=stop,
                target=target,
                confidence=0.5,
                rationale=f"NR{self.nr_lookback} compression then break above {prev_high:.2f}",
                ts=datetime.utcnow(),
            )
        return None
