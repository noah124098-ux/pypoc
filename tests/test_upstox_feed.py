"""Tests for UpstoxFeed stub.

These tests verify the interface scaffold without requiring any Upstox credentials
or network access. The stub always raises NotImplementedError on network operations.
"""
from __future__ import annotations

import pytest

from core.data.upstox_feed import UpstoxFeed


# ---------------------------------------------------------------------------
# Instantiation
# ---------------------------------------------------------------------------

def test_missing_api_key_raises():
    """Constructor must reject empty api_key."""
    with pytest.raises(ValueError, match="Upstox credentials missing"):
        UpstoxFeed(api_key="", access_token="some-token")


def test_missing_access_token_raises():
    """Constructor must reject empty access_token."""
    with pytest.raises(ValueError, match="Upstox credentials missing"):
        UpstoxFeed(api_key="some-key", access_token="")


def test_both_credentials_missing_raises():
    """Constructor must reject both empty."""
    with pytest.raises(ValueError, match="Upstox credentials missing"):
        UpstoxFeed(api_key="", access_token="")


def test_valid_credentials_instantiates():
    """Constructor succeeds when both credentials are provided."""
    feed = UpstoxFeed(api_key="test-key", access_token="test-token")
    assert feed is not None


# ---------------------------------------------------------------------------
# ILiveFeed interface — initial state
# ---------------------------------------------------------------------------

def test_is_connected_initially_false():
    feed = UpstoxFeed(api_key="k", access_token="t")
    assert feed.is_connected() is False


def test_last_tick_age_initially_infinite():
    feed = UpstoxFeed(api_key="k", access_token="t")
    assert feed.last_tick_age_seconds() == float("inf")


def test_on_tick_registers_callback():
    """on_tick() should accept and store a callback without raising."""
    feed = UpstoxFeed(api_key="k", access_token="t")
    called = []
    feed.on_tick(lambda tick: called.append(tick))
    # Callback registered — no error expected.
    assert len(feed._tick_callbacks) == 1


# ---------------------------------------------------------------------------
# Stub barriers — connect and subscribe must raise NotImplementedError
# ---------------------------------------------------------------------------

def test_connect_raises_not_implemented():
    """connect() must raise NotImplementedError with a descriptive message."""
    feed = UpstoxFeed(api_key="k", access_token="t")
    with pytest.raises(NotImplementedError) as exc_info:
        feed.connect()
    assert "UpstoxFeed.connect()" in str(exc_info.value)
    assert "not yet implemented" in str(exc_info.value).lower()


def test_subscribe_raises_not_implemented():
    """subscribe() must raise NotImplementedError with a descriptive message."""
    feed = UpstoxFeed(api_key="k", access_token="t")
    with pytest.raises(NotImplementedError) as exc_info:
        feed.subscribe(["RELIANCE", "INFY"])
    assert "UpstoxFeed.subscribe()" in str(exc_info.value)
    assert "not yet implemented" in str(exc_info.value).lower()


def test_disconnect_does_not_raise_when_not_connected():
    """disconnect() on an unconnected feed should be a no-op (no crash)."""
    feed = UpstoxFeed(api_key="k", access_token="t")
    feed.disconnect()  # Must not raise.
    assert feed.is_connected() is False


# ---------------------------------------------------------------------------
# from_env() classmethod
# ---------------------------------------------------------------------------

def test_from_env_reads_correct_env_vars(monkeypatch):
    """from_env() must read UPSTOX_API_KEY and UPSTOX_ACCESS_TOKEN."""
    monkeypatch.setenv("UPSTOX_API_KEY", "env-api-key")
    monkeypatch.setenv("UPSTOX_ACCESS_TOKEN", "env-access-token")
    feed = UpstoxFeed.from_env()
    assert feed._api_key == "env-api-key"
    assert feed._access_token == "env-access-token"


def test_from_env_missing_vars_raises(monkeypatch):
    """from_env() must raise ValueError when env vars are absent."""
    monkeypatch.delenv("UPSTOX_API_KEY", raising=False)
    monkeypatch.delenv("UPSTOX_ACCESS_TOKEN", raising=False)
    with pytest.raises(ValueError, match="Upstox credentials missing"):
        UpstoxFeed.from_env()


def test_from_env_partial_vars_raises(monkeypatch):
    """from_env() must raise ValueError when only one var is set."""
    monkeypatch.setenv("UPSTOX_API_KEY", "env-api-key")
    monkeypatch.delenv("UPSTOX_ACCESS_TOKEN", raising=False)
    with pytest.raises(ValueError, match="Upstox credentials missing"):
        UpstoxFeed.from_env()


# ---------------------------------------------------------------------------
# Config integration — Secrets model
# ---------------------------------------------------------------------------

def test_secrets_model_has_upstox_fields():
    """Secrets must expose upstox_api_key and upstox_access_token fields."""
    from core.config import Secrets
    s = Secrets()
    assert hasattr(s, "upstox_api_key")
    assert hasattr(s, "upstox_access_token")
    assert s.upstox_api_key == ""
    assert s.upstox_access_token == ""


def test_secrets_from_env_reads_upstox_vars(monkeypatch):
    """Secrets.from_env() must populate upstox fields from environment."""
    monkeypatch.setenv("UPSTOX_API_KEY", "cfg-key")
    monkeypatch.setenv("UPSTOX_ACCESS_TOKEN", "cfg-token")
    from core.config import Secrets
    s = Secrets.from_env()
    assert s.upstox_api_key == "cfg-key"
    assert s.upstox_access_token == "cfg-token"
