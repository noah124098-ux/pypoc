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
