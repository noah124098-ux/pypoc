"""Tests for UpstoxFeed — Upstox V3 WebSocket live data feed.

Tests cover:
- Construction validation
- ILiveFeed interface initial state
- from_env() classmethod
- Instrument key resolution
- Protobuf frame parsing helpers
- Tick callback firing
- HTTP polling fallback
- WebSocket subscription flow (mocked)
- Config/Secrets integration
"""
from __future__ import annotations

import gzip
import json
import struct
import threading
import time
from datetime import datetime
from io import BytesIO
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from core.data.upstox_feed import (
    UpstoxFeed,
    _parse_ltpc,
    _parse_bid_ask,
    _parse_feed,
    _parse_upstox_frame,
    _read_varint,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_feed(**kwargs) -> UpstoxFeed:
    return UpstoxFeed(api_key=kwargs.pop("api_key", "test-key"),
                      access_token=kwargs.pop("access_token", "test-token"),
                      **kwargs)


def _encode_varint(value: int) -> bytes:
    """Encode a positive integer as a protobuf varint."""
    result = []
    while value > 0x7F:
        result.append((value & 0x7F) | 0x80)
        value >>= 7
    result.append(value & 0x7F)
    return bytes(result)


def _encode_field_float(field_number: int, value: float) -> bytes:
    """Encode a protobuf 32-bit float field."""
    tag = (field_number << 3) | 5  # wire type 5 = 32-bit
    return _encode_varint(tag) + struct.pack("<f", value)


def _encode_field_bytes(field_number: int, data: bytes) -> bytes:
    """Encode a protobuf length-delimited field."""
    tag = (field_number << 3) | 2  # wire type 2 = length-delimited
    return _encode_varint(tag) + _encode_varint(len(data)) + data


def _encode_field_string(field_number: int, value: str) -> bytes:
    return _encode_field_bytes(field_number, value.encode("utf-8"))


# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------

class TestConstruction:
    def test_missing_api_key_raises(self):
        with pytest.raises(ValueError, match="Upstox credentials missing"):
            UpstoxFeed(api_key="", access_token="some-token")

    def test_missing_access_token_raises(self):
        with pytest.raises(ValueError, match="Upstox credentials missing"):
            UpstoxFeed(api_key="some-key", access_token="")

    def test_both_credentials_missing_raises(self):
        with pytest.raises(ValueError, match="Upstox credentials missing"):
            UpstoxFeed(api_key="", access_token="")

    def test_valid_credentials_instantiates(self):
        feed = _make_feed()
        assert feed is not None

    def test_default_reconnect_params(self):
        feed = _make_feed()
        assert feed._reconnect_max == 5
        assert feed._reconnect_backoff == 2

    def test_custom_reconnect_params(self):
        feed = _make_feed(reconnect_max_attempts=3, reconnect_backoff_seconds=1)
        assert feed._reconnect_max == 3
        assert feed._reconnect_backoff == 1

    def test_cache_dir_created(self, tmp_path):
        cache = tmp_path / "upstox_cache"
        feed = _make_feed(cache_dir=str(cache))
        assert cache.exists()

    def test_stores_credentials(self):
        feed = _make_feed(api_key="my-key", access_token="my-token")
        assert feed._api_key == "my-key"
        assert feed._access_token == "my-token"


# ---------------------------------------------------------------------------
# ILiveFeed interface — initial state
# ---------------------------------------------------------------------------

class TestInitialState:
    def test_is_connected_initially_false(self):
        feed = _make_feed()
        assert feed.is_connected() is False

    def test_last_tick_age_initially_infinite(self):
        feed = _make_feed()
        assert feed.last_tick_age_seconds() == float("inf")

    def test_on_tick_registers_callback(self):
        feed = _make_feed()
        called = []
        feed.on_tick(lambda tick: called.append(tick))
        assert len(feed._tick_callbacks) == 1

    def test_multiple_callbacks_registered(self):
        feed = _make_feed()
        feed.on_tick(lambda t: None)
        feed.on_tick(lambda t: None)
        assert len(feed._tick_callbacks) == 2

    def test_subscribed_symbols_initially_empty(self):
        feed = _make_feed()
        assert feed._subscribed_symbols == []

    def test_subscribed_keys_initially_empty(self):
        feed = _make_feed()
        assert feed._subscribed_keys == []


# ---------------------------------------------------------------------------
# last_tick_age_seconds
# ---------------------------------------------------------------------------

class TestLastTickAge:
    def test_returns_elapsed_time_after_tick(self):
        feed = _make_feed()
        feed._last_tick_ts = time.time() - 3.0
        age = feed.last_tick_age_seconds()
        assert 2.5 < age < 4.0

    def test_returns_inf_when_no_tick(self):
        feed = _make_feed()
        feed._last_tick_ts = 0.0
        assert feed.last_tick_age_seconds() == float("inf")


# ---------------------------------------------------------------------------
# disconnect — no-op when not connected
# ---------------------------------------------------------------------------

class TestDisconnect:
    def test_disconnect_does_not_raise_when_not_connected(self):
        feed = _make_feed()
        feed.disconnect()
        assert feed.is_connected() is False

    def test_disconnect_sets_connected_false(self):
        feed = _make_feed()
        feed._connected = True
        feed.disconnect()
        assert feed.is_connected() is False

    def test_disconnect_calls_ws_close(self):
        feed = _make_feed()
        mock_ws = MagicMock()
        feed._ws = mock_ws
        feed.disconnect()
        mock_ws.close.assert_called_once()


# ---------------------------------------------------------------------------
# from_env() classmethod
# ---------------------------------------------------------------------------

class TestFromEnv:
    def test_reads_correct_env_vars(self, monkeypatch):
        monkeypatch.setenv("UPSTOX_API_KEY", "env-api-key")
        monkeypatch.setenv("UPSTOX_ACCESS_TOKEN", "env-access-token")
        feed = UpstoxFeed.from_env()
        assert feed._api_key == "env-api-key"
        assert feed._access_token == "env-access-token"

    def test_missing_vars_raises(self, monkeypatch):
        monkeypatch.delenv("UPSTOX_API_KEY", raising=False)
        monkeypatch.delenv("UPSTOX_ACCESS_TOKEN", raising=False)
        with pytest.raises(ValueError, match="Upstox credentials missing"):
            UpstoxFeed.from_env()

    def test_partial_vars_raises(self, monkeypatch):
        monkeypatch.setenv("UPSTOX_API_KEY", "env-api-key")
        monkeypatch.delenv("UPSTOX_ACCESS_TOKEN", raising=False)
        with pytest.raises(ValueError, match="Upstox credentials missing"):
            UpstoxFeed.from_env()


# ---------------------------------------------------------------------------
# Config/Secrets integration
# ---------------------------------------------------------------------------

class TestSecretsModel:
    def test_secrets_has_upstox_fields(self):
        from core.config import Secrets
        s = Secrets()
        assert hasattr(s, "upstox_api_key")
        assert hasattr(s, "upstox_access_token")
        assert s.upstox_api_key == ""
        assert s.upstox_access_token == ""

    def test_secrets_from_env_reads_upstox_vars(self, monkeypatch):
        monkeypatch.setenv("UPSTOX_API_KEY", "cfg-key")
        monkeypatch.setenv("UPSTOX_ACCESS_TOKEN", "cfg-token")
        from core.config import Secrets
        s = Secrets.from_env()
        assert s.upstox_api_key == "cfg-key"
        assert s.upstox_access_token == "cfg-token"


# ---------------------------------------------------------------------------
# Instrument master loading
# ---------------------------------------------------------------------------

SAMPLE_INSTRUMENTS = [
    {"instrument_key": "NSE_EQ|INE002A01018", "trading_symbol": "RELIANCE"},
    {"instrument_key": "NSE_EQ|INE009A01021", "trading_symbol": "INFY"},
    {"instrument_key": "NSE_EQ|INE467B01029", "trading_symbol": "TCS"},
    {"instrument_key": "BSE_EQ|INE002A01018", "trading_symbol": "RELIANCE"},  # should be skipped
]


def _make_instruments_gz(instruments: list) -> bytes:
    buf = BytesIO()
    with gzip.GzipFile(fileobj=buf, mode="wb") as gz:
        gz.write(json.dumps(instruments).encode("utf-8"))
    return buf.getvalue()


class TestInstrumentMaster:
    def test_load_instrument_master_builds_maps(self, tmp_path):
        feed = _make_feed(cache_dir=str(tmp_path))
        gz_data = _make_instruments_gz(SAMPLE_INSTRUMENTS)

        with patch("requests.get") as mock_get:
            mock_resp = MagicMock()
            mock_resp.content = gz_data
            mock_resp.raise_for_status = MagicMock()
            mock_get.return_value = mock_resp

            feed._load_instrument_master()

        assert feed._symbol_to_key.get("RELIANCE") == "NSE_EQ|INE002A01018"
        assert feed._symbol_to_key.get("INFY") == "NSE_EQ|INE009A01021"
        assert feed._key_to_symbol.get("NSE_EQ|INE009A01021") == "INFY"

    def test_bse_instruments_skipped(self, tmp_path):
        feed = _make_feed(cache_dir=str(tmp_path))
        gz_data = _make_instruments_gz(SAMPLE_INSTRUMENTS)

        with patch("requests.get") as mock_get:
            mock_resp = MagicMock()
            mock_resp.content = gz_data
            mock_resp.raise_for_status = MagicMock()
            mock_get.return_value = mock_resp

            feed._load_instrument_master()

        # BSE instrument should not add a second RELIANCE entry overshadowing NSE
        assert feed._symbol_to_key.get("RELIANCE") == "NSE_EQ|INE002A01018"

    def test_uses_cached_file_when_fresh(self, tmp_path):
        feed = _make_feed(cache_dir=str(tmp_path))
        cache_file = tmp_path / "upstox_instruments.json"
        cache_file.write_text(
            json.dumps(SAMPLE_INSTRUMENTS[:2]),
            encoding="utf-8",
        )

        with patch("requests.get") as mock_get:
            feed._load_instrument_master()
            mock_get.assert_not_called()

        assert "RELIANCE" in feed._symbol_to_key

    def test_refreshes_stale_cache(self, tmp_path):
        import os
        feed = _make_feed(cache_dir=str(tmp_path))
        cache_file = tmp_path / "upstox_instruments.json"
        cache_file.write_text(json.dumps([]), encoding="utf-8")
        # Backdate the cache file by 25 hours
        old_mtime = time.time() - 25 * 3600
        os.utime(str(cache_file), (old_mtime, old_mtime))

        gz_data = _make_instruments_gz(SAMPLE_INSTRUMENTS)
        with patch("requests.get") as mock_get:
            mock_resp = MagicMock()
            mock_resp.content = gz_data
            mock_resp.raise_for_status = MagicMock()
            mock_get.return_value = mock_resp

            feed._load_instrument_master()
            mock_get.assert_called_once()

    def test_get_instrument_key_returns_correct_key(self, tmp_path):
        feed = _make_feed(cache_dir=str(tmp_path))
        feed._symbol_to_key = {"RELIANCE": "NSE_EQ|INE002A01018"}
        assert feed._get_instrument_key("RELIANCE") == "NSE_EQ|INE002A01018"

    def test_get_instrument_key_returns_none_for_unknown(self, tmp_path):
        feed = _make_feed(cache_dir=str(tmp_path))
        feed._symbol_to_key = {}
        assert feed._get_instrument_key("UNKNOWN_SYM") is None


# ---------------------------------------------------------------------------
# subscribe() method
# ---------------------------------------------------------------------------

class TestSubscribe:
    def test_subscribe_resolves_symbols(self, tmp_path):
        feed = _make_feed(cache_dir=str(tmp_path))
        feed._symbol_to_key = {
            "RELIANCE": "NSE_EQ|INE002A01018",
            "INFY": "NSE_EQ|INE009A01021",
        }
        feed._ws = None  # no WS, no send
        feed.subscribe(["RELIANCE", "INFY"])
        assert "RELIANCE" in feed._subscribed_symbols
        assert "INFY" in feed._subscribed_symbols
        assert "NSE_EQ|INE002A01018" in feed._subscribed_keys
        assert "NSE_EQ|INE009A01021" in feed._subscribed_keys

    def test_subscribe_skips_unknown_symbol(self, tmp_path):
        feed = _make_feed(cache_dir=str(tmp_path))
        feed._symbol_to_key = {"RELIANCE": "NSE_EQ|INE002A01018"}
        feed._ws = None
        feed.subscribe(["RELIANCE", "UNKNOWN"])
        assert "RELIANCE" in feed._subscribed_symbols
        assert "UNKNOWN" not in feed._subscribed_symbols

    def test_subscribe_sends_ws_message_when_connected(self, tmp_path):
        feed = _make_feed(cache_dir=str(tmp_path))
        feed._symbol_to_key = {"RELIANCE": "NSE_EQ|INE002A01018"}
        mock_ws = MagicMock()
        feed._ws = mock_ws
        feed.subscribe(["RELIANCE"])
        mock_ws.send.assert_called_once()
        sent_payload = json.loads(mock_ws.send.call_args[0][0])
        assert sent_payload["method"] == "sub"
        assert "NSE_EQ|INE002A01018" in sent_payload["data"]["instrumentKeys"]

    def test_subscribe_empty_list_logs_error(self, tmp_path, caplog):
        feed = _make_feed(cache_dir=str(tmp_path))
        feed._symbol_to_key = {}
        feed._ws = None
        import logging
        with caplog.at_level(logging.ERROR, logger="agent.upstox"):
            feed.subscribe(["NOSYM"])
        assert feed._subscribed_symbols == []


# ---------------------------------------------------------------------------
# Tick callback firing
# ---------------------------------------------------------------------------

class TestTickCallbacks:
    def test_fire_callbacks_calls_all_registered(self):
        from core.types import Tick
        feed = _make_feed()
        results = []
        feed.on_tick(lambda t: results.append(("cb1", t.symbol)))
        feed.on_tick(lambda t: results.append(("cb2", t.symbol)))
        tick = Tick(symbol="INFY", ltp=1500.0, bid=1499.5, ask=1500.5, volume=100, ts=datetime.utcnow())
        feed._fire_callbacks(tick)
        assert ("cb1", "INFY") in results
        assert ("cb2", "INFY") in results

    def test_fire_callbacks_tolerates_exception_in_one_callback(self):
        from core.types import Tick
        feed = _make_feed()
        results = []

        def bad_cb(t):
            raise ValueError("callback error")

        feed.on_tick(bad_cb)
        feed.on_tick(lambda t: results.append(t.symbol))
        tick = Tick(symbol="RELIANCE", ltp=2800.0, bid=2799.0, ask=2801.0, volume=500, ts=datetime.utcnow())
        feed._fire_callbacks(tick)  # Must not raise
        assert "RELIANCE" in results

    def test_fire_callbacks_updates_last_tick_ts_via_on_data(self):
        feed = _make_feed()
        feed._key_to_symbol = {"NSE_EQ|INE009A01021": "INFY"}

        # Build a minimal valid protobuf frame with ltp field
        ltpc_bytes = _encode_field_float(1, 1500.0)
        feed_bytes = _encode_field_bytes(1, ltpc_bytes)
        map_entry = _encode_field_string(1, "NSE_EQ|INE009A01021") + _encode_field_bytes(2, feed_bytes)
        frame = _encode_field_bytes(2, map_entry)

        assert feed._last_tick_ts == 0.0
        feed._decode_protobuf_frame(frame)
        assert feed._last_tick_ts > 0.0


# ---------------------------------------------------------------------------
# Protobuf parser helpers
# ---------------------------------------------------------------------------

class TestProtoHelpers:
    def test_read_varint_single_byte(self):
        val, pos = _read_varint(b"\x05", 0)
        assert val == 5
        assert pos == 1

    def test_read_varint_multi_byte(self):
        # 300 = 0xAC 0x02
        val, pos = _read_varint(b"\xac\x02", 0)
        assert val == 300
        assert pos == 2

    def test_read_varint_zero(self):
        val, pos = _read_varint(b"\x00", 0)
        assert val == 0

    def test_parse_ltpc_extracts_ltp(self):
        buf = _encode_field_float(1, 1500.0)
        result = _parse_ltpc(buf)
        assert abs(result.get("ltp", 0) - 1500.0) < 0.5

    def test_parse_ltpc_extracts_close(self):
        buf = _encode_field_float(1, 1500.0) + _encode_field_float(2, 1490.0)
        result = _parse_ltpc(buf)
        assert abs(result.get("ltp", 0) - 1500.0) < 0.5
        assert abs(result.get("close_price", 0) - 1490.0) < 0.5

    def test_parse_bid_ask_extracts_bid_ask(self):
        buf = _encode_field_float(1, 1499.5) + _encode_field_float(2, 1500.5)
        result = _parse_bid_ask(buf)
        assert abs(result.get("bid", 0) - 1499.5) < 0.5
        assert abs(result.get("ask", 0) - 1500.5) < 0.5

    def test_parse_feed_defaults_bid_ask_to_ltp(self):
        ltpc_bytes = _encode_field_float(1, 2800.0)
        feed_bytes = _encode_field_bytes(1, ltpc_bytes)
        result = _parse_feed(feed_bytes)
        assert abs(result["ltp"] - 2800.0) < 1.0
        assert abs(result["bid"] - 2800.0) < 1.0
        assert abs(result["ask"] - 2800.0) < 1.0

    def test_parse_upstox_frame_decodes_ltp(self):
        ltpc_bytes = _encode_field_float(1, 1500.0)
        feed_bytes = _encode_field_bytes(1, ltpc_bytes)
        map_entry = _encode_field_string(1, "NSE_EQ|INE009A01021") + _encode_field_bytes(2, feed_bytes)
        frame = _encode_field_bytes(2, map_entry)

        result = _parse_upstox_frame(frame)
        assert result is not None
        assert "NSE_EQ|INE009A01021" in result
        assert abs(result["NSE_EQ|INE009A01021"]["ltp"] - 1500.0) < 1.0

    def test_parse_upstox_frame_returns_none_for_empty(self):
        result = _parse_upstox_frame(b"")
        assert result is None

    def test_parse_upstox_frame_returns_none_for_garbage(self):
        result = _parse_upstox_frame(b"\xff\xff\xff\xff\xff")
        assert result is None

    def test_parse_upstox_frame_multiple_instruments(self):
        def _make_entry(key: str, ltp: float) -> bytes:
            ltpc_bytes = _encode_field_float(1, ltp)
            feed_bytes = _encode_field_bytes(1, ltpc_bytes)
            return _encode_field_bytes(2,
                _encode_field_string(1, key) + _encode_field_bytes(2, feed_bytes)
            )

        frame = (
            _make_entry("NSE_EQ|INE002A01018", 2800.0)
            + _make_entry("NSE_EQ|INE009A01021", 1500.0)
        )
        result = _parse_upstox_frame(frame)
        assert result is not None
        assert len(result) == 2
        assert "NSE_EQ|INE002A01018" in result
        assert "NSE_EQ|INE009A01021" in result


# ---------------------------------------------------------------------------
# decode_protobuf_frame — integration
# ---------------------------------------------------------------------------

class TestDecodeProtobufFrame:
    def test_decodes_frame_fires_callback(self):
        from core.types import Tick
        feed = _make_feed()
        feed._key_to_symbol = {"NSE_EQ|INE009A01021": "INFY"}
        ticks = []
        feed.on_tick(ticks.append)

        ltpc_bytes = _encode_field_float(1, 1500.0)
        feed_bytes = _encode_field_bytes(1, ltpc_bytes)
        map_entry = _encode_field_string(1, "NSE_EQ|INE009A01021") + _encode_field_bytes(2, feed_bytes)
        frame = _encode_field_bytes(2, map_entry)

        feed._decode_protobuf_frame(frame)
        assert len(ticks) == 1
        assert ticks[0].symbol == "INFY"
        assert abs(ticks[0].ltp - 1500.0) < 1.0

    def test_skips_zero_ltp(self):
        feed = _make_feed()
        feed._key_to_symbol = {"NSE_EQ|INE009A01021": "INFY"}
        ticks = []
        feed.on_tick(ticks.append)

        ltpc_bytes = _encode_field_float(1, 0.0)
        feed_bytes = _encode_field_bytes(1, ltpc_bytes)
        map_entry = _encode_field_string(1, "NSE_EQ|INE009A01021") + _encode_field_bytes(2, feed_bytes)
        frame = _encode_field_bytes(2, map_entry)

        feed._decode_protobuf_frame(frame)
        assert len(ticks) == 0

    def test_tolerates_corrupt_frame(self):
        feed = _make_feed()
        ticks = []
        feed.on_tick(ticks.append)
        feed._decode_protobuf_frame(b"\x99\x88\x77\x66garbage")
        assert len(ticks) == 0


# ---------------------------------------------------------------------------
# Authorization
# ---------------------------------------------------------------------------

class TestAuthorization:
    def test_authorize_websocket_returns_uri(self):
        feed = _make_feed()
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "data": {"authorizedRedirectUri": "wss://feeds.upstox.com/v3/abc123"}
        }
        mock_resp.raise_for_status = MagicMock()

        with patch("requests.get", return_value=mock_resp) as mock_get:
            uri = feed._authorize_websocket()

        assert uri == "wss://feeds.upstox.com/v3/abc123"
        call_kwargs = mock_get.call_args
        assert "Authorization" in call_kwargs[1]["headers"]

    def test_authorize_websocket_raises_on_missing_uri(self):
        feed = _make_feed()
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"data": {}}
        mock_resp.raise_for_status = MagicMock()

        with patch("requests.get", return_value=mock_resp):
            with pytest.raises(RuntimeError, match="authorizedRedirectUri"):
                feed._authorize_websocket()

    def test_authorize_sends_bearer_token(self):
        feed = _make_feed(access_token="my-secret-token")
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "data": {"authorizedRedirectUri": "wss://feeds.upstox.com/v3/x"}
        }
        mock_resp.raise_for_status = MagicMock()

        with patch("requests.get", return_value=mock_resp) as mock_get:
            feed._authorize_websocket()

        headers = mock_get.call_args[1]["headers"]
        assert headers["Authorization"] == "Bearer my-secret-token"


# ---------------------------------------------------------------------------
# HTTP polling fallback
# ---------------------------------------------------------------------------

class TestPollingFallback:
    def test_poll_ticks_fires_callback(self):
        from core.types import Tick
        feed = _make_feed()
        feed._subscribed_keys = ["NSE_EQ|INE009A01021"]
        feed._key_to_symbol = {"NSE_EQ|INE009A01021": "INFY"}
        ticks = []
        feed.on_tick(ticks.append)

        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "data": {
                "NSE_EQ:INE009A01021": {
                    "last_price": 1500.0,
                    "volume": 1000,
                }
            }
        }
        mock_resp.raise_for_status = MagicMock()

        with patch("requests.get", return_value=mock_resp):
            feed._poll_ticks()

        assert len(ticks) == 1
        assert ticks[0].symbol == "INFY"
        assert abs(ticks[0].ltp - 1500.0) < 0.01

    def test_poll_ticks_handles_request_error(self):
        import requests as req_module
        feed = _make_feed()
        feed._subscribed_keys = ["NSE_EQ|INE009A01021"]

        with patch("requests.get", side_effect=req_module.RequestException("timeout")):
            feed._poll_ticks()  # Must not raise

    def test_poll_ticks_skips_zero_ltp(self):
        feed = _make_feed()
        feed._subscribed_keys = ["NSE_EQ|INE009A01021"]
        feed._key_to_symbol = {"NSE_EQ|INE009A01021": "INFY"}
        ticks = []
        feed.on_tick(ticks.append)

        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "data": {"NSE_EQ:INE009A01021": {"last_price": 0.0}}
        }
        mock_resp.raise_for_status = MagicMock()

        with patch("requests.get", return_value=mock_resp):
            feed._poll_ticks()

        assert len(ticks) == 0

    def test_poll_ticks_updates_last_tick_ts(self):
        feed = _make_feed()
        feed._subscribed_keys = ["NSE_EQ|INE009A01021"]
        feed._key_to_symbol = {"NSE_EQ|INE009A01021": "INFY"}

        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "data": {"NSE_EQ:INE009A01021": {"last_price": 1500.0}}
        }
        mock_resp.raise_for_status = MagicMock()

        assert feed._last_tick_ts == 0.0
        with patch("requests.get", return_value=mock_resp):
            feed._poll_ticks()

        assert feed._last_tick_ts > 0.0


# ---------------------------------------------------------------------------
# WebSocket event handlers
# ---------------------------------------------------------------------------

class TestWebSocketHandlers:
    def test_on_open_sets_connected(self):
        feed = _make_feed()
        assert not feed._connected
        feed._on_open(MagicMock())
        assert feed._connected

    def test_on_open_sends_subscribe_if_keys_pending(self):
        feed = _make_feed()
        feed._subscribed_keys = ["NSE_EQ|INE009A01021"]
        mock_ws = MagicMock()
        feed._ws = mock_ws
        feed._on_open(mock_ws)
        mock_ws.send.assert_called_once()

    def test_on_error_clears_connected(self):
        feed = _make_feed()
        feed._connected = True
        feed._on_error(MagicMock(), Exception("test error"))
        assert not feed._connected

    def test_on_close_clears_connected(self):
        feed = _make_feed()
        feed._connected = True
        feed._stop_event.set()  # prevent reconnect attempt
        feed._on_close(MagicMock(), 1000, "Normal closure")
        assert not feed._connected

    def test_on_message_handles_text(self):
        feed = _make_feed()
        # Should not raise on valid JSON text message
        feed._on_message(MagicMock(), '{"type": "heartbeat"}')

    def test_on_message_handles_non_json_text(self):
        feed = _make_feed()
        feed._on_message(MagicMock(), "not-json-data")  # Should not raise

    def test_on_message_handles_binary(self):
        feed = _make_feed()
        feed._on_message(MagicMock(), b"\x00\x01\x02")  # minimal binary, should not raise


# ---------------------------------------------------------------------------
# connect() integration — mocked network
# ---------------------------------------------------------------------------

class TestConnect:
    def test_connect_loads_instruments_and_opens_ws(self, tmp_path):
        feed = _make_feed(cache_dir=str(tmp_path))
        gz_data = _make_instruments_gz(SAMPLE_INSTRUMENTS)

        auth_resp = MagicMock()
        auth_resp.json.return_value = {
            "data": {"authorizedRedirectUri": "wss://feeds.upstox.com/v3/abc"}
        }
        auth_resp.raise_for_status = MagicMock()
        auth_resp.content = gz_data

        master_resp = MagicMock()
        master_resp.content = gz_data
        master_resp.raise_for_status = MagicMock()

        def _mock_get(url, **kwargs):
            if "instruments" in url or "assets.upstox" in url:
                return master_resp
            return auth_resp

        with patch("requests.get", side_effect=_mock_get):
            with patch("websocket.WebSocketApp") as mock_ws_cls:
                mock_ws_inst = MagicMock()
                mock_ws_cls.return_value = mock_ws_inst
                # Simulate WS connection opening quickly
                feed._connected = False

                def _run_forever(**kwargs):
                    feed._connected = True

                mock_ws_inst.run_forever.side_effect = _run_forever

                with patch("threading.Thread") as mock_thread_cls:
                    mock_thread = MagicMock()
                    mock_thread_cls.return_value = mock_thread
                    feed.connect()

                # Instrument maps should be populated
                assert "RELIANCE" in feed._symbol_to_key

    def test_connect_falls_back_to_polling_on_ws_failure(self, tmp_path):
        feed = _make_feed(cache_dir=str(tmp_path))
        gz_data = _make_instruments_gz(SAMPLE_INSTRUMENTS)

        master_resp = MagicMock()
        master_resp.content = gz_data
        master_resp.raise_for_status = MagicMock()

        def _mock_get(url, **kwargs):
            if "assets.upstox" in url:
                return master_resp
            raise RuntimeError("auth failed")

        with patch("requests.get", side_effect=_mock_get):
            with patch("threading.Thread") as mock_thread_cls:
                mock_thread = MagicMock()
                mock_thread_cls.return_value = mock_thread
                feed.connect()

        # Should be connected via polling
        assert feed.is_connected() is True


# ---------------------------------------------------------------------------
# _send_subscribe payload format
# ---------------------------------------------------------------------------

class TestSendSubscribe:
    def test_payload_has_correct_structure(self):
        feed = _make_feed()
        mock_ws = MagicMock()
        feed._ws = mock_ws
        feed._send_subscribe(["NSE_EQ|INE002A01018", "NSE_EQ|INE009A01021"])

        mock_ws.send.assert_called_once()
        payload = json.loads(mock_ws.send.call_args[0][0])
        assert payload["method"] == "sub"
        assert payload["data"]["mode"] == "full"
        assert "NSE_EQ|INE002A01018" in payload["data"]["instrumentKeys"]
        assert "guid" in payload

    def test_no_send_when_ws_is_none(self):
        feed = _make_feed()
        feed._ws = None
        feed._send_subscribe(["NSE_EQ|INE002A01018"])  # Must not raise
