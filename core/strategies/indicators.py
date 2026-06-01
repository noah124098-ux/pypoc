"""Indicator helpers used by multiple strategies. Pure functions over OHLC DataFrame."""
from __future__ import annotations

import numpy as np
import pandas as pd


def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    high, low, close = df["high"], df["low"], df["close"]
    tr = pd.concat(
        [
            (high - low),
            (high - close.shift()).abs(),
            (low - close.shift()).abs(),
        ],
        axis=1,
    ).max(axis=1)
    return tr.rolling(period).mean()


def rsi(df: pd.DataFrame, period: int = 14) -> pd.Series:
    delta = df["close"].diff()
    gain = delta.where(delta > 0, 0.0).rolling(period).mean()
    loss = -delta.where(delta < 0, 0.0).rolling(period).mean()
    rs = gain / loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def bollinger(df: pd.DataFrame, period: int = 20, n_std: float = 2.0):
    ma = df["close"].rolling(period).mean()
    sd = df["close"].rolling(period).std()
    return ma, ma + n_std * sd, ma - n_std * sd


def donchian(df: pd.DataFrame, period: int = 20):
    return df["high"].rolling(period).max(), df["low"].rolling(period).min()


def volume_ratio(df: pd.DataFrame, period: int = 20) -> pd.Series:
    """Latest volume / N-day average volume. > 1.0 means above-average volume."""
    avg = df["volume"].rolling(period).mean()
    return df["volume"] / avg.replace(0, float("nan"))


def supertrend_bands(
    df: pd.DataFrame, atr_period: int = 10, multiplier: float = 3.0
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Compute Supertrend final_upper, final_lower, and direction arrays (numpy).

    Returns (final_upper, final_lower, direction) as 1-D float64/int8 numpy arrays
    aligned to df's index. Uses a fast numpy loop -- ~50x faster than the pandas
    iloc-based implementation for 800-bar daily histories.

    direction: +1 = bullish (price above lower band), -1 = bearish.
    """
    close = df["close"].to_numpy(dtype=np.float64)
    high = df["high"].to_numpy(dtype=np.float64)
    low = df["low"].to_numpy(dtype=np.float64)

    hl2 = (high + low) / 2.0
    # Compute ATR via rolling mean of true range
    prev_close = np.empty_like(close)
    prev_close[0] = close[0]
    prev_close[1:] = close[:-1]
    tr = np.maximum.reduce([high - low, np.abs(high - prev_close), np.abs(low - prev_close)])
    # Simple rolling mean ATR (first atr_period values are nan)
    atr_arr = np.full_like(close, np.nan)
    for i in range(atr_period - 1, len(close)):
        atr_arr[i] = tr[max(0, i - atr_period + 1): i + 1].mean()

    upper_band = hl2 + multiplier * atr_arr
    lower_band = hl2 - multiplier * atr_arr

    n = len(close)
    final_upper = upper_band.copy()
    final_lower = lower_band.copy()
    direction = np.ones(n, dtype=np.int8)

    for i in range(1, n):
        if np.isnan(atr_arr[i]):
            direction[i] = direction[i - 1]
            final_upper[i] = upper_band[i]
            final_lower[i] = lower_band[i]
            continue
        # Final upper: only tighten (lower), unless price broke above previous upper
        if upper_band[i] < final_upper[i - 1] or close[i - 1] > final_upper[i - 1]:
            final_upper[i] = upper_band[i]
        else:
            final_upper[i] = final_upper[i - 1]
        # Final lower: only tighten (higher), unless price broke below previous lower
        if lower_band[i] > final_lower[i - 1] or close[i - 1] < final_lower[i - 1]:
            final_lower[i] = lower_band[i]
        else:
            final_lower[i] = final_lower[i - 1]
        # Direction
        if close[i] > final_upper[i - 1]:
            direction[i] = 1
        elif close[i] < final_lower[i - 1]:
            direction[i] = -1
        else:
            direction[i] = direction[i - 1]

    return final_upper, final_lower, direction


def adx_value(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Compute ADX (Average Directional Index). Returns smoothed ADX series."""
    high, low, close = df["high"], df["low"], df["close"]
    up = high.diff()
    down = -low.diff()
    plus_dm = up.where((up > down) & (up > 0), 0.0)
    minus_dm = down.where((down > up) & (down > 0), 0.0)
    tr = pd.concat([(high - low), (high - close.shift()).abs(), (low - close.shift()).abs()], axis=1).max(axis=1)
    atr_s = tr.ewm(alpha=1 / period, adjust=False).mean()
    plus_di = 100 * plus_dm.ewm(alpha=1 / period, adjust=False).mean() / atr_s.replace(0, float("nan"))
    minus_di = 100 * minus_dm.ewm(alpha=1 / period, adjust=False).mean() / atr_s.replace(0, float("nan"))
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, float("nan"))
    return dx.ewm(alpha=1 / period, adjust=False).mean()


def rolling_autocorr(series: pd.Series, lag: int = 1, window: int = 20) -> pd.Series:
    """Rolling autocorrelation with given lag over a rolling window.
    Positive = trending (momentum), Negative = mean-reverting.
    Returns pd.Series of same length as input.
    """
    return series.rolling(window).apply(
        lambda x: x.autocorr(lag=lag) if len(x) >= lag + 1 else float("nan"),
        raw=False,
    )


def hurst_exponent(series: pd.Series, min_lag: int = 2, max_lag: int = 20) -> float:
    """Estimate Hurst exponent using R/S analysis.
    H > 0.5: trending (persistent) — breakout strategies work
    H = 0.5: random walk
    H < 0.5: mean-reverting — breakout strategies FAIL
    Returns float in [0, 1], or 0.5 if insufficient data.
    """
    if len(series) < max_lag + 1:
        return 0.5
    try:
        lags = range(min_lag, min(max_lag, len(series) // 2))
        rs_vals = []
        for lag in lags:
            sub = series.values
            rs_list = []
            for start in range(0, len(sub) - lag, lag):
                chunk = sub[start:start + lag]
                if len(chunk) < 2:
                    continue
                mean_c = chunk.mean()
                if mean_c == 0:
                    continue
                dev = chunk - mean_c
                cum_dev = np.cumsum(dev)
                r = cum_dev.max() - cum_dev.min()
                s = chunk.std(ddof=1)
                if s > 0:
                    rs_list.append(r / s)
            if rs_list:
                rs_vals.append((lag, np.mean(rs_list)))
        if len(rs_vals) < 2:
            return 0.5
        lags_log = np.log([x[0] for x in rs_vals])
        rs_log = np.log([x[1] for x in rs_vals])
        h = np.polyfit(lags_log, rs_log, 1)[0]
        return float(np.clip(h, 0.0, 1.0))
    except Exception:
        return 0.5


def is_trending_market(nifty_close: pd.Series, lookback: int = 20) -> bool:
    """Returns True if Nifty is in a trending (persistent) regime.
    Uses rolling autocorrelation: True when autocorr(lag=1, window=lookback) > 0.
    """
    if len(nifty_close) < lookback + 2:
        return True  # fail-open
    autocorr_val = nifty_close.iloc[-lookback:].autocorr(lag=1)
    return bool(not pd.isna(autocorr_val) and autocorr_val > 0)
