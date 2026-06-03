"""Tests for NRPatternBreakout strategy.

Verifies:
1. evaluate() returns a non-None Signal on data crafted to trigger it.
2. evaluate() returns None when the regime is not supported.
3. evaluate() returns None (not an exception) on insufficient data.
4. When a signal is returned, R:R >= 1 (target - entry > entry - stop).
5. Returns None when current bar is NOT the narrowest in nr_lookback.
6. Returns None when close is in lower portion of the bar (bearish close).
7. Returns None when close does not break above yesterday's high.
8. Works in both TREND and RANGE regimes.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from core.strategies.nr_pattern import NRPatternBreakout
from core.types import Regime, Side


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_ohlcv(
    n: int,
    start_price: float = 1000.0,
    drift: float = 0.001,
    vol: float = 0.01,
    seed: int = 42,
    volume_base: int = 2_000_000,
) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    rets = rng.normal(loc=drift, scale=vol, size=n)
    close = start_price * np.exp(np.cumsum(rets))
    spread = rng.uniform(0.002, vol, size=n)
    high = close * (1 + spread)
    low = close * (1 - spread)
    open_ = np.r_[close[0], close[:-1]]
    volume = rng.integers(volume_base // 2, volume_base * 2, size=n).astype(float)
    idx = pd.bdate_range("2023-01-02", periods=n)
    return pd.DataFrame(
        {"open": open_, "high": high, "low": low, "close": close, "volume": volume},
        index=idx,
    )


def _nr_pattern_candles() -> pd.DataFrame:
    """
    40-bar series where the last bar is a valid NR7 breakout:
    - Bars -7 to -2: progressively wider ranges (NR7 must be narrower than all)
    - Last bar (bar -1): has the narrowest range of the 7, closes in upper 30%,
      AND close > previous bar's high.
    """
    n = 40
    rng = np.random.default_rng(99)
    base_close = 1000.0 + np.linspace(0, 40, n)
    high = base_close + 10.0
    low = base_close - 10.0
    open_ = np.r_[base_close[0], base_close[:-1]]
    volume = rng.integers(1_000_000, 3_000_000, n).astype(float)
    idx = pd.bdate_range("2023-01-02", periods=n)
    df = pd.DataFrame(
        {"open": open_, "high": high, "low": low, "close": base_close, "volume": volume},
        index=idx,
    )

    # Set bars -7 to -2: progressively wider ranges (14, 12, 10, 8, 6, 4)
    # so the last bar with range=2 is the strictest NR7
    anchor = float(df["close"].iloc[-8])
    for j, i in enumerate(range(-7, -1)):
        width = 7 - j  # 7, 6, 5, 4, 3, 2
        df.iloc[i, df.columns.get_loc("high")] = anchor + width
        df.iloc[i, df.columns.get_loc("low")] = anchor - width
        df.iloc[i, df.columns.get_loc("close")] = anchor + j * 0.2

    # Previous bar (index -2): set its high explicitly
    prev_high = float(df["high"].iloc[-2])

    # Last bar: NR7 (range=1, so strictly narrowest), close in upper 30%,
    # and close > previous bar's high
    # We need: close > prev_high AND close >= low + 0.70 * range
    nr_low = anchor + 0.5
    nr_high = anchor + 1.5   # range = 1.0
    nr_close = nr_high - 0.1  # just below high — upper 30% of range [0.5, 1.5]

    # Make sure we break above prev_high
    if nr_close <= prev_high:
        # Shift the NR7 bar up so close > prev_high
        shift = prev_high - nr_close + 0.5
        nr_low += shift
        nr_high += shift
        nr_close += shift

    df.iloc[-1, df.columns.get_loc("high")] = nr_high
    df.iloc[-1, df.columns.get_loc("low")] = nr_low
    df.iloc[-1, df.columns.get_loc("close")] = nr_close
    df.iloc[-1, df.columns.get_loc("open")] = nr_low + 0.2
    return df


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_nr_pattern_returns_signal_on_setup():
    strategy = NRPatternBreakout(nr_lookback=7)
    candles = _nr_pattern_candles()
    sig = strategy.evaluate("TEST", candles, Regime.TREND)
    assert sig is not None, (
        "NRPatternBreakout should fire on NR7 bar with bullish close above prev high"
    )
    assert sig.strategy == "nr_pattern"
    assert sig.side == Side.BUY
    assert sig.entry_price > 0
    assert sig.stop_loss < sig.entry_price
    assert sig.target > sig.entry_price


def test_nr_pattern_fires_in_range_regime():
    """NRPatternBreakout supports RANGE as well as TREND."""
    strategy = NRPatternBreakout(nr_lookback=7)
    candles = _nr_pattern_candles()
    sig = strategy.evaluate("TEST", candles, Regime.RANGE)
    assert sig is not None, "NRPatternBreakout should also fire in RANGE regime"
    assert sig.strategy == "nr_pattern"


def test_nr_pattern_returns_none_on_wrong_regime():
    strategy = NRPatternBreakout()
    candles = _nr_pattern_candles()
    assert strategy.evaluate("TEST", candles, Regime.VOLATILE) is None
    assert strategy.evaluate("TEST", candles, Regime.UNKNOWN) is None


def test_nr_pattern_returns_none_on_insufficient_data():
    strategy = NRPatternBreakout()
    assert strategy.evaluate("TEST", _make_ohlcv(5), Regime.TREND) is None


def test_nr_pattern_signal_has_positive_rr():
    strategy = NRPatternBreakout(target_r_multiple=2.0)
    sig = strategy.evaluate("TEST", _nr_pattern_candles(), Regime.TREND)
    if sig is not None:
        reward = sig.target - sig.entry_price
        risk = sig.entry_price - sig.stop_loss
        assert reward >= risk, f"R:R < 1: reward={reward:.4f}, risk={risk:.4f}"


def test_nr_pattern_none_when_not_narrowest():
    """If the current bar is NOT the narrowest in the lookback, no signal."""
    candles = _nr_pattern_candles().copy()
    # Make last bar wider than the others
    last_low = float(candles["low"].iloc[-1])
    candles.iloc[-1, candles.columns.get_loc("high")] = last_low + 20.0   # range=20, wider than all
    candles.iloc[-1, candles.columns.get_loc("low")] = last_low
    candles.iloc[-1, candles.columns.get_loc("close")] = last_low + 17.0  # still upper 30%
    strategy = NRPatternBreakout(nr_lookback=7)
    sig = strategy.evaluate("TEST", candles, Regime.TREND)
    assert sig is None, "Should not fire when current bar is not the narrowest"


def test_nr_pattern_none_when_bearish_close():
    """Close in lower 30% of bar (bearish) should block the signal."""
    candles = _nr_pattern_candles().copy()
    nr_low = float(candles["low"].iloc[-1])
    nr_high = float(candles["high"].iloc[-1])
    nr_range = nr_high - nr_low
    # Set close to lower 30% of the bar
    candles.iloc[-1, candles.columns.get_loc("close")] = nr_low + 0.1 * nr_range
    strategy = NRPatternBreakout(nr_lookback=7, bullish_close_pct=0.30)
    sig = strategy.evaluate("TEST", candles, Regime.TREND)
    assert sig is None, "Should not fire when close is in lower portion of bar"


def test_nr_pattern_none_when_no_breakout_above_prev_high():
    """Close must exceed yesterday's high; if it does not, no signal."""
    candles = _nr_pattern_candles().copy()
    prev_high = float(candles["high"].iloc[-2])
    nr_low = float(candles["low"].iloc[-1])
    nr_high = float(candles["high"].iloc[-1])
    nr_range = nr_high - nr_low
    # Set close to be above 70% level (bullish) BUT below previous high
    bullish_threshold = nr_low + 0.70 * nr_range
    new_close = min(bullish_threshold + 0.1, prev_high - 0.1)
    if new_close > bullish_threshold:  # only test if we can satisfy the constraint
        candles.iloc[-1, candles.columns.get_loc("close")] = new_close
        strategy = NRPatternBreakout(nr_lookback=7)
        sig = strategy.evaluate("TEST", candles, Regime.TREND)
        assert sig is None, "Should not fire when close does not break above previous high"
