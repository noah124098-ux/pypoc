"""Tests for MomentumStrength strategy.

Verifies:
1. evaluate() returns a non-None Signal on data crafted to trigger it.
2. evaluate() returns None when the regime is not supported.
3. evaluate() returns None (not an exception) on insufficient data.
4. When a signal is returned, R:R >= 1 (target - entry > entry - stop).
5. Returns None when RSI is outside [55, 70] momentum zone.
6. Returns None when price is below or DMA is not rising.
7. Returns None when volume is insufficient.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from core.strategies.momentum_strength import MomentumStrength
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


def _momentum_strength_candles() -> pd.DataFrame:
    """
    130-bar series designed to trigger MomentumStrength:
    - Bars 0-99: steady uptrend (+1/bar) so 50-DMA is well below price and rising.
    - Bars 100-129: alternating +1.2/-0.8 pattern (slight positive bias) which
      produces RSI(20) ~60 — firmly inside [55, 70] momentum zone — and still
      keeps price above the rising 50-DMA.
    - Last bar: volume 1.5x average to satisfy the 1.2x confirmation filter.

    RSI(20) needs down bars to avoid the zero-loss NaN issue in the indicator.
    The alternating pattern gives avg_gain=0.6, avg_loss=0.4 → RSI ≈ 60.
    """
    n = 130
    close = np.empty(n, dtype=float)

    # Bars 0-99: steady uptrend — builds a rising 50-DMA well below price
    for i in range(100):
        close[i] = 1000.0 + i * 1.0   # 1000 → 1099

    # Bars 100-129: alternating +1.2 / -0.8 → RSI(20) parks near 60
    for j in range(30):
        delta = 1.2 if j % 2 == 0 else -0.8
        close[100 + j] = close[99 + j] + delta

    high = close + 2.0
    low = close - 2.0
    open_ = np.r_[close[0], close[:-1]]

    # Volume: base 1_000_000, last bar 1_500_000 (1.5x avg → passes 1.2x filter)
    volume = np.full(n, 1_000_000.0)
    volume[-1] = 1_500_000.0

    idx = pd.bdate_range("2023-01-02", periods=n)
    return pd.DataFrame(
        {"open": open_, "high": high, "low": low, "close": close, "volume": volume},
        index=idx,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_momentum_strength_returns_signal_on_setup():
    strategy = MomentumStrength(
        rsi_period=20,
        rsi_momentum_low=55.0,
        rsi_momentum_high=70.0,
        dma_period=50,
        volume_confirm_ratio=1.2,
    )
    candles = _momentum_strength_candles()
    sig = strategy.evaluate("TEST", candles, Regime.TREND)
    assert sig is not None, (
        "MomentumStrength should fire when RSI in [55,70], price above rising 50-DMA, volume ok"
    )
    assert sig.strategy == "momentum_strength"
    assert sig.side == Side.BUY
    assert sig.entry_price > 0
    assert sig.stop_loss < sig.entry_price
    assert sig.target > sig.entry_price


def test_momentum_strength_returns_none_on_wrong_regime():
    strategy = MomentumStrength()
    candles = _momentum_strength_candles()
    assert strategy.evaluate("TEST", candles, Regime.RANGE) is None
    assert strategy.evaluate("TEST", candles, Regime.VOLATILE) is None
    assert strategy.evaluate("TEST", candles, Regime.UNKNOWN) is None


def test_momentum_strength_returns_none_on_insufficient_data():
    strategy = MomentumStrength()
    assert strategy.evaluate("TEST", _make_ohlcv(10), Regime.TREND) is None


def test_momentum_strength_signal_has_positive_rr():
    strategy = MomentumStrength(target_r_multiple=2.5)
    sig = strategy.evaluate("TEST", _momentum_strength_candles(), Regime.TREND)
    if sig is not None:
        reward = sig.target - sig.entry_price
        risk = sig.entry_price - sig.stop_loss
        assert reward >= risk, f"R:R < 1: reward={reward:.4f}, risk={risk:.4f}"


def test_momentum_strength_none_when_rsi_below_zone():
    """RSI below 55 should not trigger."""
    n = 120
    # Create a flat/slightly declining series to push RSI below 55
    close = np.empty(n, dtype=float)
    for i in range(100):
        close[i] = 1000.0 + i * 1.0
    # Flat last 20 bars — RSI will settle to ~50
    for i in range(20):
        close[100 + i] = 1099.0

    high = close + 2.0
    low = close - 2.0
    open_ = np.r_[close[0], close[:-1]]
    volume = np.full(n, 1_500_000.0)
    idx = pd.bdate_range("2023-01-02", periods=n)
    df = pd.DataFrame(
        {"open": open_, "high": high, "low": low, "close": close, "volume": volume},
        index=idx,
    )
    strategy = MomentumStrength(rsi_momentum_low=55.0, rsi_momentum_high=70.0)
    sig = strategy.evaluate("TEST", df, Regime.TREND)
    # With a flat series RSI should be near 50, outside [55, 70]
    assert sig is None, "Should not fire when RSI is below momentum zone"


def test_momentum_strength_none_when_volume_insufficient():
    """Low volume should block the signal."""
    candles = _momentum_strength_candles().copy()
    # Set last bar volume to below 1.2x average
    avg_vol = candles["volume"].iloc[:-1].mean()
    candles.loc[candles.index[-1], "volume"] = avg_vol * 0.5
    strategy = MomentumStrength(volume_confirm_ratio=1.2)
    sig = strategy.evaluate("TEST", candles, Regime.TREND)
    assert sig is None, "Should not fire when volume is below 1.2x average"


def test_momentum_strength_none_when_price_below_dma():
    """Price below 50-DMA should not trigger."""
    n = 120
    # Declining series so price ends below 50-DMA
    close = np.empty(n, dtype=float)
    for i in range(n):
        close[i] = 1200.0 - i * 1.5   # 1200 → 1021.5 (falling)

    high = close + 2.0
    low = close - 2.0
    open_ = np.r_[close[0], close[:-1]]
    volume = np.full(n, 2_000_000.0)
    idx = pd.bdate_range("2023-01-02", periods=n)
    df = pd.DataFrame(
        {"open": open_, "high": high, "low": low, "close": close, "volume": volume},
        index=idx,
    )
    strategy = MomentumStrength()
    sig = strategy.evaluate("TEST", df, Regime.TREND)
    assert sig is None, "Should not fire when price is below 50-DMA"


def test_momentum_strength_none_when_price_below_20dma():
    """Price below 20-DMA (short-term correction) should not trigger.

    W3-protection filter: even if the 50-DMA is rising and RSI is in the
    momentum zone, a stock in a short-term correction (price < 20-DMA) must
    be blocked to avoid chasing deteriorating momentum.
    """
    # Build 130-bar uptrend, then add 25 sharp declining bars so price dips
    # below the 20-DMA while the 50-DMA is still rising.
    n = 155
    close = np.empty(n, dtype=float)
    for i in range(130):
        close[i] = 1000.0 + i * 1.0   # 1000 → 1129 — strong uptrend builds 20/50 DMAs
    for i in range(25):
        close[130 + i] = close[129 + i] - 2.5   # sharp decline: price falls below 20-DMA

    high = close + 2.0
    low = close - 2.0
    open_ = np.r_[close[0], close[:-1]]
    volume = np.full(n, 2_000_000.0)
    idx = pd.bdate_range("2023-01-02", periods=n)
    df = pd.DataFrame(
        {"open": open_, "high": high, "low": low, "close": close, "volume": volume},
        index=idx,
    )
    strategy = MomentumStrength(short_dma_period=20)
    sig = strategy.evaluate("TEST", df, Regime.TREND)
    assert sig is None, "Should not fire when price is below 20-DMA (short-term correction)"
