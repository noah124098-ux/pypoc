"""Smoke tests for all 9 strategies.

For each strategy we verify:
1. evaluate() returns a non-None Signal on data crafted to trigger it.
2. evaluate() returns None when the regime is not supported.
3. evaluate() returns None (not an exception) on insufficient data.
4. When a signal is returned, R:R >= 1 (target move > stop move).

Notes on Supertrend / SupertrendShort:
- supertrend_bands() has a known NaN-propagation issue where final_upper/final_lower
  never update from their initial NaN state, so direction never flips from its
  initialised value of +1 with real OHLC data.
- For the "returns signal" and "R:R" tests we therefore patch supertrend_bands to
  inject a controlled direction array.  This verifies that the STRATEGY LOGIC is
  correct given a working indicator.  The indicator bug is tracked separately.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from unittest.mock import patch

from core.strategies.bb_squeeze import BbSqueeze
from core.strategies.ema_crossover import EmaCrossover
from core.strategies.mean_reversion import MeanReversion
from core.strategies.obv_trend import ObvTrend
from core.strategies.rsi_momentum import RsiMomentum
from core.strategies.supertrend import Supertrend
from core.strategies.supertrend_short import SupertrendShort
from core.strategies.trend_breakout import TrendBreakout
from core.strategies.volatility_compression import VolatilityCompression
from core.types import Regime, Side


# ---------------------------------------------------------------------------
# Shared helper
# ---------------------------------------------------------------------------

def _make_ohlcv(
    n: int,
    start_price: float = 1000.0,
    drift: float = 0.001,
    vol: float = 0.01,
    seed: int = 42,
    volume_base: int = 2_000_000,
) -> pd.DataFrame:
    """Generic OHLCV DataFrame with gentle upward drift."""
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
# TrendBreakout
# ===========================================================================

def _trend_breakout_candles() -> pd.DataFrame:
    """
    60 bars gently creeping up, with the final bar breaking cleanly above the
    Donchian(20) upper and volume above the 20-bar average.
    """
    n = 60
    rng = np.random.default_rng(1)
    close = 1000.0 + np.linspace(0, 5, n)
    spread = rng.uniform(0.003, 0.007, n)
    high = close * (1 + spread)
    low = close * (1 - spread)
    open_ = np.r_[close[0], close[:-1]]
    volume = rng.integers(1_000_000, 3_000_000, n).astype(float)
    idx = pd.bdate_range("2023-01-02", periods=n)
    df = pd.DataFrame(
        {"open": open_, "high": high, "low": low, "close": close, "volume": volume},
        index=idx,
    )
    # Last bar: clearly above 20-day Donchian high, with high volume
    prev_donchian_high = df["high"].iloc[-21:-1].max()
    df.loc[df.index[-1], "close"] = prev_donchian_high * 1.03
    df.loc[df.index[-1], "high"] = prev_donchian_high * 1.04
    df.loc[df.index[-1], "volume"] = float(df["volume"].mean() * 2.0)
    return df


def test_trend_breakout_returns_signal_on_setup():
    strategy = TrendBreakout(donchian_period=20, volume_confirm_ratio=1.0)
    sig = strategy.evaluate("TEST", _trend_breakout_candles(), Regime.TREND)
    assert sig is not None, "TrendBreakout should fire on Donchian breakout bar"
    assert sig.strategy == "trend_breakout"
    assert sig.side == Side.BUY
    assert sig.entry_price > 0
    assert sig.stop_loss < sig.entry_price
    assert sig.target > sig.entry_price


def test_trend_breakout_returns_none_on_wrong_regime():
    strategy = TrendBreakout()
    candles = _trend_breakout_candles()
    assert strategy.evaluate("TEST", candles, Regime.RANGE) is None
    assert strategy.evaluate("TEST", candles, Regime.VOLATILE) is None
    assert strategy.evaluate("TEST", candles, Regime.UNKNOWN) is None


def test_trend_breakout_returns_none_on_insufficient_data():
    assert TrendBreakout().evaluate("TEST", _make_ohlcv(10), Regime.TREND) is None


def test_trend_breakout_signal_has_positive_rr():
    sig = TrendBreakout(target_r_multiple=2.5).evaluate(
        "TEST", _trend_breakout_candles(), Regime.TREND
    )
    if sig is not None:
        reward = sig.target - sig.entry_price
        risk = sig.entry_price - sig.stop_loss
        assert reward > risk, f"R:R < 1: reward={reward:.4f}, risk={risk:.4f}"


# ===========================================================================
# MeanReversion
# ===========================================================================

def _mean_reversion_candles() -> pd.DataFrame:
    """
    80 flat bars followed by a sharp 20-bar decline that drives RSI to 0 and
    pushes close just below the Bollinger lower band.  The 50-DMA stays flat
    (not falling > 1%), satisfying the trend filter.
    """
    n = 100
    close = np.full(n, 1000.0, dtype=float)
    # 20-bar steady decline
    for i in range(20):
        close[79 + i] = 1000.0 - (i + 1) * 1.5

    # Compute the Bollinger lower band just before the last bar, then place the
    # last bar slightly below that level so close <= lower * 1.005.
    prices_before_last = close[:99].copy()
    ma_ = np.mean(prices_before_last[-20:])
    std_ = np.std(prices_before_last[-20:], ddof=1)
    close[-1] = (ma_ - 2.0 * std_) * 0.99   # 1% below lower band

    high = close + 1.0
    low = close - 1.0
    open_ = np.r_[close[0], close[:-1]]
    volume = np.full(n, 1_000_000.0)
    idx = pd.bdate_range("2023-01-02", periods=n)
    return pd.DataFrame(
        {"open": open_, "high": high, "low": low, "close": close, "volume": volume},
        index=idx,
    )


def test_mean_reversion_returns_signal_on_setup():
    strategy = MeanReversion(rsi_period=14, bb_period=20, stock_dma_period=50)
    sig = strategy.evaluate("TEST", _mean_reversion_candles(), Regime.RANGE)
    assert sig is not None, "MeanReversion should fire when RSI oversold + at/below BB lower"
    assert sig.strategy == "mean_reversion"
    assert sig.side == Side.BUY
    assert sig.stop_loss < sig.entry_price
    assert sig.target > sig.entry_price


def test_mean_reversion_returns_none_on_wrong_regime():
    strategy = MeanReversion()
    candles = _mean_reversion_candles()
    assert strategy.evaluate("TEST", candles, Regime.TREND) is None
    assert strategy.evaluate("TEST", candles, Regime.VOLATILE) is None
    assert strategy.evaluate("TEST", candles, Regime.UNKNOWN) is None


def test_mean_reversion_returns_none_on_insufficient_data():
    assert MeanReversion().evaluate("TEST", _make_ohlcv(20), Regime.RANGE) is None


def test_mean_reversion_signal_has_positive_rr():
    sig = MeanReversion().evaluate("TEST", _mean_reversion_candles(), Regime.RANGE)
    if sig is not None:
        assert sig.target - sig.entry_price > 0
        assert sig.entry_price - sig.stop_loss > 0


# ===========================================================================
# VolatilityCompression
# ===========================================================================

def _volatility_compression_candles() -> pd.DataFrame:
    """
    NR7 setup: 7 bars with progressively narrowing ranges, last bar has the
    narrowest range AND closes above the previous bar's high (NR7 breakout).
    """
    n = 40
    rng = np.random.default_rng(3)
    close = 1000.0 + np.linspace(0, 20, n)
    high = close + rng.uniform(5, 15, n)
    low = close - rng.uniform(5, 15, n)
    open_ = np.r_[close[0], close[:-1]]
    volume = rng.integers(500_000, 2_000_000, n).astype(float)
    idx = pd.bdate_range("2023-01-02", periods=n)
    df = pd.DataFrame(
        {"open": open_, "high": high, "low": low, "close": close, "volume": volume},
        index=idx,
    )
    # Bars -7 to -2: progressively narrowing ranges (14, 12, 10, 8, 6, 4)
    base_close = float(df["close"].iloc[-8])
    for j, i in enumerate(range(-7, -1)):
        width = 14 - j * 2
        df.iloc[i, df.columns.get_loc("high")] = base_close + width
        df.iloc[i, df.columns.get_loc("low")] = base_close - width
        df.iloc[i, df.columns.get_loc("close")] = base_close + j * 0.5

    # Last bar: narrowest (range=1) and close above previous bar's high
    prev_high = float(df["high"].iloc[-2])
    df.iloc[-1, df.columns.get_loc("high")] = prev_high + 3
    df.iloc[-1, df.columns.get_loc("low")] = prev_high + 2
    df.iloc[-1, df.columns.get_loc("close")] = prev_high + 2.5
    return df


def test_volatility_compression_returns_signal_on_setup():
    strategy = VolatilityCompression(nr_lookback=7)
    sig = strategy.evaluate("TEST", _volatility_compression_candles(), Regime.VOLATILE)
    assert sig is not None, "VolatilityCompression should fire on NR7 + breakout above prev high"
    assert sig.strategy == "volatility_compression"
    assert sig.side == Side.BUY
    assert sig.stop_loss < sig.entry_price
    assert sig.target > sig.entry_price


def test_volatility_compression_returns_none_on_wrong_regime():
    strategy = VolatilityCompression()
    candles = _volatility_compression_candles()
    assert strategy.evaluate("TEST", candles, Regime.TREND) is None
    assert strategy.evaluate("TEST", candles, Regime.RANGE) is None
    assert strategy.evaluate("TEST", candles, Regime.UNKNOWN) is None


def test_volatility_compression_returns_none_on_insufficient_data():
    assert VolatilityCompression(nr_lookback=7).evaluate(
        "TEST", _make_ohlcv(5), Regime.VOLATILE
    ) is None


def test_volatility_compression_signal_has_positive_rr():
    sig = VolatilityCompression(nr_lookback=7).evaluate(
        "TEST", _volatility_compression_candles(), Regime.VOLATILE
    )
    if sig is not None:
        reward = sig.target - sig.entry_price
        risk = sig.entry_price - sig.stop_loss
        assert reward >= risk, f"R:R < 1: reward={reward:.4f}, risk={risk:.4f}"


# ===========================================================================
# EmaCrossover
# ===========================================================================

def _ema_crossover_candles() -> pd.DataFrame:
    """
    60 bars: flat for first 55 then a sharp jump so EMA(9) crosses above EMA(21)
    on the last bar.  Volume on the last bar is above the 20-bar average.
    """
    n = 60
    close = np.empty(n, dtype=float)
    # Phase 1 (0-40): gentle decline so EMA(9) < EMA(21)
    for i in range(41):
        close[i] = 1100.0 - i * 2.0     # 1100 → 1020

    # Phase 2 (41-58): flat — EMA(9) converges below EMA(21)
    for i in range(18):
        close[41 + i] = 1020.0

    # Last bar: large spike forces EMA(9) above EMA(21)
    close[-1] = 1200.0

    high = close + 5.0
    low = close - 5.0
    open_ = np.r_[close[0], close[:-1]]
    volume = np.full(n, 1_000_000.0)
    volume[-1] = 3_000_000.0       # above 20-bar average of 1 M

    idx = pd.bdate_range("2023-01-02", periods=n)
    return pd.DataFrame(
        {"open": open_, "high": high, "low": low, "close": close, "volume": volume},
        index=idx,
    )


def test_ema_crossover_returns_signal_on_setup():
    strategy = EmaCrossover(fast_period=9, slow_period=21, volume_confirmation=True)
    sig = strategy.evaluate("TEST", _ema_crossover_candles(), Regime.TREND)
    assert sig is not None, "EmaCrossover should fire on EMA(9) crossing above EMA(21)"
    assert sig.strategy == "ema_crossover"
    assert sig.side == Side.BUY
    assert sig.stop_loss < sig.entry_price
    assert sig.target > sig.entry_price


def test_ema_crossover_returns_none_on_wrong_regime():
    strategy = EmaCrossover()
    candles = _ema_crossover_candles()
    assert strategy.evaluate("TEST", candles, Regime.RANGE) is None
    assert strategy.evaluate("TEST", candles, Regime.VOLATILE) is None
    assert strategy.evaluate("TEST", candles, Regime.UNKNOWN) is None


def test_ema_crossover_returns_none_on_insufficient_data():
    assert EmaCrossover().evaluate("TEST", _make_ohlcv(10), Regime.TREND) is None


def test_ema_crossover_signal_has_positive_rr():
    sig = EmaCrossover(target_r_multiple=2.0).evaluate(
        "TEST", _ema_crossover_candles(), Regime.TREND
    )
    if sig is not None:
        reward = sig.target - sig.entry_price
        risk = sig.entry_price - sig.stop_loss
        assert reward >= risk, f"R:R < 1: reward={reward:.4f}, risk={risk:.4f}"


# ===========================================================================
# RsiMomentum
# ===========================================================================

def _rsi_momentum_candles() -> pd.DataFrame:
    """
    120-bar series: steady uptrend for bars 0-99 (so EMA-50 is rising and
    close >> EMA-50), then an alternating pattern for bars 100-118 that parks
    RSI ~46-47, followed by a large up bar on bar 119 that bumps RSI into the
    [40, 55] window AND makes RSI[-1] > RSI[-2].
    """
    n = 120
    close = np.empty(n, dtype=float)

    # Bars 0-99: steady uptrend (1.0 per bar)
    for i in range(100):
        close[i] = 1000.0 + i * 1.0   # 1000 → 1099

    # Bars 100-117: alternating −1.7 / +1.5 (locks RSI near 46)
    val = 1099.0
    alt_pattern = [-1.7, 1.5] * 9     # 18 bars
    for j, delta in enumerate(alt_pattern):
        val += delta
        close[100 + j] = val

    # Bar 118: down bar (to ensure RSI[-2] < RSI[-1])
    close[118] = close[117] - 1.7

    # Bar 119 (last): big uptick → RSI jumps from ~47 to ~51 (still in [40,55])
    close[119] = close[118] + 3.5

    high = close + 3.0
    low = close - 3.0
    open_ = np.r_[close[0], close[:-1]]
    volume = np.full(n, 2_000_000.0)
    volume[-1] = 4_000_000.0          # above 20-bar average → volume_confirm passes

    idx = pd.bdate_range("2023-01-02", periods=n)
    return pd.DataFrame(
        {"open": open_, "high": high, "low": low, "close": close, "volume": volume},
        index=idx,
    )


def test_rsi_momentum_returns_signal_on_setup():
    strategy = RsiMomentum(
        rsi_period=14,
        rsi_pullback_low=40.0,
        rsi_pullback_high=55.0,
        trend_ema_period=50,
        volume_confirm_ratio=1.0,
    )
    sig = strategy.evaluate("TEST", _rsi_momentum_candles(), Regime.TREND)
    assert sig is not None, (
        "RsiMomentum should fire when price > EMA-50, RSI in [40,55] and rising, volume ok"
    )
    assert sig.strategy == "rsi_momentum"
    assert sig.side == Side.BUY
    assert sig.stop_loss < sig.entry_price
    assert sig.target > sig.entry_price


def test_rsi_momentum_returns_none_on_wrong_regime():
    strategy = RsiMomentum()
    candles = _rsi_momentum_candles()
    assert strategy.evaluate("TEST", candles, Regime.RANGE) is None
    assert strategy.evaluate("TEST", candles, Regime.VOLATILE) is None
    assert strategy.evaluate("TEST", candles, Regime.UNKNOWN) is None


def test_rsi_momentum_returns_none_on_insufficient_data():
    assert RsiMomentum().evaluate("TEST", _make_ohlcv(20), Regime.TREND) is None


def test_rsi_momentum_signal_has_positive_rr():
    sig = RsiMomentum(target_r_multiple=2.0).evaluate(
        "TEST", _rsi_momentum_candles(), Regime.TREND
    )
    if sig is not None:
        reward = sig.target - sig.entry_price
        risk = sig.entry_price - sig.stop_loss
        assert reward >= risk, f"R:R < 1: reward={reward:.4f}, risk={risk:.4f}"


# ===========================================================================
# BbSqueeze
# ===========================================================================

def _bb_squeeze_candles() -> pd.DataFrame:
    """
    100-bar series: gently rising for bars 0-72 (so DMA is rising), then
    PERFECTLY flat for bars 73-98 (zero std → BB width collapses to 0,
    satisfying the squeeze condition), and a large up bar on bar 99 that
    breaks above BB upper.
    """
    n = 100
    close = np.empty(n, dtype=float)

    # Phase 1: gentle rise (0.4 per bar) → DMA rising, close above DMA
    for i in range(73):
        close[i] = 1000.0 + i * 0.4     # 1000 → 1028.8

    # Phase 2: perfectly flat → BB std → 0 → BB width → 0
    flat_val = close[72]
    for i in range(73, 99):
        close[i] = flat_val

    # Phase 3: breakout above the (near-zero) upper band
    close[-1] = flat_val + 10.0

    high = close + 2.0
    low = close - 2.0
    open_ = np.r_[close[0], close[:-1]]
    volume = np.full(n, 2_000_000.0)
    idx = pd.bdate_range("2023-01-02", periods=n)
    return pd.DataFrame(
        {"open": open_, "high": high, "low": low, "close": close, "volume": volume},
        index=idx,
    )


def test_bb_squeeze_returns_signal_on_setup():
    strategy = BbSqueeze(bb_period=20, squeeze_lookback=20, stock_dma_period=50)
    sig = strategy.evaluate("TEST", _bb_squeeze_candles(), Regime.TREND)
    assert sig is not None, "BbSqueeze should fire after BB squeeze + close > upper band"
    assert sig.strategy == "bb_squeeze"
    assert sig.side == Side.BUY
    assert sig.stop_loss < sig.entry_price
    assert sig.target > sig.entry_price


def test_bb_squeeze_returns_none_on_wrong_regime():
    strategy = BbSqueeze()
    candles = _bb_squeeze_candles()
    assert strategy.evaluate("TEST", candles, Regime.VOLATILE) is None
    assert strategy.evaluate("TEST", candles, Regime.UNKNOWN) is None


def test_bb_squeeze_returns_none_on_insufficient_data():
    assert BbSqueeze().evaluate("TEST", _make_ohlcv(10), Regime.TREND) is None


def test_bb_squeeze_signal_has_positive_rr():
    sig = BbSqueeze(target_r_multiple=2.0).evaluate(
        "TEST", _bb_squeeze_candles(), Regime.TREND
    )
    if sig is not None:
        reward = sig.target - sig.entry_price
        risk = sig.entry_price - sig.stop_loss
        assert reward >= risk, f"R:R < 1: reward={reward:.4f}, risk={risk:.4f}"


# ===========================================================================
# Supertrend  (patched indicator)
# ===========================================================================

def _supertrend_df(n: int = 30) -> pd.DataFrame:
    """Minimal valid DataFrame for the Supertrend strategy."""
    close = 1000.0 + np.linspace(0, 30, n)
    high = close * 1.002
    low = close * 0.998
    return pd.DataFrame(
        {
            "open": np.r_[close[0], close[:-1]],
            "high": high,
            "low": low,
            "close": close,
            "volume": np.full(n, 2_000_000.0),
        },
        index=pd.bdate_range("2023-01-02", periods=n),
    )


def _supertrend_mock_bands(df: pd.DataFrame, *, bullish_flip: bool = True):
    """
    Return (final_upper, final_lower, direction) arrays where the last bar has
    a bullish flip (direction[-2]==-1, direction[-1]==1) if bullish_flip=True,
    otherwise all +1 (no flip → strategy must return None).
    """
    n = len(df)
    close = df["close"].to_numpy()
    fu = np.full(n, close.mean() * 1.15)   # upper band comfortably above close
    fl = np.full(n, close.mean() * 0.90)   # lower band below close → valid stop
    direction = np.ones(n, dtype=np.int8)
    if bullish_flip:
        direction[-2] = -1   # was bearish yesterday
        # direction[-1] stays +1 → fresh flip
    return fu, fl, direction


def test_supertrend_returns_signal_on_setup():
    # Use a 100-bar uptrend so DMA filters (20-DMA, 50-DMA + 10-bar lookback) all resolve.
    # Disable quality filter thresholds (adx_min=0, volume_ratio_min=0) so synthetic
    # data doesn't need to produce real ADX/volume values.
    df = _supertrend_df(110)
    with patch(
        "core.strategies.supertrend.supertrend_bands",
        side_effect=lambda df_, p, m: _supertrend_mock_bands(df_, bullish_flip=True),
    ):
        strategy = Supertrend(
            atr_period=10, multiplier=3.0, target_r_multiple=2.0,
            adx_min=0.0, volume_ratio_min=0.0,  # bypass quality filters for smoke test
        )
        sig = strategy.evaluate("TEST", df, Regime.TREND)
    assert sig is not None, "Supertrend should fire on direction flip to bullish"
    assert sig.strategy == "supertrend"
    assert sig.side == Side.BUY
    assert sig.stop_loss < sig.entry_price
    assert sig.target > sig.entry_price


def test_supertrend_returns_none_on_wrong_regime():
    # No patching needed — wrong regime check fires before indicator is called
    strategy = Supertrend()
    df = _supertrend_df(30)
    assert strategy.evaluate("TEST", df, Regime.RANGE) is None
    assert strategy.evaluate("TEST", df, Regime.UNKNOWN) is None


def test_supertrend_returns_none_on_insufficient_data():
    # Also no patching needed — length check fires first
    assert Supertrend(atr_period=10).evaluate("TEST", _make_ohlcv(5), Regime.TREND) is None


def test_supertrend_signal_has_positive_rr():
    df = _supertrend_df()
    with patch(
        "core.strategies.supertrend.supertrend_bands",
        side_effect=lambda df_, p, m: _supertrend_mock_bands(df_, bullish_flip=True),
    ):
        sig = Supertrend(target_r_multiple=2.0).evaluate("TEST", df, Regime.TREND)
    if sig is not None:
        reward = sig.target - sig.entry_price
        risk = sig.entry_price - sig.stop_loss
        assert reward >= risk, f"R:R < 1: reward={reward:.4f}, risk={risk:.4f}"


# ===========================================================================
# SupertrendShort  (patched indicator)
# ===========================================================================

def _supertrend_short_df() -> pd.DataFrame:
    """
    100-bar series where ALL quality filters pass on the last bar:
    - 94 bars of slow rise, then 6 bars of sharp decline (~0.8%/bar).
    - 20-bar DMA is falling TODAY (dma[-1] < dma[-11]) but was NOT falling
      yesterday (dma[-2] >= dma[-12]) — satisfies the fresh-signal check.
    - 10-day return ~= -4.5% (satisfies the < -3% threshold).
    - Volume on the last bar is 3x average (satisfies the 1.5x spike filter).
    We use stock_dma_period=20 (instead of the default 50) to keep n small.
    """
    n = 100
    drop_bars = 6
    close = np.empty(n, dtype=float)
    for i in range(n - drop_bars):
        close[i] = 1000.0 + i * 1.0
    peak = close[n - drop_bars - 1]
    for i in range(drop_bars):
        close[n - drop_bars + i] = peak * (1.0 - (i + 1) * 0.008)

    high = close * 1.002
    low = close * 0.998
    # Last bar has a volume spike (3x average) for the volume_spike_min=1.5 filter
    volume = np.full(n, 2_000_000.0)
    volume[-1] = 6_000_000.0
    return pd.DataFrame(
        {
            "open": np.r_[close[0], close[:-1]],
            "high": high,
            "low": low,
            "close": close,
            "volume": volume,
        },
        index=pd.bdate_range("2023-01-02", periods=n),
    )


def _supertrend_short_mock_bands(df: pd.DataFrame):
    """All-bearish direction; final_upper above close (acts as stop for short)."""
    n = len(df)
    close = df["close"].to_numpy()
    fu = np.full(n, close.mean() * 1.15)   # above close → stop above entry
    fl = np.full(n, close.mean() * 0.85)
    direction = np.full(n, -1, dtype=np.int8)   # always bearish
    return fu, fl, direction


def test_supertrend_short_returns_signal_on_setup():
    df = _supertrend_short_df()
    with patch(
        "core.strategies.supertrend_short.supertrend_bands",
        side_effect=lambda df_, p, m: _supertrend_short_mock_bands(df_),
    ):
        strategy = SupertrendShort(
            atr_period=10, multiplier=3.0,
            target_r_multiple=2.0, stock_dma_period=20,
            dma_falling_lookback=10,
            return_threshold=-0.03,
            volume_spike_min=1.5,
        )
        sig = strategy.evaluate("TEST", df, Regime.VOLATILE)
    assert sig is not None, "SupertrendShort should fire when Supertrend bearish + DMA falling + volume spike"
    assert sig.strategy == "supertrend_short"
    assert sig.side == Side.SELL
    assert sig.stop_loss > sig.entry_price, "Short stop must be above entry"
    assert sig.target < sig.entry_price, "Short target must be below entry"


def test_supertrend_short_returns_none_on_wrong_regime():
    strategy = SupertrendShort()
    df = _supertrend_short_df()
    assert strategy.evaluate("TEST", df, Regime.RANGE) is None
    assert strategy.evaluate("TEST", df, Regime.UNKNOWN) is None


def test_supertrend_short_returns_none_on_insufficient_data():
    assert SupertrendShort().evaluate("TEST", _make_ohlcv(20), Regime.VOLATILE) is None


def test_supertrend_short_signal_has_positive_rr():
    df = _supertrend_short_df()
    with patch(
        "core.strategies.supertrend_short.supertrend_bands",
        side_effect=lambda df_, p, m: _supertrend_short_mock_bands(df_),
    ):
        sig = SupertrendShort(
            target_r_multiple=2.0, stock_dma_period=20,
            dma_falling_lookback=10,
            return_threshold=-0.03,
            volume_spike_min=1.5,
        ).evaluate("TEST", df, Regime.VOLATILE)
    if sig is not None:
        reward = sig.entry_price - sig.target    # short: profit = entry - target
        risk = sig.stop_loss - sig.entry_price   # short: loss  = stop  - entry
        assert reward >= risk, f"R:R < 1: reward={reward:.4f}, risk={risk:.4f}"


# ===========================================================================
# ObvTrend
# ===========================================================================

def _obv_trend_candles() -> pd.DataFrame:
    """
    60-bar uptrend.  Last bar: close breaks the 15-bar price high AND volume is
    so large that OBV also reaches a 15-bar high.
    """
    n = 60
    rng = np.random.default_rng(10)
    close = 1000.0 + np.linspace(0, 60, n)
    high = close + rng.uniform(2, 5, n)
    low = close - rng.uniform(2, 5, n)
    open_ = np.r_[close[0], close[:-1]]
    volume = rng.integers(1_000_000, 3_000_000, n).astype(float)
    idx = pd.bdate_range("2023-01-02", periods=n)
    df = pd.DataFrame(
        {"open": open_, "high": high, "low": low, "close": close, "volume": volume},
        index=idx,
    )
    # Last bar: price breaks the 15-bar high and volume dominates OBV
    prev_price_high = float(df["close"].iloc[-16:-1].max())
    df.loc[df.index[-1], "close"] = prev_price_high * 1.02
    df.loc[df.index[-1], "high"] = prev_price_high * 1.025
    df.loc[df.index[-1], "volume"] = 10_000_000.0   # enormous → OBV also at new high
    return df


def test_obv_trend_returns_signal_on_setup():
    strategy = ObvTrend(breakout_period=15, atr_period=14)
    sig = strategy.evaluate("TEST", _obv_trend_candles(), Regime.TREND)
    assert sig is not None, "ObvTrend should fire on price + OBV breakout"
    assert sig.strategy == "obv_trend"
    assert sig.side == Side.BUY
    assert sig.stop_loss < sig.entry_price
    assert sig.target > sig.entry_price


def test_obv_trend_returns_none_on_wrong_regime():
    strategy = ObvTrend()
    candles = _obv_trend_candles()
    assert strategy.evaluate("TEST", candles, Regime.RANGE) is None
    assert strategy.evaluate("TEST", candles, Regime.VOLATILE) is None
    assert strategy.evaluate("TEST", candles, Regime.UNKNOWN) is None


def test_obv_trend_returns_none_on_insufficient_data():
    assert ObvTrend().evaluate("TEST", _make_ohlcv(10), Regime.TREND) is None


def test_obv_trend_signal_has_positive_rr():
    sig = ObvTrend(target_r_multiple=2.5).evaluate(
        "TEST", _obv_trend_candles(), Regime.TREND
    )
    if sig is not None:
        reward = sig.target - sig.entry_price
        risk = sig.entry_price - sig.stop_loss
        assert reward >= risk, f"R:R < 1: reward={reward:.4f}, risk={risk:.4f}"
