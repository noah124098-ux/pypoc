"""NR7 Pattern Breakout strategy — volatility compression before expansion.

Fires when:
  1. The last bar has the smallest high-low range among the past 7 bars (NR7 condition)
  2. The close is in the upper 30% of the bar's range (bullish close — buyers in control)
  3. Today's close breaks above yesterday's high (momentum confirmation)

NR7 (Narrow Range 7) captures periods of volatility compression. When the market
contracts into a NR7 day with a bullish close that also breaks above the prior day's
high, a volatility expansion in the direction of the breakout is highly probable.

Works in TREND and RANGE regimes. In TREND it captures continuation setups after
brief consolidation. In RANGE it identifies the leading edge of range breakouts.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

import pandas as pd

from core.strategies.base import IStrategy
from core.strategies.indicators import atr
from core.types import Regime, Side, Signal


class NRPatternBreakout(IStrategy):
    name = "nr_pattern"
    regimes = [Regime.TREND, Regime.RANGE]

    def __init__(
        self,
        nr_lookback: int = 7,
        bullish_close_pct: float = 0.30,
        atr_period: int = 14,
        target_r_multiple: float = 2.0,
    ):
        self.nr_lookback = nr_lookback
        self.bullish_close_pct = bullish_close_pct
        self.atr_period = atr_period
        self.target_r_multiple = target_r_multiple

    def evaluate(self, symbol: str, candles: pd.DataFrame, regime: Regime) -> Optional[Signal]:
        if not self.supports(regime):
            return None

        min_bars = max(self.nr_lookback, self.atr_period) + 3
        if len(candles) < min_bars:
            return None

        high = candles["high"]
        low = candles["low"]
        close = candles["close"]

        # Compute ranges for the last nr_lookback bars (inclusive of current bar)
        ranges = high - low
        current_range = ranges.iloc[-1]
        lookback_ranges = ranges.iloc[-self.nr_lookback:]

        if current_range <= 0:
            return None

        # NR7: current bar must have the smallest range in the lookback window
        if current_range >= lookback_ranges.min():
            # Only fire if strictly narrowest (ties are excluded to avoid noise)
            if current_range > lookback_ranges.min() or (lookback_ranges == current_range).sum() > 1:
                return None

        current_high = high.iloc[-1]
        current_low = low.iloc[-1]
        current_close = close.iloc[-1]

        # Bullish close: close must be in upper 30% of the NR7 bar's range
        # Upper 30% means close >= low + 0.70 * range
        bullish_threshold = current_low + (1.0 - self.bullish_close_pct) * current_range
        if current_close < bullish_threshold:
            return None

        # Breakout confirmation: close must be above yesterday's high
        prev_high = high.iloc[-2]
        if current_close <= prev_high:
            return None

        latest_atr = atr(candles, self.atr_period).iloc[-1]
        if pd.isna(latest_atr) or latest_atr <= 0:
            return None

        # Stop: low of the NR7 bar (the compression floor)
        stop = current_low
        risk = current_close - stop
        if risk <= 0:
            return None
        target = current_close + self.target_r_multiple * risk

        return Signal(
            symbol=symbol,
            side=Side.BUY,
            strategy=self.name,
            regime=regime,
            entry_price=current_close,
            stop_loss=stop,
            target=target,
            confidence=0.60,
            rationale=(
                f"NR{self.nr_lookback} breakout: range {current_range:.2f} narrowest in "
                f"{self.nr_lookback} bars, close {current_close:.2f} above prev high "
                f"{prev_high:.2f}, bullish close pct "
                f"{(current_close - current_low) / current_range * 100:.0f}%"
            ),
            ts=datetime.utcnow(),
        )
