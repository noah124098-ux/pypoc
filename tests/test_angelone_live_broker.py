"""Tests for AngelOneLiveBroker — full implementation with mocked SmartConnect.

Covers:
  1. Startup guard — empty api_key raises ValueError.
  2. Startup guard — data-feed key reuse raises ValueError.
  3. from_env() reads ANGEL_ONE_LIVE_* vars, NOT the data-feed vars.
  4. place_order returns FILLED on SmartAPI success.
  5. place_order returns REJECTED on SmartAPI exception.
  6. place_order returns REJECTED on SmartAPI non-success response.
  7. place_order returns REJECTED when broker not connected.
  8. cancel_order returns True on success, False on exception.
  9. get_positions maps SmartAPI response to Position list.
 10. get_position returns correct Position or None.
 11. equity() / cash() return floats from getRMS.
 12. update_market_prices is a no-op (does not raise).
 13. connect() calls generateSession and sets _connected.
 14. disconnect() calls terminateSession.
 15. _get_token raises ValueError for unknown symbol.
 16. Env-var separation sanity checks.
"""
from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

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
from core.types import OrderStatus, OrderType, Side


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


def _broker(api_key: str = "FAKE_LIVE_KEY") -> AngelOneLiveBroker:
    """Create a broker instance without connecting. Data-feed env var must be absent."""
    return AngelOneLiveBroker(
        api_key=api_key,
        client_code="LIVE_CLIENT",
        password="LIVE_PASS",
        totp_secret="LIVE_TOTP",
        exec_cfg=_exec_cfg(),
    )


def _connected_broker(mock_smart_api: MagicMock) -> AngelOneLiveBroker:
    """Return a broker that is pre-connected with the given mocked SmartConnect."""
    broker = _broker()
    broker._smart_api = mock_smart_api
    broker._connected = True
    # Pre-load instrument master so tests that call _get_token work.
    broker._symbol_to_token = {"RELIANCE": "2885", "INFY": "1594"}
    return broker


# ---------------------------------------------------------------------------
# 1. Startup guards
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


def test_init_accepts_nonempty_key(monkeypatch):
    monkeypatch.delenv(_DATA_FEED_API_KEY_ENV, raising=False)
    broker = _broker("SOME_KEY")
    assert broker is not None


def test_init_rejects_data_feed_key(monkeypatch):
    """Passing the data-feed API key to the live broker must be rejected."""
    monkeypatch.setenv(_DATA_FEED_API_KEY_ENV, "data_feed_key_xyz")
    with pytest.raises(ValueError, match=_DATA_FEED_API_KEY_ENV):
        AngelOneLiveBroker(
            api_key="data_feed_key_xyz",
            client_code="LIVE_CLIENT",
            password="LIVE_PASS",
            totp_secret="LIVE_TOTP",
            exec_cfg=_exec_cfg(),
        )


# ---------------------------------------------------------------------------
# 2. connect() / disconnect()
# ---------------------------------------------------------------------------

def test_connect_calls_generate_session(monkeypatch):
    monkeypatch.delenv(_DATA_FEED_API_KEY_ENV, raising=False)
    broker = _broker()

    mock_sc = MagicMock()
    mock_sc.generateSession.return_value = {"status": True, "data": {"jwtToken": "tok"}}
    mock_sc.getRMS.return_value = {
        "status": True,
        "data": {"availablecash": "500000", "net": "500000"},
    }

    with patch("SmartApi.SmartConnect", return_value=mock_sc), \
         patch("pyotp.TOTP") as mock_totp_cls, \
         patch.object(broker, "_load_instrument_master"), \
         patch.object(broker, "_refresh_balance"):
        mock_totp_cls.return_value.now.return_value = "123456"
        broker.connect()

    mock_sc.generateSession.assert_called_once_with("LIVE_CLIENT", "LIVE_PASS", "123456")
    assert broker._connected is True


def test_connect_raises_on_login_failure(monkeypatch):
    monkeypatch.delenv(_DATA_FEED_API_KEY_ENV, raising=False)
    broker = _broker()

    mock_sc = MagicMock()
    mock_sc.generateSession.return_value = {"status": False, "message": "Invalid credentials"}

    with patch("SmartApi.SmartConnect", return_value=mock_sc), \
         patch("pyotp.TOTP") as mock_totp_cls:
        mock_totp_cls.return_value.now.return_value = "654321"
        with pytest.raises(RuntimeError, match="Invalid credentials"):
            broker.connect()


def test_disconnect_calls_terminate_session(monkeypatch):
    monkeypatch.delenv(_DATA_FEED_API_KEY_ENV, raising=False)
    mock_sc = MagicMock()
    broker = _connected_broker(mock_sc)
    broker.disconnect()
    mock_sc.terminateSession.assert_called_once_with("LIVE_CLIENT")
    assert broker._connected is False
    assert broker._smart_api is None


def test_disconnect_tolerates_terminate_exception(monkeypatch):
    monkeypatch.delenv(_DATA_FEED_API_KEY_ENV, raising=False)
    mock_sc = MagicMock()
    mock_sc.terminateSession.side_effect = Exception("network error")
    broker = _connected_broker(mock_sc)
    broker.disconnect()  # should not raise
    assert broker._connected is False


# ---------------------------------------------------------------------------
# 3. place_order — success path
# ---------------------------------------------------------------------------

def test_place_order_returns_filled_on_success(monkeypatch):
    monkeypatch.delenv(_DATA_FEED_API_KEY_ENV, raising=False)
    mock_sc = MagicMock()
    mock_sc.placeOrder.return_value = {
        "status": True,
        "data": {"orderid": "AO123456"},
    }
    broker = _connected_broker(mock_sc)

    order = broker.place_order(
        symbol="RELIANCE",
        side=Side.BUY,
        qty=10,
        order_type=OrderType.MARKET,
        stop_loss=2000.0,
        target=2200.0,
    )

    assert order.status == OrderStatus.FILLED
    assert order.filled_qty == 10
    assert order.id == "AO123456"
    mock_sc.placeOrder.assert_called_once()
    payload = mock_sc.placeOrder.call_args[0][0]
    assert payload["tradingsymbol"] == "RELIANCE"
    assert payload["symboltoken"] == "2885"
    assert payload["transactiontype"] == "BUY"
    assert payload["quantity"] == "10"
    assert payload["ordertype"] == "MARKET"
    assert payload["producttype"] == "INTRADAY"
    assert payload["exchange"] == "NSE"
    assert payload["variety"] == "NORMAL"


def test_place_order_sell(monkeypatch):
    monkeypatch.delenv(_DATA_FEED_API_KEY_ENV, raising=False)
    mock_sc = MagicMock()
    mock_sc.placeOrder.return_value = {"status": True, "data": {"orderid": "AO_SELL_1"}}
    broker = _connected_broker(mock_sc)

    order = broker.place_order(
        symbol="INFY",
        side=Side.SELL,
        qty=5,
        order_type=OrderType.MARKET,
        stop_loss=1800.0,
        target=None,
    )

    assert order.status == OrderStatus.FILLED
    payload = mock_sc.placeOrder.call_args[0][0]
    assert payload["transactiontype"] == "SELL"
    assert payload["tradingsymbol"] == "INFY"


# ---------------------------------------------------------------------------
# 4. place_order — rejection paths
# ---------------------------------------------------------------------------

def test_place_order_returns_rejected_on_smartapi_exception(monkeypatch):
    monkeypatch.delenv(_DATA_FEED_API_KEY_ENV, raising=False)
    mock_sc = MagicMock()
    mock_sc.placeOrder.side_effect = Exception("SmartAPI network timeout")
    broker = _connected_broker(mock_sc)

    order = broker.place_order(
        symbol="RELIANCE",
        side=Side.BUY,
        qty=5,
        order_type=OrderType.MARKET,
        stop_loss=2000.0,
        target=2200.0,
    )

    assert order.status == OrderStatus.REJECTED
    assert "smartapi_exception" in (order.rejection_reason or "")


def test_place_order_returns_rejected_on_smartapi_non_success(monkeypatch):
    monkeypatch.delenv(_DATA_FEED_API_KEY_ENV, raising=False)
    mock_sc = MagicMock()
    mock_sc.placeOrder.return_value = {"status": False, "message": "Insufficient margin"}
    broker = _connected_broker(mock_sc)

    order = broker.place_order(
        symbol="RELIANCE",
        side=Side.BUY,
        qty=100,
        order_type=OrderType.MARKET,
        stop_loss=2000.0,
        target=None,
    )

    assert order.status == OrderStatus.REJECTED
    assert "Insufficient margin" in (order.rejection_reason or "")


def test_place_order_returns_rejected_when_not_connected(monkeypatch):
    monkeypatch.delenv(_DATA_FEED_API_KEY_ENV, raising=False)
    broker = _broker()
    # Broker is not connected (default state).

    order = broker.place_order(
        symbol="RELIANCE",
        side=Side.BUY,
        qty=1,
        order_type=OrderType.MARKET,
        stop_loss=2000.0,
        target=None,
    )

    assert order.status == OrderStatus.REJECTED
    assert "not_connected" in (order.rejection_reason or "")


def test_place_order_returns_rejected_for_unknown_symbol(monkeypatch):
    monkeypatch.delenv(_DATA_FEED_API_KEY_ENV, raising=False)
    mock_sc = MagicMock()
    broker = _connected_broker(mock_sc)

    order = broker.place_order(
        symbol="NOSUCHSYM",
        side=Side.BUY,
        qty=1,
        order_type=OrderType.MARKET,
        stop_loss=10.0,
        target=None,
    )

    assert order.status == OrderStatus.REJECTED
    assert "NOSUCHSYM" in (order.rejection_reason or "")


# ---------------------------------------------------------------------------
# 5. cancel_order
# ---------------------------------------------------------------------------

def test_cancel_order_returns_true_on_success(monkeypatch):
    monkeypatch.delenv(_DATA_FEED_API_KEY_ENV, raising=False)
    mock_sc = MagicMock()
    mock_sc.cancelOrder.return_value = {"status": True}
    broker = _connected_broker(mock_sc)

    result = broker.cancel_order("AO123456")
    assert result is True
    mock_sc.cancelOrder.assert_called_once_with("AO123456", "NORMAL")


def test_cancel_order_returns_false_on_exception(monkeypatch):
    monkeypatch.delenv(_DATA_FEED_API_KEY_ENV, raising=False)
    mock_sc = MagicMock()
    mock_sc.cancelOrder.side_effect = Exception("network error")
    broker = _connected_broker(mock_sc)

    result = broker.cancel_order("BADID")
    assert result is False


def test_cancel_order_returns_false_when_not_connected(monkeypatch):
    monkeypatch.delenv(_DATA_FEED_API_KEY_ENV, raising=False)
    broker = _broker()
    result = broker.cancel_order("AO123456")
    assert result is False


# ---------------------------------------------------------------------------
# 6. get_positions
# ---------------------------------------------------------------------------

def test_get_positions_maps_response(monkeypatch):
    monkeypatch.delenv(_DATA_FEED_API_KEY_ENV, raising=False)
    mock_sc = MagicMock()
    mock_sc.getPosition.return_value = {
        "status": True,
        "data": [
            {
                "tradingsymbol": "RELIANCE-EQ",
                "netqty": "10",
                "netavgprice": "2100.50",
                "ltp": "2120.00",
            },
            {
                "tradingsymbol": "INFY-EQ",
                "netqty": "0",
                "netavgprice": "1500.00",
                "ltp": "1510.00",
            },
        ],
    }
    broker = _connected_broker(mock_sc)

    positions = broker.get_positions()
    assert len(positions) == 1
    pos = positions[0]
    assert pos.symbol == "RELIANCE"
    assert pos.qty == 10
    assert pos.avg_price == pytest.approx(2100.50)
    assert pos.last_price == pytest.approx(2120.00)


def test_get_positions_returns_empty_on_exception(monkeypatch):
    monkeypatch.delenv(_DATA_FEED_API_KEY_ENV, raising=False)
    mock_sc = MagicMock()
    mock_sc.getPosition.side_effect = Exception("SmartAPI error")
    broker = _connected_broker(mock_sc)

    positions = broker.get_positions()
    assert positions == []


def test_get_positions_returns_empty_when_not_connected(monkeypatch):
    monkeypatch.delenv(_DATA_FEED_API_KEY_ENV, raising=False)
    broker = _broker()
    assert broker.get_positions() == []


def test_get_position_returns_none_for_missing(monkeypatch):
    monkeypatch.delenv(_DATA_FEED_API_KEY_ENV, raising=False)
    mock_sc = MagicMock()
    mock_sc.getPosition.return_value = {"status": True, "data": []}
    broker = _connected_broker(mock_sc)

    pos = broker.get_position("RELIANCE")
    assert pos is None


# ---------------------------------------------------------------------------
# 7. equity() / cash() from getRMS
# ---------------------------------------------------------------------------

def test_equity_returns_float_from_rms(monkeypatch):
    monkeypatch.delenv(_DATA_FEED_API_KEY_ENV, raising=False)
    mock_sc = MagicMock()
    mock_sc.getRMS.return_value = {
        "status": True,
        "data": {"availablecash": "450000.50", "net": "500000.75"},
    }
    broker = _connected_broker(mock_sc)

    eq = broker.equity()
    assert isinstance(eq, float)
    assert eq == pytest.approx(500000.75)


def test_cash_returns_float_from_rms(monkeypatch):
    monkeypatch.delenv(_DATA_FEED_API_KEY_ENV, raising=False)
    mock_sc = MagicMock()
    mock_sc.getRMS.return_value = {
        "status": True,
        "data": {"availablecash": "450000.50", "net": "500000.75"},
    }
    broker = _connected_broker(mock_sc)

    c = broker.cash()
    assert isinstance(c, float)
    assert c == pytest.approx(450000.50)


def test_equity_returns_cached_on_rms_exception(monkeypatch):
    monkeypatch.delenv(_DATA_FEED_API_KEY_ENV, raising=False)
    mock_sc = MagicMock()
    mock_sc.getRMS.side_effect = Exception("network error")
    broker = _connected_broker(mock_sc)
    broker._equity = 123456.0

    eq = broker.equity()
    assert eq == pytest.approx(123456.0)


# ---------------------------------------------------------------------------
# 8. update_market_prices is a no-op
# ---------------------------------------------------------------------------

def test_update_market_prices_does_not_raise(monkeypatch):
    monkeypatch.delenv(_DATA_FEED_API_KEY_ENV, raising=False)
    broker = _broker()
    broker.update_market_prices({"RELIANCE": 2100.0, "INFY": 1500.0})  # should not raise


# ---------------------------------------------------------------------------
# 9. _get_token
# ---------------------------------------------------------------------------

def test_get_token_raises_for_unknown_symbol(monkeypatch):
    monkeypatch.delenv(_DATA_FEED_API_KEY_ENV, raising=False)
    broker = _broker()
    broker._symbol_to_token = {"RELIANCE": "2885"}

    with pytest.raises(ValueError, match="NOSUCHSYM"):
        broker._get_token("NOSUCHSYM")


def test_get_token_returns_correct_token(monkeypatch):
    monkeypatch.delenv(_DATA_FEED_API_KEY_ENV, raising=False)
    broker = _broker()
    broker._symbol_to_token = {"RELIANCE": "2885", "INFY": "1594"}

    assert broker._get_token("RELIANCE") == "2885"
    assert broker._get_token("INFY") == "1594"


# ---------------------------------------------------------------------------
# 10. from_env() reads LIVE vars, not data-feed vars
# ---------------------------------------------------------------------------

def test_from_env_reads_live_vars(monkeypatch):
    monkeypatch.setenv(LIVE_API_KEY_ENV, "live_key_123")
    monkeypatch.setenv(LIVE_CLIENT_CODE_ENV, "live_client_456")
    monkeypatch.setenv(LIVE_PASSWORD_ENV, "live_pass_789")
    monkeypatch.setenv(LIVE_TOTP_SECRET_ENV, "live_totp_abc")
    monkeypatch.delenv(_DATA_FEED_API_KEY_ENV, raising=False)

    broker = AngelOneLiveBroker.from_env(_exec_cfg())
    assert broker._api_key == "live_key_123"
    assert broker._client_code == "live_client_456"


def test_from_env_fails_when_live_key_absent(monkeypatch):
    monkeypatch.delenv(LIVE_API_KEY_ENV, raising=False)
    with pytest.raises(ValueError, match=LIVE_API_KEY_ENV):
        AngelOneLiveBroker.from_env(_exec_cfg())


def test_from_env_does_not_use_data_feed_vars(monkeypatch):
    """Setting only ANGEL_ONE_API_KEY (data-feed) must NOT satisfy the live broker."""
    monkeypatch.setenv(_DATA_FEED_API_KEY_ENV, "data_feed_key_xyz")
    monkeypatch.delenv(LIVE_API_KEY_ENV, raising=False)

    with pytest.raises(ValueError, match=LIVE_API_KEY_ENV):
        AngelOneLiveBroker.from_env(_exec_cfg())


def test_live_env_var_names_differ_from_data_feed():
    """Sanity-check: live env var names are distinct from data-feed var names."""
    assert LIVE_API_KEY_ENV != _DATA_FEED_API_KEY_ENV
    assert LIVE_API_KEY_ENV != "ANGEL_ONE_API_KEY"
    assert LIVE_CLIENT_CODE_ENV != "ANGEL_ONE_CLIENT_CODE"
    assert LIVE_PASSWORD_ENV != "ANGEL_ONE_PASSWORD"
    assert LIVE_TOTP_SECRET_ENV != "ANGEL_ONE_TOTP_SECRET"
