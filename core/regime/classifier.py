"""Market regime classifier — TREND / RANGE / VOLATILE.

Logic (kept simple and explainable on purpose — every decision must be auditable):
  - VOLATILE  if VIX > vix_volatile_threshold  OR  recent realised vol spike
  - TREND     if Nifty ADX > adx_trend_threshold
  - RANGE     if Bollinger width (relative to price) < bb_width_range_threshold
  - else      UNKNOWN (no new entries)

Re-evaluated on every Nifty candle close.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from core.config import RegimeCfg
from core.types import Regime


@dataclass
class RegimeSnapshot:
    regime: Regime
    adx: float
    bb_width: float
    vix: float
    rationale: str


class RegimeClassifier:
    def __init__(self, cfg: RegimeCfg):
        self.cfg = cfg

    def classify(self, nifty_ohlc: pd.DataFrame, vix: float) -> RegimeSnapshot:
        if len(nifty_ohlc) < max(self.cfg.adx_period * 2, 25):
            return RegimeSnapshot(Regime.UNKNOWN, 0.0, 0.0, vix, "insufficient history")

        adx = _adx(nifty_ohlc, self.cfg.adx_period)
        bb_width = _bb_width(nifty_ohlc, period=20, n_std=2.0)

        if vix > self.cfg.vix_volatile_threshold:
            return RegimeSnapshot(Regime.VOLATILE, adx, bb_width, vix,
                                   f"VIX {vix:.1f} > {self.cfg.vix_volatile_threshold}")
        if adx > self.cfg.adx_trend_threshold:
            # Verify market is actually trending (not just high-volatility mean-reverting)
            # Autocorr > 0 = momentum/trending, autocorr <= 0 = mean-reverting choppy
            if len(nifty_ohlc) >= 22:
                _ac = nifty_ohlc["close"].iloc[-20:].autocorr(lag=1)
                if not pd.isna(_ac) and _ac <= 0:
                    # High ADX but mean-reverting: reclassify as RANGE
                    return RegimeSnapshot(Regime.RANGE, adx, bb_width, vix,
                        f"ADX {adx:.1f}>{self.cfg.adx_trend_threshold} but autocorr={_ac:.3f}<=0 (mean-reverting)")
            return RegimeSnapshot(Regime.TREND, adx, bb_width, vix,
                                   f"ADX {adx:.1f} > {self.cfg.adx_trend_threshold}")
        if bb_width < self.cfg.bb_width_range_threshold:
            return RegimeSnapshot(Regime.RANGE, adx, bb_width, vix,
                                   f"BB width {bb_width:.4f} < {self.cfg.bb_width_range_threshold}")
        return RegimeSnapshot(Regime.UNKNOWN, adx, bb_width, vix, "no clear regime")


def _adx(df: pd.DataFrame, period: int) -> float:
    high, low, close = df["high"], df["low"], df["close"]
    tr = pd.concat(
        [
            (high - low),
            (high - close.shift()).abs(),
            (low - close.shift()).abs(),
        ],
        axis=1,
    ).max(axis=1)
    plus_dm = (high.diff()).where((high.diff() > -low.diff()) & (high.diff() > 0), 0.0)
    minus_dm = (-low.diff()).where((-low.diff() > high.diff()) & (-low.diff() > 0), 0.0)
    atr = tr.rolling(period).mean()
    plus_di = 100 * plus_dm.rolling(period).mean() / atr
    minus_di = 100 * minus_dm.rolling(period).mean() / atr
    dx = (abs(plus_di - minus_di) / (plus_di + minus_di).replace(0, np.nan)) * 100
    adx = dx.rolling(period).mean()
    return float(adx.iloc[-1]) if not np.isnan(adx.iloc[-1]) else 0.0


def _bb_width(df: pd.DataFrame, period: int, n_std: float) -> float:
    close = df["close"]
    ma = close.rolling(period).mean()
    sd = close.rolling(period).std()
    upper = ma + n_std * sd
    lower = ma - n_std * sd
    width = (upper - lower) / ma
    return float(width.iloc[-1]) if not np.isnan(width.iloc[-1]) else 0.0
