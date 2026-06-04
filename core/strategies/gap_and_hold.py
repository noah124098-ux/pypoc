"""GapAndHold strategy.

Fires when institutional-driven gap-ups sustain (not filled) at close:
1. Today's open > yesterday's close by > 0.5% (meaningful gap up, not noise).
2. Today's close >= today's open (gap held — not filled; bullish structure).
3. Volume > 1.5x the 20-day average on the gap day (institutional participation).

The signal fires at day-close after confirming the gap held — entry is on the
NEXT day's open (standard daily-bar backtest model: signal on day D, fill on D+1).

Works in TREND and RANGE regimes. Gap-ups occur in both trending and consolidating
markets. VOLATILE regime is excluded — gaps in volatile markets are unreliable and
often fade intraday.

Stop placed below the gap level (yesterday's close) with a small ATR buffer.
Target at 2x risk.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

import pandas as pd

from core.strategies.base import IStrategy
from core.strategies.indicators import atr
from core.types import Regime, Side, Signal


class GapAndHold(IStrategy):
    name = "gap_and_hold"
    regimes = [Regime.TREND, Regime.RANGE]

    def __init__(
        self,
        gap_pct_min: float = 0.005,       # minimum gap size (0.5%)
        volume_avg_period: int = 20,
        volume_ratio_min: float = 1.5,
        atr_period: int = 14,
        atr_stop_buffer: float = 0.5,     # ATR fraction below the gap level for stop
        target_r_multiple: float = 2.0,
    ):
        self.gap_pct_min = gap_pct_min
        self.volume_avg_period = volume_avg_period
        self.volume_ratio_min = volume_ratio_min
        self.atr_period = atr_period
        self.atr_stop_buffer = atr_stop_buffer
        self.target_r_multiple = target_r_multiple

    def evaluate(self, symbol: str, candles: pd.DataFrame, regime: Regime) -> Optional[Signal]:
        if not self.supports(regime):
            return None

        min_bars = max(self.volume_avg_period, self.atr_period) + 3
        if len(candles) < min_bars:
            return None

        close = candles["close"]
        open_ = candles["open"]
        volume = candles["volume"]

        latest_close = float(close.iloc[-1])
        latest_open = float(open_.iloc[-1])
        prev_close = float(close.iloc[-2])

        # Filter 1: gap up > gap_pct_min (today open vs yesterday close)
        gap_pct = (latest_open - prev_close) / prev_close
        if gap_pct < self.gap_pct_min:
            return None

        # Filter 2: gap held — close >= open (did not fill the gap)
        if latest_close < latest_open:
            return None

        # Filter 3: volume spike on gap day
        vol_avg = volume.rolling(self.volume_avg_period).mean()
        vol_avg_val = float(vol_avg.iloc[-1])
        if pd.isna(vol_avg_val) or vol_avg_val <= 0:
            return None
        vol_ratio = float(volume.iloc[-1]) / vol_avg_val
        if vol_ratio < self.volume_ratio_min:
            return None

        # ATR-based stop: place below the gap level (prev_close) with a buffer
        latest_atr = float(atr(candles, self.atr_period).iloc[-1])
        if pd.isna(latest_atr) or latest_atr <= 0:
            return None

        # Stop below the gap fill level (yesterday's close) with ATR buffer
        stop = prev_close - self.atr_stop_buffer * latest_atr
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
                f"Gap-and-hold: open {latest_open:.2f} gapped {gap_pct:.2%} above "
                f"prev close {prev_close:.2f}, held at {latest_close:.2f}, "
                f"vol {vol_ratio:.1f}x avg"
            ),
            ts=datetime.utcnow(),
        )
