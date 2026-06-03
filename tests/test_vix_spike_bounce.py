"""Smoke tests for VixSpikeBounce strategy.

Five tests:
  1. evaluate() returns a valid Signal on data crafted to trigger all filters.
  2. evaluate() returns None when regime is not VOLATILE.
  3. evaluate() returns None when there is insufficient data.
  4. evaluate() returns None when RSI is not oversold (>= threshold).
  5. When a signal is returned, R:R >= 1 (target move / stop move >= target_r_multiple condition).
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from core.strategies.vix_spike_bounce import VixSpikeBounce
from core.types import Regime, Side


# ---------------------------------------------------------------------------
# Helper: build OHLCV data that reliably triggers all five filters
# ---------------------------------------------------------------------------

def _vix_spike_bounce_candles(
    n: int = 250,
    rsi_threshold: float = 35.0,
    make_oversold: bool = True,
    bullish_bar: bool = True,
    high_volume: bool = True,
) -> pd.DataFrame:
    """Return a DataFrame that fires the VixSpikeBounce signal by default.

    The last bar satisfies:
      - price above 200-DMA (n=250, so 200-DMA is available)
      - RSI < rsi_threshold (oversold during VIX spike) — controlled by make_oversold
      - green reversal bar (close > open, close > prev close) — controlled by bullish_bar
      - volume >= 1.5x average — controlled by high_volume

    Parameters allow selectively disabling individual filters to test rejection paths.

    Strategy for keeping price above 200-DMA while making RSI oversold:
      - Strong uptrend for first (n - 30) bars so the 200-DMA is well below current price.
      - Then a short but sharp sell-off for bars (n-30)..(n-2) — just enough consecutive
        down-closes to push RSI below 35, but short enough that the 200-DMA (which is the
        average of 200 bars) stays below price.
      - Final bar: bullish reversal (close > open, close > prev close).
    """
    rng = np.random.default_rng(77)

    # Build close prices for all n bars first, then override OHLCV for the last bar.
    close = np.empty(n, dtype=float)

    # n_decline bars of consecutive declines push RSI below 35.
    # Using a shallow per-bar drop (0.2%) so price stays above the 200-DMA
    # (which is the rolling mean of 200 bars — mostly the long uptrend phase).
    n_decline = 15
    drop_per_bar = 0.002

    if make_oversold:
        # Phase 1: steady uptrend for (n - n_decline - 1) bars — 1000 to ~1300.
        # Long rising phase builds a large gap above the eventual 200-DMA.
        phase1_bars = n - n_decline - 1
        for i in range(phase1_bars):
            close[i] = 1000.0 + i * (300.0 / max(phase1_bars - 1, 1))

        # Phase 2: n_decline consecutive down-closes (0.2%/bar) → RSI < 35.
        # Total decline ~3%; the 200-DMA stays well above current price because
        # it averages 200 bars, the majority of which are from the rising phase.
        peak = float(close[phase1_bars - 1])
        for i in range(n_decline):
            close[phase1_bars + i] = peak * (1.0 - (i + 1) * drop_per_bar)

        # Last bar seeded from the bar before it (reversal bar override below)
        close[n - 1] = close[n - 2]

    else:
        # Gentle continuous uptrend for all bars (RSI stays ~50-60, not oversold)
        for i in range(n):
            close[i] = 1000.0 + i * (300.0 / max(n - 1, 1))

    # Build OHLCV from close
    high = close + rng.uniform(1.0, 3.0, n)
    low = close - rng.uniform(1.0, 3.0, n)
    open_ = np.r_[close[0], close[:-1]]

    volume = rng.integers(1_000_000, 3_000_000, n).astype(float)
    avg_vol = float(volume[:-1].mean())

    if high_volume:
        volume[-1] = avg_vol * 2.0   # 2x average — well above 1.5x threshold
    else:
        volume[-1] = avg_vol * 0.8   # below threshold

    # Override the last bar explicitly using the second-to-last close as anchor
    anchor = float(close[-2])
    if bullish_bar:
        # Green reversal bar: close > open AND close > prev close
        close[-1] = anchor * 1.002
        open_[-1] = anchor * 0.995
        high[-1] = close[-1] * 1.003
        low[-1] = open_[-1] * 0.997
    else:
        # Bearish bar: close < open AND close < prev close
        close[-1] = anchor * 0.995
        open_[-1] = anchor * 1.003
        high[-1] = open_[-1] * 1.002
        low[-1] = close[-1] * 0.997

    idx = pd.bdate_range("2022-01-03", periods=n)
    return pd.DataFrame(
        {"open": open_, "high": high, "low": low, "close": close, "volume": volume},
        index=idx,
    )


# ---------------------------------------------------------------------------
# Test 1: strategy fires on well-crafted data
# ---------------------------------------------------------------------------

def test_vix_spike_bounce_returns_signal_on_setup():
    strategy = VixSpikeBounce(
        rsi_period=14,
        rsi_oversold_threshold=35.0,
        dma_period=200,
        atr_period=14,
        atr_stop_multiplier=2.0,
        target_r_multiple=1.5,
        volume_ratio_min=1.5,
    )
    candles = _vix_spike_bounce_candles()
    sig = strategy.evaluate("RELIANCE", candles, Regime.VOLATILE)

    assert sig is not None, "VixSpikeBounce must fire when all five filters pass"
    assert sig.strategy == "vix_spike_bounce"
    assert sig.side == Side.BUY
    assert sig.entry_price > 0
    assert sig.stop_loss < sig.entry_price, "Stop must be below entry for a BUY signal"
    assert sig.target > sig.entry_price, "Target must be above entry for a BUY signal"
    assert "RSI" in sig.rationale
    assert sig.confidence > 0


# ---------------------------------------------------------------------------
# Test 2: returns None when regime is not VOLATILE
# ---------------------------------------------------------------------------

def test_vix_spike_bounce_returns_none_on_wrong_regime():
    strategy = VixSpikeBounce()
    candles = _vix_spike_bounce_candles()

    assert strategy.evaluate("RELIANCE", candles, Regime.TREND) is None
    assert strategy.evaluate("RELIANCE", candles, Regime.RANGE) is None
    assert strategy.evaluate("RELIANCE", candles, Regime.UNKNOWN) is None


# ---------------------------------------------------------------------------
# Test 3: returns None on insufficient data
# ---------------------------------------------------------------------------

def test_vix_spike_bounce_returns_none_on_insufficient_data():
    strategy = VixSpikeBounce(dma_period=200)
    # 200 bars is the minimum needed for a valid 200-DMA — pass only 50
    tiny = pd.DataFrame(
        {
            "open": np.full(50, 1000.0),
            "high": np.full(50, 1005.0),
            "low": np.full(50, 995.0),
            "close": np.full(50, 1000.0),
            "volume": np.full(50, 2_000_000.0),
        },
        index=pd.bdate_range("2023-01-02", periods=50),
    )
    assert strategy.evaluate("TCS", tiny, Regime.VOLATILE) is None


# ---------------------------------------------------------------------------
# Test 4: returns None when RSI is not oversold
# ---------------------------------------------------------------------------

def test_vix_spike_bounce_returns_none_when_rsi_not_oversold():
    strategy = VixSpikeBounce(rsi_oversold_threshold=35.0)
    # make_oversold=False → RSI will be well above 35 (gentle uptrend → RSI ~50-60)
    candles = _vix_spike_bounce_candles(make_oversold=False)
    result = strategy.evaluate("INFY", candles, Regime.VOLATILE)
    assert result is None, "VixSpikeBounce must not fire when RSI is not oversold"


# ---------------------------------------------------------------------------
# Test 5: signal R:R satisfies target_r_multiple
# ---------------------------------------------------------------------------

def test_vix_spike_bounce_signal_rr_matches_target_r_multiple():
    target_r = 1.5
    strategy = VixSpikeBounce(target_r_multiple=target_r, atr_stop_multiplier=2.0)
    candles = _vix_spike_bounce_candles()
    sig = strategy.evaluate("HDFC", candles, Regime.VOLATILE)

    if sig is not None:
        reward = sig.target - sig.entry_price
        risk = sig.entry_price - sig.stop_loss
        assert risk > 0, "Risk (entry - stop) must be positive"
        actual_rr = reward / risk
        # Allow small floating-point tolerance
        assert actual_rr >= target_r - 0.01, (
            f"R:R {actual_rr:.4f} is below target_r_multiple {target_r}"
        )
