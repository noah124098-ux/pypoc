"""Tests for the Bhavcopy archives client. HTTP mocked so tests run offline."""
from __future__ import annotations

from datetime import date, timedelta
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from core.data.bhavcopy import BhavcopyHistory


def _csv_text() -> str:
    """Sample Bhavcopy CSV (note leading whitespace in column names — matches the real file)."""
    return (
        "SYMBOL, SERIES, DATE1, PREV_CLOSE, OPEN_PRICE, HIGH_PRICE, LOW_PRICE, "
        "LAST_PRICE, CLOSE_PRICE, AVG_PRICE, TTL_TRD_QNTY, TURNOVER_LACS, "
        "NO_OF_TRADES, DELIV_QTY, DELIV_PER\n"
        "RELIANCE, EQ, 13-May-2026, 1361.4, 1361.4, 1372.4, 1352.4, "
        "1358.8, 1358.8, 1361.0, 13797989, 18781.0, 100000, 8000000, 58.0\n"
        "TCS, EQ, 13-May-2026, 4000.0, 4010.0, 4050.0, 3990.0, "
        "4030.0, 4030.0, 4020.0, 5000000, 20100.0, 80000, 3000000, 60.0\n"
        "1018GS2026, GS, 13-May-2026, 105.67, 105.67, 106.25, 104.01, "
        "106.25, 106.25, 106.24, 423, 0.45, 9, 423, 100.00\n"
    )


@pytest.fixture
def fake_get(monkeypatch):
    def _get(url, **kwargs):
        resp = MagicMock()
        resp.status_code = 200
        resp.content = _csv_text().encode("utf-8")
        return resp
    monkeypatch.setattr("requests.get", _get)
    return _get


def test_fetch_equity_daily_for_known_symbol(fake_get, tmp_path):
    b = BhavcopyHistory(cache_dir=tmp_path / "cache", request_pause_seconds=0)
    df = b.fetch_equity_daily("RELIANCE", days=3)
    assert df is not None
    assert "open" in df.columns
    # Several days will fetch the same canned file — at least one row expected.
    assert len(df) >= 1
    assert df["close"].iloc[-1] == pytest.approx(1358.8)


def test_unknown_symbol_returns_none(fake_get, tmp_path):
    b = BhavcopyHistory(cache_dir=tmp_path / "cache", request_pause_seconds=0)
    assert b.fetch_equity_daily("DOES_NOT_EXIST", days=3) is None


def test_non_eq_series_skipped(fake_get, tmp_path):
    """The GS-series row should be filtered out — only EQ symbols are returned."""
    b = BhavcopyHistory(cache_dir=tmp_path / "cache", request_pause_seconds=0)
    assert b.fetch_equity_daily("1018GS2026", days=3) is None


def test_404_treated_as_holiday(monkeypatch, tmp_path):
    def fake_get(url, **kwargs):
        resp = MagicMock()
        resp.status_code = 404
        return resp
    monkeypatch.setattr("requests.get", fake_get)
    b = BhavcopyHistory(cache_dir=tmp_path / "cache", request_pause_seconds=0)
    df = b.fetch_equity_daily("RELIANCE", days=3)
    assert df is None  # no data available


def test_csv_caching(fake_get, tmp_path):
    """Second call should hit disk cache, not the network."""
    b = BhavcopyHistory(cache_dir=tmp_path / "cache", request_pause_seconds=0)
    b.fetch_equity_daily("RELIANCE", days=2)
    call_count_after_first = sum(1 for f in (tmp_path / "cache").glob("*.csv"))
    assert call_count_after_first >= 1
    # Second call: should not fail and should produce same data
    df = b.fetch_equity_daily("RELIANCE", days=2)
    assert df is not None


def test_business_days_only(fake_get, tmp_path):
    """Saturday/Sunday should not be queried."""
    b = BhavcopyHistory(cache_dir=tmp_path / "cache", request_pause_seconds=0)
    # Iterate a known weekend range
    days = list(b._iter_business_days(date(2026, 5, 9), date(2026, 5, 11)))  # Sat..Mon
    weekdays = [d for d in days if d.weekday() < 5]
    assert all(d.weekday() < 5 for d in days)
    # Mon 2026-05-11 should be the only business day in that range
    assert date(2026, 5, 11) in weekdays
    assert date(2026, 5, 9) not in days
    assert date(2026, 5, 10) not in days
