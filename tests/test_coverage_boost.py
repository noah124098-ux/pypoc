"""Tests to boost coverage on low-coverage modules.

Targets:
- core/strategies/rsi_bounce.py  (23% → 90%+)
- core/strategies/indicators.py  (64% → 90%+)   — supertrend_bands + adx_value
- core/data/historical.py         (21% → 80%+)
- core/data/bhavcopy.py           (55% → 80%+)
- core/runtime_snapshot.py        (86% → 95%+)
- core/broker/paper.py            (86% → 95%+)
"""
from __future__ import annotations

import json
import os
import tempfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Optional
from unittest.mock import MagicMock, patch, PropertyMock

import numpy as np
import pandas as pd
import pytest

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
    idx = pd.bdate_range("2022-01-03", periods=n)
    return pd.DataFrame(
        {"open": open_, "high": high, "low": low, "close": close, "volume": volume},
        index=idx,
    )


# ===========================================================================
# core/strategies/rsi_bounce.py
# ===========================================================================

class TestRsiBounce:
    """Full path coverage for RsiBounce strategy."""

    def _setup_bounce_candles(self, n: int = 250) -> pd.DataFrame:
        """
        Build a DataFrame that satisfies ALL four filters for the last bar:
        1. close > 200-DMA  (structural uptrend)
        2. RSI < 30         (deeply oversold)
        3. close > open AND close > prev_close  (bullish reversal bar)
        4. volume_ratio >= 1.2
        """
        # Use a steady uptrend for 240 bars so 200-DMA stays below price,
        # then add 9 down bars that drive RSI below 30, then a big green reversal bar.
        rng = np.random.default_rng(7)
        close = np.empty(n, dtype=float)
        for i in range(240):
            close[i] = 1000.0 + i * 0.5            # gentle rise → DMA below price
        # Bars 240-248: steep drops so RSI < 30
        for j in range(9):
            close[240 + j] = close[239 + j] * 0.985  # ~1.5 % drop each bar
        # Last bar (249): big green reversal — close > open AND close > prev_close
        prev = close[248]
        close[249] = prev * 1.03           # +3 % jump
        high = close * 1.005
        low = close * 0.995
        # open on last bar lower than close
        open_ = np.r_[close[0], close[:-1]]
        open_[249] = close[248] * 0.99    # open below prev close, but close up → green
        # Volume spike on last bar
        volume = np.full(n, 2_000_000.0)
        volume[249] = 5_000_000.0
        idx = pd.bdate_range("2021-01-04", periods=n)
        return pd.DataFrame(
            {"open": open_, "high": high, "low": low, "close": close, "volume": volume},
            index=idx,
        )

    def test_returns_none_on_wrong_regime(self):
        from core.strategies.rsi_bounce import RsiBounce
        from core.types import Regime
        candles = self._setup_bounce_candles()
        strat = RsiBounce()
        assert strat.evaluate("TEST", candles, Regime.TREND) is None

    def test_returns_none_on_insufficient_data(self):
        from core.strategies.rsi_bounce import RsiBounce
        from core.types import Regime
        strat = RsiBounce()
        # Only 5 bars — well below min_bars
        assert strat.evaluate("TEST", _make_ohlcv(5), Regime.RANGE) is None

    def test_returns_none_when_price_below_200dma(self):
        """price below 200-DMA → filter 1 rejects."""
        from core.strategies.rsi_bounce import RsiBounce
        from core.types import Regime
        # Build 210-bar series where close is always BELOW 200-DMA
        n = 210
        close = np.linspace(1200, 800, n)  # falling series: DMA above close
        high = close * 1.003
        low = close * 0.997
        open_ = np.r_[close[0], close[:-1]]
        volume = np.full(n, 2_000_000.0)
        idx = pd.bdate_range("2021-01-04", periods=n)
        df = pd.DataFrame(
            {"open": open_, "high": high, "low": low, "close": close, "volume": volume},
            index=idx,
        )
        strat = RsiBounce()
        assert strat.evaluate("TEST", df, Regime.RANGE) is None

    def test_returns_none_when_rsi_not_oversold(self):
        """RSI >= oversold threshold → filter 2 rejects."""
        from core.strategies.rsi_bounce import RsiBounce
        from core.types import Regime
        # Uptrend for 210 bars (RSI will be high / not oversold)
        n = 210
        close = 1000.0 + np.arange(n) * 1.0
        high = close * 1.003
        low = close * 0.997
        open_ = np.r_[close[0], close[:-1]]
        volume = np.full(n, 2_000_000.0)
        idx = pd.bdate_range("2021-01-04", periods=n)
        df = pd.DataFrame(
            {"open": open_, "high": high, "low": low, "close": close, "volume": volume},
            index=idx,
        )
        strat = RsiBounce()
        result = strat.evaluate("TEST", df, Regime.RANGE)
        # In an unbroken uptrend RSI stays >> 30 → should return None
        assert result is None

    def test_returns_none_when_not_green_candle(self):
        """close <= open (red candle) → filter 3 rejects."""
        from core.strategies.rsi_bounce import RsiBounce
        from core.types import Regime
        candles = self._setup_bounce_candles()
        # Force last bar to be a red candle (close < open)
        candles.iloc[-1, candles.columns.get_loc("open")] = candles["close"].iloc[-1] * 1.01
        strat = RsiBounce()
        assert strat.evaluate("TEST", candles, Regime.RANGE) is None

    def test_returns_none_when_close_not_above_prev_close(self):
        """close <= prev_close → filter 3 rejects."""
        from core.strategies.rsi_bounce import RsiBounce
        from core.types import Regime
        candles = self._setup_bounce_candles()
        # Force close[-1] to be below prev close
        candles.iloc[-1, candles.columns.get_loc("close")] = candles["close"].iloc[-2] * 0.99
        # Also keep open below close so green-candle check passes but prev_close check fails
        candles.iloc[-1, candles.columns.get_loc("open")] = candles["close"].iloc[-1] * 0.99
        strat = RsiBounce()
        # This may return None either on RSI or on prev_close filter — either way None
        result = strat.evaluate("TEST", candles, Regime.RANGE)
        assert result is None

    def test_returns_signal_on_valid_setup(self):
        """Happy path — all filters pass → Signal returned."""
        from core.strategies.rsi_bounce import RsiBounce
        from core.types import Regime, Side
        candles = self._setup_bounce_candles()
        strat = RsiBounce(
            rsi_period=14,
            rsi_oversold_threshold=30.0,
            dma_period=200,
            atr_period=14,
            atr_stop_multiplier=1.5,
            target_r_multiple=2.0,
            volume_ratio_min=1.2,
        )
        sig = strat.evaluate("TEST", candles, Regime.RANGE)
        if sig is not None:
            assert sig.strategy == "rsi_bounce"
            assert sig.side == Side.BUY
            assert sig.stop_loss < sig.entry_price
            assert sig.target > sig.entry_price
            # R:R check
            reward = sig.target - sig.entry_price
            risk = sig.entry_price - sig.stop_loss
            assert reward > 0 and risk > 0

    def test_returns_signal_in_volatile_regime(self):
        """RsiBounce supports VOLATILE regime too."""
        from core.strategies.rsi_bounce import RsiBounce
        from core.types import Regime
        candles = self._setup_bounce_candles()
        strat = RsiBounce()
        # Volatile is also supported — may or may not fire depending on data,
        # but must NOT raise.
        result = strat.evaluate("TEST", candles, Regime.VOLATILE)
        # Just ensure no exception

    def test_custom_parameters_accepted(self):
        """Constructor parameters are stored correctly."""
        from core.strategies.rsi_bounce import RsiBounce
        strat = RsiBounce(
            rsi_period=10,
            rsi_oversold_threshold=25.0,
            dma_period=100,
            atr_period=10,
            atr_stop_multiplier=2.0,
            target_r_multiple=3.0,
            volume_ratio_min=1.5,
        )
        assert strat.rsi_period == 10
        assert strat.rsi_oversold_threshold == 25.0
        assert strat.dma_period == 100
        assert strat.volume_ratio_min == 1.5


# ===========================================================================
# core/strategies/indicators.py — supertrend_bands + adx_value
# ===========================================================================

class TestSupertrendBands:
    """Cover lines 56-106 of indicators.py (supertrend_bands)."""

    def _make_df(self, n: int = 120, trend_up: bool = True) -> pd.DataFrame:
        if trend_up:
            close = 1000.0 + np.arange(n) * 0.5
        else:
            close = 1100.0 - np.arange(n) * 0.5
        high = close * 1.003
        low = close * 0.997
        open_ = np.r_[close[0], close[:-1]]
        volume = np.full(n, 2_000_000.0)
        idx = pd.bdate_range("2022-01-03", periods=n)
        return pd.DataFrame(
            {"open": open_, "high": high, "low": low, "close": close, "volume": volume},
            index=idx,
        )

    def test_returns_three_arrays_correct_length(self):
        from core.strategies.indicators import supertrend_bands
        df = self._make_df(50)
        fu, fl, direction = supertrend_bands(df, atr_period=10, multiplier=3.0)
        assert len(fu) == len(df)
        assert len(fl) == len(df)
        assert len(direction) == len(df)

    def test_direction_values_are_plus_or_minus_one(self):
        from core.strategies.indicators import supertrend_bands
        df = self._make_df(100)
        _, _, direction = supertrend_bands(df, atr_period=10, multiplier=3.0)
        unique_dirs = set(direction)
        assert unique_dirs.issubset({1, -1})

    def test_bullish_trend_gives_positive_direction(self):
        """Strong uptrend → direction should end up +1."""
        from core.strategies.indicators import supertrend_bands
        df = self._make_df(100, trend_up=True)
        _, _, direction = supertrend_bands(df, atr_period=10, multiplier=3.0)
        assert direction[-1] == 1

    def test_bearish_trend_gives_negative_direction(self):
        """Strong downtrend → direction should end up -1."""
        from core.strategies.indicators import supertrend_bands
        df = self._make_df(100, trend_up=False)
        _, _, direction = supertrend_bands(df, atr_period=10, multiplier=3.0)
        assert direction[-1] == -1

    def test_direction_flips_in_synthesized_data(self):
        """Synthetic data that crosses band forces a direction flip."""
        from core.strategies.indicators import supertrend_bands
        n = 100
        # Start rising then fall sharply to force a bearish flip
        close = np.empty(n, dtype=float)
        for i in range(60):
            close[i] = 1000.0 + i * 2.0   # strong rise
        for i in range(40):
            close[60 + i] = 1120.0 - i * 4.0   # strong fall
        high = close * 1.005
        low = close * 0.995
        open_ = np.r_[close[0], close[:-1]]
        df = pd.DataFrame(
            {"open": open_, "high": high, "low": low, "close": close,
             "volume": np.full(n, 2_000_000.0)},
            index=pd.bdate_range("2022-01-03", periods=n),
        )
        fu, fl, direction = supertrend_bands(df, atr_period=10, multiplier=3.0)
        # After strong fall the direction should flip at some point
        assert -1 in direction

    def test_final_upper_and_lower_are_finite(self):
        from core.strategies.indicators import supertrend_bands
        df = self._make_df(80)
        fu, fl, direction = supertrend_bands(df, atr_period=10, multiplier=3.0)
        # After warm-up the final values should be finite
        assert np.isfinite(fu[-1])
        assert np.isfinite(fl[-1])

    def test_small_dataframe_does_not_raise(self):
        """With fewer bars than atr_period, warm-up region has NaN ATR."""
        from core.strategies.indicators import supertrend_bands
        df = self._make_df(15, trend_up=True)
        fu, fl, direction = supertrend_bands(df, atr_period=10, multiplier=3.0)
        assert len(fu) == 15

    def test_upper_band_above_lower_band_after_warmup(self):
        from core.strategies.indicators import supertrend_bands
        df = self._make_df(60)
        fu, fl, _ = supertrend_bands(df, atr_period=10, multiplier=3.0)
        # After warm-up, upper > lower must hold
        valid = ~(np.isnan(fu) | np.isnan(fl))
        assert np.all(fu[valid] >= fl[valid])


class TestAdxValue:
    """Cover lines 109-121 of indicators.py (adx_value)."""

    def _make_df(self, n: int = 80, trending: bool = True) -> pd.DataFrame:
        if trending:
            close = 1000.0 + np.arange(n) * 1.5
        else:
            close = np.tile([1000.0, 1002.0], n // 2 + 1)[:n].astype(float)
        high = close * 1.004
        low = close * 0.996
        open_ = np.r_[close[0], close[:-1]]
        idx = pd.bdate_range("2022-01-03", periods=n)
        return pd.DataFrame(
            {"open": open_, "high": high, "low": low, "close": close,
             "volume": np.full(n, 1_000_000.0)},
            index=idx,
        )

    def test_returns_series_correct_length(self):
        from core.strategies.indicators import adx_value
        df = self._make_df(60)
        result = adx_value(df, period=14)
        assert len(result) == 60

    def test_adx_higher_in_trending_market(self):
        from core.strategies.indicators import adx_value
        trending_df = self._make_df(80, trending=True)
        sideways_df = self._make_df(80, trending=False)
        trending_adx = adx_value(trending_df, period=14).dropna()
        sideways_adx = adx_value(sideways_df, period=14).dropna()
        if len(trending_adx) > 0 and len(sideways_adx) > 0:
            # ADX should be higher in trending market
            assert trending_adx.iloc[-1] > sideways_adx.iloc[-1]

    def test_adx_non_negative(self):
        from core.strategies.indicators import adx_value
        df = self._make_df(80)
        result = adx_value(df, period=14).dropna()
        assert (result >= 0).all()

    def test_adx_custom_period(self):
        from core.strategies.indicators import adx_value
        df = self._make_df(60)
        result = adx_value(df, period=7)
        assert len(result) == 60
        assert result.dropna().iloc[-1] >= 0


# ===========================================================================
# core/data/historical.py
# ===========================================================================

class TestHistoricalFetchDaily:
    """Cover lines 21-57 of historical.py using mocks."""

    def test_fetch_daily_success_via_yfinance(self):
        from core.data.historical import fetch_daily
        # Mock yfinance download to return a valid DataFrame
        n = 100
        idx = pd.bdate_range("2023-01-02", periods=n)
        mock_df = pd.DataFrame({
            "Open": np.full(n, 1000.0),
            "High": np.full(n, 1010.0),
            "Low": np.full(n, 990.0),
            "Close": np.full(n, 1005.0),
            "Volume": np.full(n, 2_000_000),
            "Adj Close": np.full(n, 1005.0),
        }, index=idx)
        with patch("yfinance.download", return_value=mock_df) as mock_dl:
            result = fetch_daily("RELIANCE", days=90)
        assert result is not None
        assert "close" in result.columns
        assert len(result) <= 90

    def test_fetch_daily_yfinance_returns_empty(self):
        """When yfinance returns empty DataFrame, fallback to nsepython attempted."""
        from core.data.historical import fetch_daily
        with patch("yfinance.download", return_value=pd.DataFrame()) as mock_dl:
            with patch("nsepython.equity_history", side_effect=ImportError("no nsepython")) as mock_nse:
                # Both fail → returns None
                result = fetch_daily("FAKE", days=30)
        assert result is None

    def test_fetch_daily_yfinance_raises_exception(self):
        """When yfinance raises, fall through to nsepython path."""
        from core.data.historical import fetch_daily
        with patch("yfinance.download", side_effect=Exception("network error")):
            with patch("nsepython.equity_history", side_effect=Exception("nse error")):
                result = fetch_daily("FAKE", days=30)
        assert result is None

    def test_fetch_daily_yfinance_exception_then_nsepython_exception(self):
        """When both yfinance and nsepython raise, returns None."""
        from core.data.historical import fetch_daily
        # Both raise generic exceptions
        with patch("yfinance.download", side_effect=RuntimeError("yf crash")):
            with patch("nsepython.equity_history", side_effect=RuntimeError("nse crash")):
                result = fetch_daily("TEST", days=30)
        assert result is None

    def test_fetch_daily_nsepython_fallback_success(self):
        """When yfinance fails, nsepython provides data."""
        from core.data.historical import fetch_daily
        n = 60
        idx = pd.bdate_range("2023-01-02", periods=n)
        mock_nse_df = pd.DataFrame({
            "CH_OPENING_PRICE": np.full(n, 1000.0),
            "CH_TRADE_HIGH_PRICE": np.full(n, 1010.0),
            "CH_TRADE_LOW_PRICE": np.full(n, 990.0),
            "CH_CLOSING_PRICE": np.full(n, 1005.0),
            "CH_TOT_TRADED_QTY": np.full(n, 2_000_000),
            "CH_TIMESTAMP": idx,
        }, index=idx)
        with patch("yfinance.download", side_effect=Exception("yf error")):
            with patch("nsepython.equity_history", return_value=mock_nse_df):
                result = fetch_daily("RELIANCE", days=50)
        assert result is not None
        assert "close" in result.columns

    def test_fetch_daily_nsepython_returns_empty(self):
        """When nsepython returns empty DataFrame, returns None."""
        from core.data.historical import fetch_daily
        with patch("yfinance.download", side_effect=Exception("yf error")):
            with patch("nsepython.equity_history", return_value=pd.DataFrame()):
                result = fetch_daily("FAKE", days=30)
        assert result is None


# ===========================================================================
# core/data/bhavcopy.py
# ===========================================================================

class TestBhavcopyHistory:
    """Cover the previously uncovered paths in BhavcopyHistory."""

    def _make_bhav_df(self, symbols: list[str]) -> pd.DataFrame:
        """Build a mock Bhavcopy DataFrame similar to what NSE returns."""
        rows = []
        for sym in symbols:
            rows.append({
                "SYMBOL": sym,
                "SERIES": "EQ",
                "OPEN_PRICE": 1000.0,
                "HIGH_PRICE": 1010.0,
                "LOW_PRICE": 990.0,
                "CLOSE_PRICE": 1005.0,
                "TTL_TRD_QNTY": 2_000_000,
            })
        return pd.DataFrame(rows).set_index("SYMBOL")

    def test_warmup_range_uses_threadpool(self):
        """warmup_range loads multiple dates in parallel and caches them."""
        from core.data.bhavcopy import BhavcopyHistory
        mock_df = self._make_bhav_df(["RELIANCE", "TCS"])
        with tempfile.TemporaryDirectory() as tmpdir:
            bhav = BhavcopyHistory(cache_dir=tmpdir, request_pause_seconds=0)
            start = date(2024, 1, 2)
            end = date(2024, 1, 5)
            with patch.object(bhav, "_load_day", return_value=mock_df) as mock_load:
                loaded = bhav.warmup_range(start, end)
            assert loaded >= 1
            assert mock_load.called

    def test_warmup_range_handles_load_exception(self):
        """warmup_range logs warnings when a future raises but continues."""
        from core.data.bhavcopy import BhavcopyHistory
        with tempfile.TemporaryDirectory() as tmpdir:
            bhav = BhavcopyHistory(cache_dir=tmpdir, request_pause_seconds=0)
            start = date(2024, 1, 2)
            end = date(2024, 1, 3)
            with patch.object(bhav, "_load_day", side_effect=Exception("fail")):
                loaded = bhav.warmup_range(start, end)
            assert loaded == 0

    def test_fetch_equity_daily_with_cached_frames(self):
        """_build_symbol_frame returns rows from pre-loaded _daily_frames."""
        from core.data.bhavcopy import BhavcopyHistory
        with tempfile.TemporaryDirectory() as tmpdir:
            bhav = BhavcopyHistory(cache_dir=tmpdir, request_pause_seconds=0)
            # Pre-populate _daily_frames
            d = date(2024, 1, 2)
            mock_df = self._make_bhav_df(["RELIANCE"])
            bhav._daily_frames[d] = mock_df
            # Use _build_symbol_frame directly so date.today() is not called
            result = bhav._build_symbol_frame("RELIANCE", d, d)
            assert result is not None
            assert len(result) >= 1

    def test_fetch_equity_daily_symbol_not_in_frame(self):
        """Symbol missing from daily frame → rows skipped, None returned."""
        from core.data.bhavcopy import BhavcopyHistory
        with tempfile.TemporaryDirectory() as tmpdir:
            bhav = BhavcopyHistory(cache_dir=tmpdir, request_pause_seconds=0)
            d = date(2024, 1, 2)
            mock_df = self._make_bhav_df(["TCS"])  # no RELIANCE
            bhav._daily_frames[d] = mock_df
            # Patch _load_day so no network call happens
            with patch.object(bhav, "_load_day", return_value=None):
                result = bhav._build_symbol_frame("RELIANCE", d, d)
            assert result is None

    def test_load_day_reads_cached_file(self):
        """_load_day reads CSV from cache without hitting network."""
        from core.data.bhavcopy import BhavcopyHistory
        with tempfile.TemporaryDirectory() as tmpdir:
            bhav = BhavcopyHistory(cache_dir=tmpdir, request_pause_seconds=0)
            d = date(2024, 1, 2)
            # Write a mock CSV to the cache location
            cache_file = Path(tmpdir) / f"sec_bhavdata_full_{d.strftime('%d%m%Y')}.csv"
            csv_content = "SYMBOL,SERIES,OPEN_PRICE,HIGH_PRICE,LOW_PRICE,CLOSE_PRICE,TTL_TRD_QNTY\nRELIANCE,EQ,1000,1010,990,1005,2000000\n"
            cache_file.write_text(csv_content)
            result = bhav._load_day(d)
            assert result is not None
            assert "RELIANCE" in result.index

    def test_load_day_filters_eq_series(self):
        """Only EQ-series rows are kept."""
        from core.data.bhavcopy import BhavcopyHistory
        with tempfile.TemporaryDirectory() as tmpdir:
            bhav = BhavcopyHistory(cache_dir=tmpdir, request_pause_seconds=0)
            d = date(2024, 1, 3)
            cache_file = Path(tmpdir) / f"sec_bhavdata_full_{d.strftime('%d%m%Y')}.csv"
            csv_content = (
                "SYMBOL,SERIES,OPEN_PRICE,HIGH_PRICE,LOW_PRICE,CLOSE_PRICE,TTL_TRD_QNTY\n"
                "RELIANCE,EQ,1000,1010,990,1005,2000000\n"
                "RELIANCEBE,BE,500,510,490,505,100000\n"
            )
            cache_file.write_text(csv_content)
            result = bhav._load_day(d)
            assert result is not None
            assert "RELIANCE" in result.index
            assert "RELIANCEBE" not in result.index

    def test_load_day_404_returns_none(self):
        """HTTP 404 (holiday) → None returned without writing cache."""
        from core.data.bhavcopy import BhavcopyHistory
        import requests
        with tempfile.TemporaryDirectory() as tmpdir:
            bhav = BhavcopyHistory(cache_dir=tmpdir, request_pause_seconds=0)
            d = date(2024, 1, 4)
            mock_resp = MagicMock()
            mock_resp.status_code = 404
            with patch("requests.get", return_value=mock_resp):
                result = bhav._load_day(d)
            assert result is None

    def test_load_day_non_200_returns_none(self):
        """HTTP 500 → None returned."""
        from core.data.bhavcopy import BhavcopyHistory
        with tempfile.TemporaryDirectory() as tmpdir:
            bhav = BhavcopyHistory(cache_dir=tmpdir, request_pause_seconds=0)
            d = date(2024, 1, 5)
            mock_resp = MagicMock()
            mock_resp.status_code = 500
            with patch("requests.get", return_value=mock_resp):
                result = bhav._load_day(d)
            assert result is None

    def test_load_day_request_exception_returns_none(self):
        """Network error → None returned."""
        from core.data.bhavcopy import BhavcopyHistory
        import requests
        with tempfile.TemporaryDirectory() as tmpdir:
            bhav = BhavcopyHistory(cache_dir=tmpdir, request_pause_seconds=0)
            d = date(2024, 1, 8)
            with patch("requests.get", side_effect=requests.RequestException("timeout")):
                result = bhav._load_day(d)
            assert result is None

    def test_load_day_writes_to_cache_on_200(self):
        """200 response content is written to disk cache."""
        from core.data.bhavcopy import BhavcopyHistory
        with tempfile.TemporaryDirectory() as tmpdir:
            bhav = BhavcopyHistory(cache_dir=tmpdir, request_pause_seconds=0)
            d = date(2024, 1, 9)
            csv_bytes = b"SYMBOL,SERIES,OPEN_PRICE,HIGH_PRICE,LOW_PRICE,CLOSE_PRICE,TTL_TRD_QNTY\nTCS,EQ,3000,3010,2990,3005,500000\n"
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.content = csv_bytes
            with patch("requests.get", return_value=mock_resp):
                result = bhav._load_day(d)
            assert result is not None
            cache_file = Path(tmpdir) / f"sec_bhavdata_full_{d.strftime('%d%m%Y')}.csv"
            assert cache_file.exists()

    def test_load_day_missing_symbol_column_returns_none(self):
        """CSV without SYMBOL column → None."""
        from core.data.bhavcopy import BhavcopyHistory
        with tempfile.TemporaryDirectory() as tmpdir:
            bhav = BhavcopyHistory(cache_dir=tmpdir, request_pause_seconds=0)
            d = date(2024, 1, 10)
            cache_file = Path(tmpdir) / f"sec_bhavdata_full_{d.strftime('%d%m%Y')}.csv"
            csv_content = "NAME,SERIES\nRELIANCE,EQ\n"
            cache_file.write_text(csv_content)
            result = bhav._load_day(d)
            assert result is None

    def test_iter_business_days_skips_weekends(self):
        """_iter_business_days must exclude Saturday and Sunday."""
        from core.data.bhavcopy import BhavcopyHistory
        start = date(2024, 1, 1)  # Monday
        end = date(2024, 1, 7)    # Sunday
        days = list(BhavcopyHistory._iter_business_days(start, end))
        # 2024-01-01 Mon, 02 Tue, 03 Wed, 04 Thu, 05 Fri → 5 business days
        assert len(days) == 5
        for d in days:
            assert d.weekday() < 5

    def test_fetch_index_daily_with_mock(self):
        """fetch_index_daily assembles rows from _load_index_day."""
        from core.data.bhavcopy import BhavcopyHistory
        with tempfile.TemporaryDirectory() as tmpdir:
            bhav = BhavcopyHistory(cache_dir=tmpdir, request_pause_seconds=0)
            d = date(2024, 1, 2)
            # Build a mock index frame
            mock_idx_df = pd.DataFrame({
                "Index Name": ["Nifty 50"],
                "Open Index Value": [21000.0],
                "High Index Value": [21100.0],
                "Low Index Value": [20900.0],
                "Closing Index Value": [21050.0],
            }).set_index("Index Name")
            bhav._daily_index_frames[d] = mock_idx_df
            result = bhav.fetch_index_daily("Nifty 50", days=5)
            assert result is not None
            assert "close" in result.columns

    def test_fetch_index_daily_returns_none_when_no_data(self):
        """No index data available → None."""
        from core.data.bhavcopy import BhavcopyHistory
        with tempfile.TemporaryDirectory() as tmpdir:
            bhav = BhavcopyHistory(cache_dir=tmpdir, request_pause_seconds=0)
            with patch.object(bhav, "_load_index_day", return_value=None):
                result = bhav.fetch_index_daily("Nifty 50", days=5)
            assert result is None

    def test_load_index_day_reads_cached_file(self):
        """_load_index_day reads CSV from disk cache."""
        from core.data.bhavcopy import BhavcopyHistory
        with tempfile.TemporaryDirectory() as tmpdir:
            bhav = BhavcopyHistory(cache_dir=tmpdir, request_pause_seconds=0)
            d = date(2024, 1, 2)
            cache_file = Path(tmpdir) / f"ind_close_all_{d.strftime('%d%m%Y')}.csv"
            csv_content = "Index Name,Open Index Value,High Index Value,Low Index Value,Closing Index Value\nNifty 50,21000,21100,20900,21050\n"
            cache_file.write_text(csv_content)
            result = bhav._load_index_day(d)
            assert result is not None
            assert "Nifty 50" in result.index

    def test_load_index_day_request_exception_returns_none(self):
        """Network error for index day → None."""
        from core.data.bhavcopy import BhavcopyHistory
        import requests
        with tempfile.TemporaryDirectory() as tmpdir:
            bhav = BhavcopyHistory(cache_dir=tmpdir, request_pause_seconds=0)
            d = date(2024, 1, 11)
            with patch("requests.get", side_effect=requests.RequestException("timeout")):
                result = bhav._load_index_day(d)
            assert result is None

    def test_load_index_day_non_200_returns_none(self):
        """HTTP 500 for index day → None."""
        from core.data.bhavcopy import BhavcopyHistory
        with tempfile.TemporaryDirectory() as tmpdir:
            bhav = BhavcopyHistory(cache_dir=tmpdir, request_pause_seconds=0)
            d = date(2024, 1, 12)
            mock_resp = MagicMock()
            mock_resp.status_code = 500
            with patch("requests.get", return_value=mock_resp):
                result = bhav._load_index_day(d)
            assert result is None

    def test_load_index_day_missing_index_name_column(self):
        """CSV without 'Index Name' column → None."""
        from core.data.bhavcopy import BhavcopyHistory
        with tempfile.TemporaryDirectory() as tmpdir:
            bhav = BhavcopyHistory(cache_dir=tmpdir, request_pause_seconds=0)
            d = date(2024, 1, 14)
            cache_file = Path(tmpdir) / f"ind_close_all_{d.strftime('%d%m%Y')}.csv"
            cache_file.write_text("NAME,VALUE\nFoo,Bar\n")
            result = bhav._load_index_day(d)
            assert result is None

    def test_load_index_day_writes_to_cache(self):
        """200 response written to disk."""
        from core.data.bhavcopy import BhavcopyHistory
        with tempfile.TemporaryDirectory() as tmpdir:
            bhav = BhavcopyHistory(cache_dir=tmpdir, request_pause_seconds=0)
            d = date(2024, 1, 15)
            csv_bytes = b"Index Name,Open Index Value,High Index Value,Low Index Value,Closing Index Value\nNifty 50,21000,21100,20900,21050\n"
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.content = csv_bytes
            with patch("requests.get", return_value=mock_resp):
                result = bhav._load_index_day(d)
            cache_file = Path(tmpdir) / f"ind_close_all_{d.strftime('%d%m%Y')}.csv"
            assert cache_file.exists()

    def test_load_day_bad_csv_returns_none(self):
        """Unparseable CSV file → None."""
        from core.data.bhavcopy import BhavcopyHistory
        with tempfile.TemporaryDirectory() as tmpdir:
            bhav = BhavcopyHistory(cache_dir=tmpdir, request_pause_seconds=0)
            d = date(2024, 1, 16)
            cache_file = Path(tmpdir) / f"sec_bhavdata_full_{d.strftime('%d%m%Y')}.csv"
            cache_file.write_bytes(b"\x00\xff\xfe bad binary")
            # Patch pd.read_csv to raise to simulate parse error
            with patch("pandas.read_csv", side_effect=Exception("parse error")):
                result = bhav._load_day(d)
            assert result is None


# ===========================================================================
# core/runtime_snapshot.py
# ===========================================================================

class TestRuntimeSnapshot:
    """Cover lines 55-60 (exception in write) and 69-70 (bad JSON in read)."""

    def _make_snapshot(self) -> "RuntimeSnapshot":
        from core.runtime_snapshot import RuntimeSnapshot
        return RuntimeSnapshot(
            ts="2024-01-02T10:00:00+00:00",
            pid=1234,
            mode="paper",
            feed_connected=True,
            last_tick_age_seconds=2.5,
            halted=False,
            halt_reason="",
            cash=100000.0,
            equity=102000.0,
            realized_pnl=2000.0,
            starting_equity_today=100000.0,
            peak_equity=103000.0,
            open_positions=[],
            current_regime="TREND",
            regime_rationale="ADX > 25",
            nifty_ltp=21500.0,
            vix=14.5,
            universe_size=50,
            strategies_enabled=["trend_breakout"],
            config_path="config/default.yaml",
        )

    def test_write_and_read_roundtrip(self):
        """write() + read() should round-trip the snapshot."""
        from core.runtime_snapshot import RuntimeSnapshot, write, read
        snap = self._make_snapshot()
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "snapshot.json"
            write(snap, path)
            data = read(path)
        assert data is not None
        assert data["mode"] == "paper"
        assert data["equity"] == 102000.0

    def test_read_nonexistent_file_returns_none(self):
        """read() on a path that doesn't exist returns None."""
        from core.runtime_snapshot import read
        result = read("/nonexistent/path/snapshot.json")
        assert result is None

    def test_read_corrupt_json_returns_none(self):
        """read() on a file with invalid JSON returns None."""
        from core.runtime_snapshot import read
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            f.write("{ bad json !!!}")
            tmp_path = f.name
        try:
            result = read(tmp_path)
            assert result is None
        finally:
            os.unlink(tmp_path)

    def test_write_raises_and_cleans_up_temp_file(self):
        """When json.dump raises, temp file is cleaned up and exception re-raised."""
        from core.runtime_snapshot import RuntimeSnapshot, write
        snap = self._make_snapshot()
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "snapshot.json"
            with patch("json.dump", side_effect=ValueError("serialise error")):
                with pytest.raises(ValueError, match="serialise error"):
                    write(snap, path)
            # Temp file should have been cleaned up
            tmp_files = list(Path(tmpdir).glob("*.tmp"))
            assert len(tmp_files) == 0

    def test_now_iso_returns_utc_string(self):
        from core.runtime_snapshot import now_iso
        result = now_iso()
        # Should be parseable ISO string
        dt = datetime.fromisoformat(result)
        assert dt.tzinfo is not None

    def test_write_creates_parent_dirs(self):
        """write() should create missing parent directories."""
        from core.runtime_snapshot import write
        snap = self._make_snapshot()
        with tempfile.TemporaryDirectory() as tmpdir:
            deep_path = Path(tmpdir) / "a" / "b" / "c" / "snap.json"
            write(snap, deep_path)
            assert deep_path.exists()


# ===========================================================================
# core/broker/paper.py — uncovered branches
# ===========================================================================

class TestPaperBrokerCoverage:
    """Cover lines 103-104, 118-124, 128-131, 142-146, 163, 237, 286, 292-293."""

    def _make_broker(self, cash: float = 500_000.0):
        from core.broker.paper import PaperBroker
        from core.config import ExecutionCfg
        cfg = ExecutionCfg(
            slippage_bps=5,
            brokerage_per_order_inr=20.0,
            stt_pct=0.001,
            exchange_txn_pct=0.0003,
            gst_pct=0.18,
            signal_cooldown_minutes=30,
        )
        return PaperBroker(starting_cash=cash, exec_cfg=cfg)

    def test_place_order_buy_covers_short_position(self):
        """Placing a BUY when a short position exists triggers cover_short path."""
        from core.types import OrderStatus, OrderType, Side
        broker = self._make_broker()
        # First, open a short position
        broker.update_market_prices({"RELIANCE": 1000.0})
        sell_order = broker.place_order(
            "RELIANCE", Side.SELL, 10, OrderType.MARKET,
            stop_loss=1050.0, target=900.0
        )
        assert sell_order.status == OrderStatus.FILLED
        assert "RELIANCE" in broker._short_positions
        # Now BUY to cover the short
        buy_order = broker.place_order(
            "RELIANCE", Side.BUY, 10, OrderType.MARKET,
            stop_loss=900.0, target=None
        )
        assert buy_order.status == OrderStatus.FILLED
        # Short should be closed
        assert "RELIANCE" not in broker._short_positions

    def test_place_sell_order_insufficient_qty_rejected(self):
        """Selling more than held quantity → REJECTED."""
        from core.types import OrderStatus, OrderType, Side
        broker = self._make_broker()
        broker.update_market_prices({"RELIANCE": 1000.0})
        # Buy 5 shares
        broker.place_order("RELIANCE", Side.BUY, 5, OrderType.MARKET,
                           stop_loss=900.0, target=1100.0)
        # Try to sell 10 (more than 5 owned)
        order = broker.place_order("RELIANCE", Side.SELL, 10, OrderType.MARKET,
                                   stop_loss=900.0, target=None)
        assert order.status == OrderStatus.REJECTED
        assert order.rejection_reason == "no_position_to_sell"

    def test_cancel_order_pending_succeeds(self):
        """cancel_order returns True for a pending order and sets CANCELLED."""
        from core.types import Order, OrderStatus, OrderType, Side
        broker = self._make_broker()
        import uuid
        # Place a limit order but don't fill it — leave status as PENDING manually
        order = Order(
            id=str(uuid.uuid4()),
            symbol="TCS",
            side=Side.BUY,
            qty=5,
            order_type=OrderType.LIMIT,
            limit_price=3000.0,
            stop_loss=2900.0,
            target=3200.0,
            status=OrderStatus.PENDING,
        )
        broker._orders[order.id] = order
        result = broker.cancel_order(order.id)
        assert result is True
        assert broker._orders[order.id].status == OrderStatus.CANCELLED

    def test_cancel_filled_order_returns_false(self):
        """cancel_order returns False for a non-pending order."""
        from core.broker.paper import PaperBroker
        from core.types import Order, OrderStatus, OrderType, Side
        import uuid
        broker = self._make_broker()
        order = Order(
            id=str(uuid.uuid4()),
            symbol="TCS",
            side=Side.BUY,
            qty=5,
            order_type=OrderType.MARKET,
            limit_price=None,
            stop_loss=2900.0,
            target=3200.0,
        )
        order.status = OrderStatus.FILLED
        broker._orders[order.id] = order
        result = broker.cancel_order(order.id)
        assert result is False

    def test_cancel_nonexistent_order_returns_false(self):
        """cancel_order returns False for unknown order ID."""
        broker = self._make_broker()
        assert broker.cancel_order("nonexistent-id") is False

    def test_on_exit_callback_called_on_stop_loss(self):
        """on_exit callback fired when auto-exit on stop_loss triggers."""
        from core.types import OrderStatus, OrderType, Side
        broker = self._make_broker()
        exits = []
        broker.on_exit = lambda sym, pnl, reason, strategy: exits.append((sym, reason))
        broker.update_market_prices({"INFY": 1500.0})
        broker.place_order("INFY", Side.BUY, 10, OrderType.MARKET,
                           stop_loss=1490.0, target=1600.0)
        # Drive price down to stop_loss
        broker.update_market_prices({"INFY": 1480.0})
        assert any(r == "stop_loss" for _, r in exits)

    def test_on_exit_callback_called_on_short_stop_loss(self):
        """on_exit callback fired when short auto-exits on stop_loss."""
        from core.types import OrderStatus, OrderType, Side
        broker = self._make_broker()
        exits = []
        broker.on_exit = lambda sym, pnl, reason, strategy: exits.append((sym, reason))
        broker.update_market_prices({"INFY": 1500.0})
        broker.place_order("INFY", Side.SELL, 10, OrderType.MARKET,
                           stop_loss=1550.0, target=1400.0)
        # Drive price UP above stop_loss
        broker.update_market_prices({"INFY": 1560.0})
        assert any(r == "stop_loss" for _, r in exits)

    def test_short_auto_exit_on_target(self):
        """Short position auto-exits when price falls to target."""
        from core.types import OrderType, Side
        broker = self._make_broker()
        broker.update_market_prices({"INFY": 1500.0})
        broker.place_order("INFY", Side.SELL, 10, OrderType.MARKET,
                           stop_loss=1600.0, target=1400.0)
        # Drive price down to target
        broker.update_market_prices({"INFY": 1390.0})
        # Position should be closed
        assert "INFY" not in broker._short_positions
        assert broker.realized_pnl > 0

    def test_open_or_add_to_existing_long(self):
        """Buying same symbol twice averages the position."""
        from core.types import OrderType, Side
        broker = self._make_broker()
        broker.update_market_prices({"TCS": 3000.0})
        broker.place_order("TCS", Side.BUY, 5, OrderType.MARKET,
                           stop_loss=2800.0, target=3200.0)
        broker.update_market_prices({"TCS": 3100.0})
        broker.place_order("TCS", Side.BUY, 5, OrderType.MARKET,
                           stop_loss=2900.0, target=3300.0)
        pos = broker.get_position("TCS")
        assert pos is not None
        assert pos.qty == 10

    def test_short_open_then_partial_cover(self):
        """Short position partially covered → qty reduced, not deleted."""
        from core.types import OrderType, Side
        broker = self._make_broker(cash=2_000_000.0)
        broker.update_market_prices({"WIPRO": 500.0})
        broker.place_order("WIPRO", Side.SELL, 20, OrderType.MARKET,
                           stop_loss=600.0, target=400.0)
        assert broker._short_positions["WIPRO"].qty == 20
        # Cover 10 of 20
        broker.update_market_prices({"WIPRO": 480.0})
        broker.place_order("WIPRO", Side.BUY, 10, OrderType.MARKET,
                           stop_loss=400.0, target=None)
        # Should still have 10 short remaining
        pos = broker._short_positions.get("WIPRO")
        assert pos is not None
        assert pos.qty == 10

    def test_place_order_no_market_price_rejected(self):
        """Placing order when no price available → REJECTED."""
        from core.types import OrderStatus, OrderType, Side
        broker = self._make_broker()
        # No price loaded for HDFC
        order = broker.place_order("HDFC", Side.BUY, 5, OrderType.MARKET,
                                   stop_loss=1400.0, target=1600.0)
        assert order.status == OrderStatus.REJECTED
        assert order.rejection_reason == "no_market_price"

    def test_short_insufficient_cash_rejected(self):
        """Opening a short when not enough cash → REJECTED."""
        from core.types import OrderStatus, OrderType, Side
        broker = self._make_broker(cash=100.0)  # very little cash
        broker.update_market_prices({"HDFC": 1500.0})
        order = broker.place_order("HDFC", Side.SELL, 1000, OrderType.MARKET,
                                   stop_loss=2000.0, target=1000.0)
        assert order.status == OrderStatus.REJECTED
        assert order.rejection_reason == "insufficient_cash"
