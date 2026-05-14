"""Unit tests for the NSE direct historical client.

HTTP is mocked so tests run offline. Real network behaviour (cookie bootstrap)
is exercised via the live walk-forward command.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from core.data.nse_history import NseHistory


def _equity_payload():
    return {
        "data": [
            {"CH_TIMESTAMP": "2026-01-02", "CH_OPENING_PRICE": 100, "CH_TRADE_HIGH_PRICE": 105,
             "CH_TRADE_LOW_PRICE": 99, "CH_CLOSING_PRICE": 104, "CH_TOT_TRADED_QTY": 1_000_000},
            {"CH_TIMESTAMP": "2026-01-03", "CH_OPENING_PRICE": 104, "CH_TRADE_HIGH_PRICE": 108,
             "CH_TRADE_LOW_PRICE": 103, "CH_CLOSING_PRICE": 107, "CH_TOT_TRADED_QTY": 1_200_000},
        ]
    }


def _index_payload():
    return {
        "data": [
            {"EOD_TIMESTAMP": "2026-01-02", "EOD_OPEN_INDEX_VAL": 22000,
             "EOD_HIGH_INDEX_VAL": 22100, "EOD_LOW_INDEX_VAL": 21950,
             "EOD_CLOSE_INDEX_VAL": 22050},
            {"EOD_TIMESTAMP": "2026-01-03", "EOD_OPEN_INDEX_VAL": 22050,
             "EOD_HIGH_INDEX_VAL": 22200, "EOD_LOW_INDEX_VAL": 22000,
             "EOD_CLOSE_INDEX_VAL": 22150},
        ]
    }


@pytest.fixture
def fake_session(monkeypatch):
    """Replace requests.Session with a mock that returns canned responses based on URL."""
    session = MagicMock()
    session.headers = {}

    def fake_get(url, **kwargs):
        resp = MagicMock()
        resp.status_code = 200
        resp.raise_for_status = MagicMock()
        if "historical/cm/equity" in url:
            resp.json.return_value = _equity_payload()
        elif "historical/indicesHistory" in url:
            resp.json.return_value = _index_payload()
        else:
            resp.json.return_value = {}
        return resp

    session.get.side_effect = fake_get
    monkeypatch.setattr("requests.Session", lambda: session)
    return session


def test_equity_fetch_parses_payload(fake_session):
    h = NseHistory(request_pause_seconds=0)
    df = h.fetch_equity_daily("RELIANCE", days=10)
    assert df is not None
    assert len(df) == 2
    assert list(df.columns) == ["open", "high", "low", "close", "volume"]
    assert df["close"].iloc[-1] == 107.0


def test_equity_returns_none_on_empty(fake_session, monkeypatch):
    def fake_get(url, **kwargs):
        resp = MagicMock()
        resp.status_code = 200
        resp.raise_for_status = MagicMock()
        resp.json.return_value = {"data": []}
        return resp
    fake_session.get.side_effect = fake_get
    h = NseHistory(request_pause_seconds=0)
    assert h.fetch_equity_daily("RELIANCE", days=10) is None


def test_session_is_seeded_via_homepage(fake_session):
    h = NseHistory(request_pause_seconds=0)
    h.fetch_equity_daily("RELIANCE", days=10)
    urls = [call.args[0] for call in fake_session.get.call_args_list]
    assert any("nseindia.com" in u and "/api/" not in u for u in urls), \
        "Should hit homepage to seed cookies"


def test_index_fetch_parses_payload(fake_session):
    h = NseHistory(request_pause_seconds=0)
    df = h.fetch_nifty_index_daily(days=10)
    assert df is not None
    assert len(df) == 2
    assert df["close"].iloc[-1] == 22150.0


def test_failed_request_returns_none(fake_session, monkeypatch):
    import requests

    def fake_get(url, **kwargs):
        if "/api/" in url:
            raise requests.RequestException("simulated network error")
        resp = MagicMock()
        resp.status_code = 200
        resp.raise_for_status = MagicMock()
        return resp
    fake_session.get.side_effect = fake_get
    h = NseHistory(request_pause_seconds=0)
    assert h.fetch_equity_daily("RELIANCE", days=10) is None


def test_unknown_response_shape_returns_none(fake_session):
    def fake_get(url, **kwargs):
        resp = MagicMock()
        resp.status_code = 200
        resp.raise_for_status = MagicMock()
        if "/api/" in url:
            resp.json.return_value = {"data": [{"unexpected": "shape"}]}
        else:
            resp.json.return_value = {}
        return resp
    fake_session.get.side_effect = fake_get
    h = NseHistory(request_pause_seconds=0)
    assert h.fetch_equity_daily("RELIANCE", days=10) is None
