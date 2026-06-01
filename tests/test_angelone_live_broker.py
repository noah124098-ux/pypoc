"""Tests for AngelOneLiveBroker stub.

Verifies:
  - Raises ValueError when ANGEL_ONE_LIVE_API_KEY is empty (startup guard).
  - All IBroker methods raise NotImplementedError (stub contract).
  - from_env() reads ANGEL_ONE_LIVE_* vars, NOT the data-feed vars.
  - Data-feed credentials (ANGEL_ONE_API_KEY) are NOT accepted via from_env().
"""
from __future__ import annotations

import os
import pytest

from core.broker.angelone_live import (
    AngelOneLiveBroker,
    LIVE_API_KEY_ENV,
    LIVE_CLIENT_CODE_ENV,
    LIVE_PASSWORD_ENV,
    LIVE_TOTP_SECRET_ENV,
    _DATA_FEED_API_KEY_ENV,
)
from core.config import ExecutionCfg
from core.types import OrderType, Side


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _exec_cfg() -> ExecutionCfg:
    return ExecutionCfg(
        slippage_bps=5.0,
        brokerage_per_order_inr=20.0,
        stt_pct=0.1,
        exchange_txn_pct=0.00345,
        gst_pct=18.0,
        signal_cooldown_minutes=60,
    )


def _broker_with_key(api_key: str = "FAKE_LIVE_KEY") -> AngelOneLiveBroker:
    return AngelOneLiveBroker(
        api_key=api_key,
        client_code="LIVE_CLIENT",
        password="LIVE_PASS",
        totp_secret="LIVE_TOTP",
        exec_cfg=_exec_cfg(),
    )


# ---------------------------------------------------------------------------
# 1. Startup guard — empty api_key
# ---------------------------------------------------------------------------

def test_init_raises_if_api_key_empty():
    with pytest.raises(ValueError, match=LIVE_API_KEY_ENV):
        AngelOneLiveBroker(
            api_key="",
            client_code="LIVE_CLIENT",
            password="LIVE_PASS",
            totp_secret="LIVE_TOTP",
            exec_cfg=_exec_cfg(),
        )


def test_init_accepts_nonempty_key():
    broker = _broker_with_key("SOME_KEY")
    assert broker is not None


# ---------------------------------------------------------------------------
# 2. All IBroker methods raise NotImplementedError (stub contract)
# ---------------------------------------------------------------------------

def test_place_order_raises_not_implemented():
    broker = _broker_with_key()
    with pytest.raises(NotImplementedError):
        broker.place_order(
            symbol="RELIANCE",
            side=Side.BUY,
            qty=1,
            order_type=OrderType.MARKET,
            stop_loss=2000.0,
            target=2200.0,
        )


def test_cancel_order_raises_not_implemented():
    broker = _broker_with_key()
    with pytest.raises(NotImplementedError):
        broker.cancel_order("fake-order-id")


def test_get_positions_raises_not_implemented():
    broker = _broker_with_key()
    with pytest.raises(NotImplementedError):
        broker.get_positions()


def test_get_position_raises_not_implemented():
    broker = _broker_with_key()
    with pytest.raises(NotImplementedError):
        broker.get_position("RELIANCE")


def test_equity_raises_not_implemented():
    broker = _broker_with_key()
    with pytest.raises(NotImplementedError):
        broker.equity()


def test_cash_raises_not_implemented():
    broker = _broker_with_key()
    with pytest.raises(NotImplementedError):
        broker.cash()


def test_update_market_prices_raises_not_implemented():
    broker = _broker_with_key()
    with pytest.raises(NotImplementedError):
        broker.update_market_prices({"RELIANCE": 2100.0})


def test_connect_raises_not_implemented():
    broker = _broker_with_key()
    with pytest.raises(NotImplementedError):
        broker.connect()


def test_disconnect_raises_not_implemented():
    broker = _broker_with_key()
    with pytest.raises(NotImplementedError):
        broker.disconnect()


# ---------------------------------------------------------------------------
# 3. from_env() reads LIVE vars, not data-feed vars
# ---------------------------------------------------------------------------

def test_from_env_reads_live_vars(monkeypatch):
    """from_env should pick up ANGEL_ONE_LIVE_* vars."""
    monkeypatch.setenv(LIVE_API_KEY_ENV, "live_key_123")
    monkeypatch.setenv(LIVE_CLIENT_CODE_ENV, "live_client_456")
    monkeypatch.setenv(LIVE_PASSWORD_ENV, "live_pass_789")
    monkeypatch.setenv(LIVE_TOTP_SECRET_ENV, "live_totp_abc")
    # Ensure data-feed vars are absent so there's no accidental cross-contamination.
    monkeypatch.delenv(_DATA_FEED_API_KEY_ENV, raising=False)

    broker = AngelOneLiveBroker.from_env(_exec_cfg())
    # Verify the key was consumed (we can check the private attr to confirm correct mapping).
    assert broker._api_key == "live_key_123"
    assert broker._client_code == "live_client_456"


def test_from_env_fails_when_live_key_absent(monkeypatch):
    """from_env should raise ValueError when ANGEL_ONE_LIVE_API_KEY is not set."""
    monkeypatch.delenv(LIVE_API_KEY_ENV, raising=False)
    with pytest.raises(ValueError, match=LIVE_API_KEY_ENV):
        AngelOneLiveBroker.from_env(_exec_cfg())


# ---------------------------------------------------------------------------
# 4. Data-feed credentials are NOT accepted via from_env()
# ---------------------------------------------------------------------------

def test_from_env_does_not_use_data_feed_vars(monkeypatch):
    """Setting only ANGEL_ONE_API_KEY (data-feed) must NOT satisfy the live broker."""
    # Provide the data-feed key but NOT the live key.
    monkeypatch.setenv(_DATA_FEED_API_KEY_ENV, "data_feed_key_xyz")
    monkeypatch.delenv(LIVE_API_KEY_ENV, raising=False)

    # from_env must fail because ANGEL_ONE_LIVE_API_KEY is absent.
    with pytest.raises(ValueError, match=LIVE_API_KEY_ENV):
        AngelOneLiveBroker.from_env(_exec_cfg())


def test_live_env_var_names_differ_from_data_feed():
    """Sanity-check: live env var names are distinct from data-feed var names."""
    assert LIVE_API_KEY_ENV != _DATA_FEED_API_KEY_ENV
    assert LIVE_API_KEY_ENV != "ANGEL_ONE_API_KEY"
    assert LIVE_CLIENT_CODE_ENV != "ANGEL_ONE_CLIENT_CODE"
    assert LIVE_PASSWORD_ENV != "ANGEL_ONE_PASSWORD"
    assert LIVE_TOTP_SECRET_ENV != "ANGEL_ONE_TOTP_SECRET"
