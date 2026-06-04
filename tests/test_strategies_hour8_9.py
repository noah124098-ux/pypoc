"""Tests for VolumeBreakoutConfirm and GapAndHold strategies (hours 8-9 batch)."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from core.strategies.gap_and_hold import GapAndHold
from core.strategies.volume_breakout_confirm import VolumeBreakoutConfirm
from core.types import Regime, Side


# ---------------------------------------------------------------------------
# Shared helpers
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


# ===========================================================================
# VolumeBreakoutConfirm
# ===========================================================================

def _vbc_candles() -> pd.DataFrame:
    """
    80-bar uptrending series where the final bar satisfies all four filters:
    1. close > 10-day Donchian upper (new 10-day high)
    2. volume > 2.5x 20-day average
    3. close in top 20% of bar range (strong close)
    4. close > 50-DMA (uptrend)
    """
    n = 80
    rng = np.random.default_rng(7)
    # Steady uptrend so 50-DMA is well below close
    close = 1000.0 + np.linspace(0, 80, n)
    spread = rng.uniform(0.003, 0.008, n)
    high = close * (1 + spread)
    low = close * (1 - spread)
    open_ = np.r_[close[0], close[:-1]]
    volume = rng.integers(1_000_000, 3_000_000, n).astype(float)
    idx = pd.bdate_range("2023-01-02", periods=n)
    df = pd.DataFrame(
        {"open": open_, "high": high, "low": low, "close": close, "volume": volume},
        index=idx,
    )

    # Craft last bar to fire all filters.
    # 1. Break above 10-day Donchian high
    prev_don_high = float(df["high"].iloc[-11:-1].max())
    new_close = prev_don_high * 1.02

    # 2. Volume spike: 3x average
    avg_vol = float(df["volume"].iloc[-21:-1].mean())

    # 3. Strong close: close IS the bar high (open at low end, close at top = quality 1.0)
    bar_low = new_close * 0.990   # low well below close
    bar_high = new_close          # close equals the bar high

    df.iloc[-1, df.columns.get_loc("close")] = new_close
    df.iloc[-1, df.columns.get_loc("high")] = bar_high
    df.iloc[-1, df.columns.get_loc("low")] = bar_low
    df.iloc[-1, df.columns.get_loc("open")] = bar_low    # open at bottom
    df.iloc[-1, df.columns.get_loc("volume")] = avg_vol * 3.0

    return df


def test_vbc_returns_signal_on_setup():
    strategy = VolumeBreakoutConfirm(
        donchian_period=10,
        volume_avg_period=20,
        volume_spike_min=2.5,
        close_quality_pct=0.80,
        dma_period=50,
    )
    sig = strategy.evaluate("TEST", _vbc_candles(), Regime.TREND)
    assert sig is not None, "VolumeBreakoutConfirm should fire on all-filter-pass bar"
    assert sig.strategy == "volume_breakout_confirm"
    assert sig.side == Side.BUY
    assert sig.entry_price > 0
    assert sig.stop_loss < sig.entry_price
    assert sig.target > sig.entry_price


def test_vbc_returns_none_on_wrong_regime():
    strategy = VolumeBreakoutConfirm()
    candles = _vbc_candles()
    assert strategy.evaluate("TEST", candles, Regime.RANGE) is None
    assert strategy.evaluate("TEST", candles, Regime.VOLATILE) is None
    assert strategy.evaluate("TEST", candles, Regime.UNKNOWN) is None


def test_vbc_returns_none_on_insufficient_data():
    assert VolumeBreakoutConfirm().evaluate("TEST", _make_ohlcv(20), Regime.TREND) is None


def test_vbc_signal_has_positive_rr():
    sig = VolumeBreakoutConfirm(target_r_multiple=2.0).evaluate(
        "TEST", _vbc_candles(), Regime.TREND
    )
    if sig is not None:
        reward = sig.target - sig.entry_price
        risk = sig.entry_price - sig.stop_loss
        assert reward > 0
        assert risk > 0
        assert reward >= risk, f"R:R < 1: reward={reward:.4f}, risk={risk:.4f}"


def test_vbc_no_signal_below_dma():
    """When close < 50-DMA the signal must be suppressed."""
    df = _vbc_candles()
    # Force 50-DMA above close by injecting a large historical price
    close_val = float(df["close"].iloc[-1])
    # Make first 50 bars very high so DMA is above current close
    for i in range(50):
        df.iloc[i, df.columns.get_loc("close")] = close_val * 2.0
        df.iloc[i, df.columns.get_loc("high")] = close_val * 2.01
        df.iloc[i, df.columns.get_loc("low")] = close_val * 1.99
    strategy = VolumeBreakoutConfirm(dma_period=50)
    assert strategy.evaluate("TEST", df, Regime.TREND) is None


def test_vbc_no_signal_low_volume():
    """Volume below threshold suppresses signal."""
    df = _vbc_candles()
    # Set last bar volume to 1x average (below 2.5x threshold)
    avg_vol = float(df["volume"].iloc[-21:-1].mean())
    df.iloc[-1, df.columns.get_loc("volume")] = avg_vol * 1.2
    strategy = VolumeBreakoutConfirm(volume_spike_min=2.5)
    assert strategy.evaluate("TEST", df, Regime.TREND) is None


def test_vbc_no_signal_weak_close():
    """Close in lower half of bar range suppresses signal (weak close filter)."""
    df = _vbc_candles()
    # Manufacture a wide bar with close at the very bottom
    high_val = float(df["close"].iloc[-1]) * 1.02
    low_val = float(df["close"].iloc[-1]) * 0.98
    weak_close = low_val + (high_val - low_val) * 0.1  # close at 10% of range
    df.iloc[-1, df.columns.get_loc("high")] = high_val
    df.iloc[-1, df.columns.get_loc("low")] = low_val
    df.iloc[-1, df.columns.get_loc("close")] = weak_close
    strategy = VolumeBreakoutConfirm(close_quality_pct=0.80)
    assert strategy.evaluate("TEST", df, Regime.TREND) is None


# ===========================================================================
# GapAndHold
# ===========================================================================

def _gap_hold_candles(regime: str = "trend") -> pd.DataFrame:
    """
    60-bar series ending with a gap-up day that holds:
    - Last bar opens > previous close by 1% (gap > 0.5% threshold)
    - Last bar closes >= its open (gap held)
    - Last bar volume is 2x the 20-day average
    """
    n = 60
    rng = np.random.default_rng(13)
    close = 1000.0 + np.linspace(0, 30, n)
    high = close + rng.uniform(2, 6, n)
    low = close - rng.uniform(2, 6, n)
    open_ = np.r_[close[0], close[:-1]]
    volume = rng.integers(1_000_000, 2_500_000, n).astype(float)
    idx = pd.bdate_range("2023-01-02", periods=n)
    df = pd.DataFrame(
        {"open": open_, "high": high, "low": low, "close": close, "volume": volume},
        index=idx,
    )

    # Last bar: gap up and hold
    prev_close = float(df["close"].iloc[-2])
    gap_open = prev_close * 1.012    # 1.2% gap (above 0.5% threshold)
    gap_close = gap_open * 1.003     # close above open (gap held)
    avg_vol = float(df["volume"].iloc[-21:-1].mean())

    df.iloc[-1, df.columns.get_loc("open")] = gap_open
    df.iloc[-1, df.columns.get_loc("close")] = gap_close
    df.iloc[-1, df.columns.get_loc("high")] = gap_close * 1.002
    df.iloc[-1, df.columns.get_loc("low")] = gap_open * 0.999
    df.iloc[-1, df.columns.get_loc("volume")] = avg_vol * 2.0

    return df


def test_gap_hold_returns_signal_on_trend():
    strategy = GapAndHold(gap_pct_min=0.005, volume_ratio_min=1.5)
    sig = strategy.evaluate("TEST", _gap_hold_candles(), Regime.TREND)
    assert sig is not None, "GapAndHold should fire on gap-up + hold + volume in TREND"
    assert sig.strategy == "gap_and_hold"
    assert sig.side == Side.BUY
    assert sig.stop_loss < sig.entry_price
    assert sig.target > sig.entry_price


def test_gap_hold_returns_signal_on_range():
    strategy = GapAndHold(gap_pct_min=0.005, volume_ratio_min=1.5)
    sig = strategy.evaluate("TEST", _gap_hold_candles(), Regime.RANGE)
    assert sig is not None, "GapAndHold should fire in RANGE regime too"


def test_gap_hold_returns_none_on_volatile():
    strategy = GapAndHold()
    candles = _gap_hold_candles()
    assert strategy.evaluate("TEST", candles, Regime.VOLATILE) is None


def test_gap_hold_returns_none_on_unknown():
    strategy = GapAndHold()
    candles = _gap_hold_candles()
    assert strategy.evaluate("TEST", candles, Regime.UNKNOWN) is None


def test_gap_hold_returns_none_on_insufficient_data():
    assert GapAndHold().evaluate("TEST", _make_ohlcv(10), Regime.TREND) is None


def test_gap_hold_signal_has_positive_rr():
    sig = GapAndHold(target_r_multiple=2.0).evaluate(
        "TEST", _gap_hold_candles(), Regime.TREND
    )
    if sig is not None:
        reward = sig.target - sig.entry_price
        risk = sig.entry_price - sig.stop_loss
        assert reward > 0
        assert risk > 0
        assert reward >= risk, f"R:R < 1: reward={reward:.4f}, risk={risk:.4f}"


def test_gap_hold_no_signal_gap_filled():
    """When close < open (gap filled), no signal."""
    df = _gap_hold_candles()
    # Make close drop below open (gap filled)
    gap_open = float(df["open"].iloc[-1])
    df.iloc[-1, df.columns.get_loc("close")] = gap_open * 0.995
    strategy = GapAndHold()
    assert strategy.evaluate("TEST", df, Regime.TREND) is None


def test_gap_hold_no_signal_small_gap():
    """Gap of 0.1% (below 0.5% threshold) should be rejected."""
    df = _gap_hold_candles()
    prev_close = float(df["close"].iloc[-2])
    tiny_gap_open = prev_close * 1.001   # 0.1% gap — below 0.5% threshold
    tiny_gap_close = tiny_gap_open * 1.002
    avg_vol = float(df["volume"].iloc[-21:-1].mean())
    df.iloc[-1, df.columns.get_loc("open")] = tiny_gap_open
    df.iloc[-1, df.columns.get_loc("close")] = tiny_gap_close
    df.iloc[-1, df.columns.get_loc("high")] = tiny_gap_close * 1.001
    df.iloc[-1, df.columns.get_loc("low")] = tiny_gap_open * 0.999
    df.iloc[-1, df.columns.get_loc("volume")] = avg_vol * 2.0
    strategy = GapAndHold(gap_pct_min=0.005)
    assert strategy.evaluate("TEST", df, Regime.TREND) is None


def test_gap_hold_no_signal_low_volume():
    """Volume below threshold suppresses the gap-and-hold signal."""
    df = _gap_hold_candles()
    avg_vol = float(df["volume"].iloc[-21:-1].mean())
    df.iloc[-1, df.columns.get_loc("volume")] = avg_vol * 1.1  # below 1.5x
    strategy = GapAndHold(volume_ratio_min=1.5)
    assert strategy.evaluate("TEST", df, Regime.TREND) is None


def test_gap_hold_stop_below_gap_level():
    """Stop must be below yesterday's close (the gap level)."""
    sig = GapAndHold(gap_pct_min=0.005, volume_ratio_min=1.5).evaluate(
        "TEST", _gap_hold_candles(), Regime.TREND
    )
    if sig is not None:
        df = _gap_hold_candles()
        prev_close = float(df["close"].iloc[-2])
        assert sig.stop_loss < prev_close, (
            f"Stop {sig.stop_loss:.2f} must be below gap level (prev close {prev_close:.2f})"
        )
