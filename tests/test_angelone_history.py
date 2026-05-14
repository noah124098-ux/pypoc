"""Unit tests for the Angel One historical client.

These tests stub the SmartConnect SDK so they run offline (no network, no credentials).
A live integration test (the actual login + fetch) is gated on creds being present.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from core.data.angelone_history import AngelOneHistory


@pytest.fixture
def fake_smart_api(monkeypatch):
    """Replace SmartConnect with a mock that returns canned data."""
    mock_session = MagicMock()
    mock_session.generateSession.return_value = {"status": True, "data": {"jwtToken": "x"}}
    mock_session.getCandleData.return_value = {
        "status": True,
        "data": [
            ["2026-01-02T00:00:00+05:30", 100.0, 105.0, 99.0, 104.0, 1_000_000],
            ["2026-01-03T00:00:00+05:30", 104.0, 108.0, 103.0, 107.0, 1_200_000],
            ["2026-01-04T00:00:00+05:30", 107.0, 110.0, 106.0, 109.0, 1_100_000],
        ],
    }
    smart_constructor = MagicMock(return_value=mock_session)

    # Patch where AngelOneHistory imports it (lazy inside _ensure_login)
    monkeypatch.setattr("SmartApi.SmartConnect", smart_constructor, raising=False)
    # Also make pyotp.TOTP(...).now() deterministic
    monkeypatch.setattr("pyotp.TOTP", lambda secret: MagicMock(now=lambda: "123456"))
    return mock_session


def _client(monkeypatch, tmp_path):
    h = AngelOneHistory(
        api_key="k", client_code="c", password="p", totp_secret="JBSWY3DPEHPK3PXP",
        cache_dir=tmp_path / "cache",
    )
    # Pre-seed the symbol->token map so we don't need to mock the instrument master download.
    h._symbol_to_token = {"RELIANCE": "2885", "Nifty 50": "99926000"}
    return h


def test_missing_credentials_raises():
    with pytest.raises(ValueError, match="credentials missing"):
        AngelOneHistory(api_key="", client_code="", password="", totp_secret="")


def test_fetch_daily_parses_payload(fake_smart_api, monkeypatch, tmp_path):
    h = _client(monkeypatch, tmp_path)
    df = h.fetch_daily("RELIANCE", days=10)
    assert df is not None
    assert len(df) == 3
    assert list(df.columns) == ["open", "high", "low", "close", "volume"]
    assert df["close"].iloc[-1] == 109.0
    assert pd.api.types.is_datetime64_any_dtype(df.index)


def test_unknown_symbol_returns_none(fake_smart_api, monkeypatch, tmp_path):
    h = _client(monkeypatch, tmp_path)
    h._symbol_to_token = {}  # no symbols resolved
    df = h.fetch_daily("UNKNOWN", days=10)
    assert df is None


def test_invalid_interval_raises(fake_smart_api, monkeypatch, tmp_path):
    h = _client(monkeypatch, tmp_path)
    with pytest.raises(ValueError, match="Unsupported interval"):
        h.fetch_candles("RELIANCE", interval="2m", days=5)


def test_failed_response_returns_none(fake_smart_api, monkeypatch, tmp_path):
    h = _client(monkeypatch, tmp_path)
    fake_smart_api.getCandleData.return_value = {"status": False, "message": "rate limit"}
    df = h.fetch_daily("RELIANCE", days=10)
    assert df is None


def test_empty_data_returns_none(fake_smart_api, monkeypatch, tmp_path):
    h = _client(monkeypatch, tmp_path)
    fake_smart_api.getCandleData.return_value = {"status": True, "data": []}
    df = h.fetch_daily("RELIANCE", days=10)
    assert df is None


def test_session_only_created_once(fake_smart_api, monkeypatch, tmp_path):
    h = _client(monkeypatch, tmp_path)
    h.fetch_daily("RELIANCE", days=10)
    h.fetch_daily("RELIANCE", days=10)
    # generateSession should only fire once across two fetches
    assert fake_smart_api.generateSession.call_count == 1


def test_order_methods_are_neutralized_after_login(fake_smart_api, monkeypatch, tmp_path):
    """DATA-ONLY contract: SDK order methods must raise after login.

    This test is the safety guarantee that even if a future refactor accidentally
    wires the Angel One client to order placement code, the call will fail loudly
    rather than transmit a real order.
    """
    fake_smart_api.placeOrder = MagicMock(return_value={"status": True, "orderid": "X"})
    h = _client(monkeypatch, tmp_path)
    h.fetch_daily("RELIANCE", days=10)  # triggers login, which neutralizes order methods

    with pytest.raises(RuntimeError, match="DATA-ONLY"):
        fake_smart_api.placeOrder({"symbol": "RELIANCE", "qty": 1})
