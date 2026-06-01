"""Tests for rolling_autocorr, hurst_exponent, and is_trending_market indicators."""
from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

from core.strategies.indicators import (
    hurst_exponent,
    is_trending_market,
    rolling_autocorr,
)


# ---------------------------------------------------------------------------
# rolling_autocorr
# ---------------------------------------------------------------------------

def test_rolling_autocorr_returns_correct_length():
    """Output Series must have the same length as the input."""
    s = pd.Series(range(50), dtype=float)
    result = rolling_autocorr(s, lag=1, window=20)
    assert len(result) == len(s)


def test_rolling_autocorr_first_values_are_nan():
    """Values before the first full window must be NaN."""
    s = pd.Series(range(50), dtype=float)
    result = rolling_autocorr(s, lag=1, window=20)
    # First (window-1) entries should be NaN (rolling not yet full)
    assert result.iloc[:19].isna().all()


def test_rolling_autocorr_positive_on_trend():
    """A monotonically increasing series should yield positive autocorrelation."""
    s = pd.Series(np.arange(1, 101, dtype=float))
    result = rolling_autocorr(s, lag=1, window=20)
    last_val = result.dropna().iloc[-1]
    assert last_val > 0, f"Expected positive autocorr on trend, got {last_val}"


def test_rolling_autocorr_negative_on_oscillation():
    """An alternating +1/-1 series should yield negative autocorrelation."""
    vals = [1.0 if i % 2 == 0 else -1.0 for i in range(60)]
    s = pd.Series(vals)
    result = rolling_autocorr(s, lag=1, window=20)
    last_val = result.dropna().iloc[-1]
    assert last_val < 0, f"Expected negative autocorr on oscillation, got {last_val}"


# ---------------------------------------------------------------------------
# hurst_exponent
# ---------------------------------------------------------------------------

def test_hurst_returns_half_on_insufficient_data():
    """With fewer than max_lag+1 points, should return 0.5."""
    s = pd.Series([1.0, 2.0, 3.0])
    h = hurst_exponent(s, min_lag=2, max_lag=20)
    assert h == pytest.approx(0.5)


def test_hurst_greater_than_half_on_uptrend():
    """A pure uptrend (1,2,3,...) is highly persistent; H should be > 0.5."""
    s = pd.Series(np.arange(1, 201, dtype=float))
    h = hurst_exponent(s, min_lag=2, max_lag=20)
    assert h > 0.5, f"Expected H > 0.5 on uptrend, got {h}"


def test_hurst_less_than_half_on_oscillation():
    """A tight +1/-1 oscillation is mean-reverting; H should be < 0.5."""
    vals = []
    for i in range(200):
        vals.append(1.0 if i % 2 == 0 else -1.0)
    s = pd.Series(vals)
    h = hurst_exponent(s, min_lag=2, max_lag=20)
    assert h < 0.5, f"Expected H < 0.5 on oscillation, got {h}"


def test_hurst_returns_float_in_unit_interval():
    """Return value must be clipped to [0, 1]."""
    s = pd.Series(np.random.default_rng(42).normal(size=100))
    h = hurst_exponent(s)
    assert 0.0 <= h <= 1.0


def test_hurst_does_not_raise_on_constant_series():
    """A constant series has zero std; must return 0.5 without raising."""
    s = pd.Series([5.0] * 50)
    h = hurst_exponent(s)
    assert h == pytest.approx(0.5)


# ---------------------------------------------------------------------------
# is_trending_market
# ---------------------------------------------------------------------------

def test_is_trending_market_true_on_trend():
    """Monotonically increasing series should be classified as trending."""
    s = pd.Series(np.arange(1, 101, dtype=float))
    assert is_trending_market(s, lookback=20) is True


def test_is_trending_market_false_on_oscillation():
    """Alternating series should NOT be classified as trending."""
    vals = [float(1 if i % 2 == 0 else -1) for i in range(100)]
    s = pd.Series(vals)
    assert is_trending_market(s, lookback=20) is False


def test_is_trending_market_fail_open_on_short_series():
    """When series is shorter than lookback+2, return True (fail-open)."""
    s = pd.Series([1.0, 2.0, 3.0])
    assert is_trending_market(s, lookback=20) is True
