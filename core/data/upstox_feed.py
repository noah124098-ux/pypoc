"""Upstox V3 WebSocket live data feed. Implements ILiveFeed interface.

Authentication:
  GET https://api.upstox.com/v2/feed/market-data-feed/authorize
  Headers: Authorization: Bearer {access_token}
  Returns: {"data": {"authorizedRedirectUri": "wss://..."}}

WebSocket subscription payload:
  {"guid": "<uuid4>", "method": "sub", "data": {"mode": "full", "instrumentKeys": [...]}}

Instrument keys: "NSE_EQ|<ISIN>" e.g. "NSE_EQ|INE009A01021" for Infosys.

The feed resolves NSE symbols to Upstox instrument keys by downloading the
Upstox instrument master JSON (cached daily in data/cache/upstox_instruments.json).

Protobuf decoding: the upstox-python-sdk is NOT installed; we use the raw
websocket-client + requests stack. Protobuf frames are decoded using the
MarketDataFeedV3 proto definitions included inline (LTP-only subset sufficient
for Tick construction). Falls back to HTTP polling if WebSocket fails.

SECURITY: Credentials are read from .env only. Token values are never logged.
"""
from __future__ import annotations

import gzip
import json
import logging
import os
import struct
import threading
import time
import uuid
from datetime import datetime, timedelta
from io import BytesIO
from pathlib import Path
from typing import Callable, Optional

import requests
from dotenv import load_dotenv

from core.data.feed_base import ILiveFeed
from core.types import Tick

# Optional websocket import
try:
    import websocket  # type: ignore
    _WS_AVAILABLE = True
except ImportError:
    _WS_AVAILABLE = False

# Optional protobuf import
try:
    from google.protobuf import descriptor_pool as _dp  # noqa: F401
    _PROTOBUF_AVAILABLE = True
except ImportError:
    _PROTOBUF_AVAILABLE = False

log = logging.getLogger("agent.upstox")

# Upstox API endpoints
_AUTH_URL = "https://api.upstox.com/v2/feed/market-data-feed/authorize"
_QUOTE_URL = "https://api.upstox.com/v2/market-quote/ltp"
_INSTRUMENTS_URL = "https://assets.upstox.com/market-quote/instruments/exchange/NSE.json.gz"
_CACHE_FILENAME = "upstox_instruments.json"
_CACHE_MAX_AGE_HOURS = 20  # refresh instrument master once per trading day


class UpstoxFeed(ILiveFeed):
    """Upstox V3 WebSocket live tick feed.

    Connects to the Upstox market-data-feed WebSocket, subscribes to NSE EQ
    instruments, decodes protobuf frames, and fires callbacks with Tick objects.

    Falls back to HTTP LTP polling if WebSocket is unavailable or fails.
    """

    def __init__(
        self,
        api_key: str,
        access_token: str,
        reconnect_max_attempts: int = 5,
        reconnect_backoff_seconds: int = 2,
        cache_dir: str = "data/cache",
        poll_interval_seconds: float = 5.0,
    ):
        """Initialise the feed.

        Args:
            api_key: Upstox API key from developer console.
            access_token: Short-lived OAuth2 access token (refreshed daily).
            reconnect_max_attempts: WebSocket reconnect retries before giving up.
            reconnect_backoff_seconds: Base seconds for exponential backoff.
            cache_dir: Directory for caching the instrument master.
            poll_interval_seconds: Seconds between HTTP poll requests (fallback mode).

        Raises:
            ValueError: If api_key or access_token is empty.
        """
        if not api_key or not access_token:
            raise ValueError(
                "Upstox credentials missing. Set UPSTOX_API_KEY and "
                "UPSTOX_ACCESS_TOKEN in .env before using UpstoxFeed."
            )
        self._api_key = api_key
        self._access_token = access_token
        self._reconnect_max = reconnect_max_attempts
        self._reconnect_backoff = reconnect_backoff_seconds
        self._cache_dir = Path(cache_dir)
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        self._poll_interval = poll_interval_seconds

        self._connected: bool = False
        self._last_tick_ts: float = 0.0
        self._tick_callbacks: list[Callable[[Tick], None]] = []
        self._subscribed_symbols: list[str] = []
        self._subscribed_keys: list[str] = []

        # Symbol -> instrument key mapping (loaded from master)
        self._symbol_to_key: dict[str, str] = {}
        self._key_to_symbol: dict[str, str] = {}

        self._ws: Optional[object] = None  # websocket.WebSocketApp
        self._lock = threading.Lock()
        self._ws_thread: Optional[threading.Thread] = None
        self._poll_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()

    # ---------- ILiveFeed ----------

    def connect(self) -> None:
        """Open Upstox WebSocket v3 connection (or start HTTP polling fallback).

        Steps:
        1. Load instrument master (download/cache).
        2. Authorize WebSocket via REST to get the signed WSS URI.
        3. Connect WebSocket and start daemon thread.
        4. If WebSocket unavailable, start HTTP polling thread.

        Raises:
            RuntimeError: If authorization fails and HTTP polling also unavailable.
        """
        self._stop_event.clear()
        self._load_instrument_master()

        if _WS_AVAILABLE:
            try:
                ws_uri = self._authorize_websocket()
                self._open_websocket(ws_uri)
                log.info("Upstox WebSocket connected")
                return
            except Exception as exc:
                log.warning("Upstox WebSocket connect failed (%s); falling back to HTTP polling", exc)

        # Fallback: HTTP polling
        log.info("Starting Upstox HTTP LTP polling (interval=%.1fs)", self._poll_interval)
        self._connected = True  # polling counts as connected
        self._poll_thread = threading.Thread(
            target=self._poll_loop,
            daemon=True,
            name="upstox-poll",
        )
        self._poll_thread.start()

    def disconnect(self) -> None:
        """Close WebSocket / stop polling thread."""
        self._stop_event.set()
        if self._ws is not None:
            try:
                self._ws.close()  # type: ignore[attr-defined]
            except Exception as exc:
                log.warning("Error closing Upstox WS: %s", exc)
        self._connected = False
        log.info("Upstox feed disconnected")

    def subscribe(self, symbols: list[str]) -> None:
        """Subscribe to live ticks for NSE symbols.

        Resolves symbol names to Upstox instrument keys and sends a subscription
        message over the open WebSocket (or queues for the polling loop).

        Args:
            symbols: NSE symbol names e.g. ["RELIANCE", "INFY"].
        """
        keys = []
        resolved = []
        for sym in symbols:
            key = self._get_instrument_key(sym)
            if key is None:
                log.warning("No instrument key found for symbol %s — skipping", sym)
                continue
            keys.append(key)
            resolved.append(sym)
            self._symbol_to_key[sym] = key
            self._key_to_symbol[key] = sym

        if not keys:
            log.error("No subscribable symbols resolved from: %s", symbols)
            return

        self._subscribed_symbols.extend(resolved)
        self._subscribed_keys.extend(keys)
        log.info("Upstox: resolved %d / %d symbols to instrument keys", len(resolved), len(symbols))

        if self._ws is not None:
            self._send_subscribe(keys)

    def on_tick(self, callback: Callable[[Tick], None]) -> None:
        """Register a callback invoked on every decoded tick."""
        self._tick_callbacks.append(callback)

    def is_connected(self) -> bool:
        """Return True if the feed is currently connected."""
        return self._connected

    def last_tick_age_seconds(self) -> float:
        """Seconds since last tick (any symbol). Returns inf if no tick received."""
        if self._last_tick_ts == 0.0:
            return float("inf")
        return time.time() - self._last_tick_ts

    # ---------- Class factory ----------

    @classmethod
    def from_env(cls) -> "UpstoxFeed":
        """Construct UpstoxFeed from environment variables.

        Reads UPSTOX_API_KEY and UPSTOX_ACCESS_TOKEN from .env / environment.

        Raises:
            ValueError: If either variable is absent or empty.
        """
        load_dotenv(override=False)
        api_key = os.getenv("UPSTOX_API_KEY", "")
        access_token = os.getenv("UPSTOX_ACCESS_TOKEN", "")
        return cls(api_key=api_key, access_token=access_token)

    # ---------- Authorization ----------

    def _authorize_websocket(self) -> str:
        """Call the Upstox authorize endpoint to get a signed WebSocket URI.

        Returns:
            Authorized WSS URI string.

        Raises:
            RuntimeError: If the API call fails or the URI is missing.
        """
        headers = {
            "Authorization": f"Bearer {self._access_token}",
            "Accept": "application/json",
        }
        resp = requests.get(_AUTH_URL, headers=headers, timeout=15)
        resp.raise_for_status()
        body = resp.json()
        uri = body.get("data", {}).get("authorizedRedirectUri", "")
        if not uri:
            raise RuntimeError(
                f"Upstox authorize response missing authorizedRedirectUri: {body}"
            )
        return uri

    # ---------- WebSocket internals ----------

    def _open_websocket(self, ws_uri: str) -> None:
        """Open WebSocket connection to ws_uri and start daemon thread."""
        self._ws = websocket.WebSocketApp(  # type: ignore[attr-defined]
            ws_uri,
            on_open=self._on_open,
            on_message=self._on_message,
            on_error=self._on_error,
            on_close=self._on_close,
            header={"Authorization": f"Bearer {self._access_token}"},
        )
        self._ws_thread = threading.Thread(
            target=self._ws.run_forever,  # type: ignore[union-attr]
            kwargs={"reconnect": self._reconnect_backoff},
            daemon=True,
            name="upstox-ws",
        )
        self._ws_thread.start()

        # Wait briefly for the open handshake
        for _ in range(20):
            if self._connected:
                break
            time.sleep(0.3)

    def _send_subscribe(self, instrument_keys: list[str]) -> None:
        """Send subscription request for the given instrument keys."""
        if self._ws is None:
            return
        payload = {
            "guid": str(uuid.uuid4()),
            "method": "sub",
            "data": {
                "mode": "full",
                "instrumentKeys": instrument_keys,
            },
        }
        try:
            self._ws.send(json.dumps(payload))  # type: ignore[attr-defined]
            log.debug("Sent subscribe for %d keys", len(instrument_keys))
        except Exception as exc:
            log.warning("Failed to send subscription: %s", exc)

    def _on_open(self, ws) -> None:
        """WebSocket open handler."""
        self._connected = True
        log.info("Upstox WS opened")
        if self._subscribed_keys:
            self._send_subscribe(self._subscribed_keys)

    def _on_message(self, ws, message) -> None:
        """Receive a WebSocket message (binary protobuf or JSON text)."""
        try:
            if isinstance(message, bytes):
                self._decode_protobuf_frame(message)
            else:
                # Some messages come as JSON text (heartbeat, error, confirmation)
                try:
                    data = json.loads(message)
                    log.debug("Upstox WS text message: %s", data)
                except json.JSONDecodeError:
                    log.debug("Upstox WS non-JSON text: %s", message[:200])
        except Exception as exc:
            log.exception("Error processing Upstox WS message: %s", exc)

    def _on_error(self, ws, error) -> None:
        """WebSocket error handler."""
        log.error("Upstox WS error: %s", error)
        self._connected = False

    def _on_close(self, ws, close_status_code, close_msg) -> None:
        """WebSocket close handler — attempt reconnect if not stopped."""
        log.warning("Upstox WS closed (code=%s msg=%s)", close_status_code, close_msg)
        self._connected = False
        if not self._stop_event.is_set():
            self._reconnect_with_backoff()

    def _reconnect_with_backoff(self) -> None:
        """Attempt to reconnect WebSocket with exponential backoff."""
        for attempt in range(1, self._reconnect_max + 1):
            wait = self._reconnect_backoff * (2 ** (attempt - 1))
            log.info("Upstox WS reconnect attempt %d in %ds...", attempt, wait)
            time.sleep(wait)
            if self._stop_event.is_set():
                return
            try:
                ws_uri = self._authorize_websocket()
                self._open_websocket(ws_uri)
                if self._subscribed_keys:
                    self._send_subscribe(self._subscribed_keys)
                log.info("Upstox WS reconnected on attempt %d", attempt)
                return
            except Exception as exc:
                log.warning("Reconnect attempt %d failed: %s", attempt, exc)
        log.error("Upstox WS failed to reconnect after %d attempts", self._reconnect_max)

    # ---------- Protobuf decoding ----------

    def _decode_protobuf_frame(self, data: bytes) -> None:
        """Decode a Upstox MarketDataFeedV3 protobuf frame.

        The Upstox V3 binary frame uses standard protobuf encoding.
        We parse only the fields needed for a Tick:
          - instrument_key (field 1, string)  — maps back to symbol
          - feeds (field 2, message map)
            - ltpc (field 1) -> ltp (field 1, float)
            - market_level (field 2) -> bid_ask (field 1/2)
            - market_ohlc (field 3) -> volumes

        Since the upstox-python-sdk is not installed, we use a lightweight
        hand-rolled protobuf parser that handles the subset of field types
        present in the feed.
        """
        try:
            decoded = _parse_upstox_frame(data)
            if decoded is None:
                return
            for instrument_key, tick_data in decoded.items():
                symbol = self._key_to_symbol.get(instrument_key)
                if symbol is None:
                    # Try to extract symbol from key (format: "NSE_EQ|ISIN")
                    parts = instrument_key.split("|")
                    if len(parts) == 2:
                        symbol = self._key_to_symbol.get(instrument_key, parts[1])
                    else:
                        continue

                ltp = tick_data.get("ltp", 0.0)
                bid = tick_data.get("bid", ltp)
                ask = tick_data.get("ask", ltp)
                volume = tick_data.get("volume", 0)

                if ltp <= 0:
                    continue

                tick = Tick(
                    symbol=symbol,
                    ltp=ltp,
                    bid=bid,
                    ask=ask,
                    volume=int(volume),
                    ts=datetime.utcnow(),
                )
                with self._lock:
                    self._last_tick_ts = time.time()
                self._fire_callbacks(tick)
        except Exception as exc:
            log.debug("Protobuf decode error: %s (frame size=%d)", exc, len(data))

    def _fire_callbacks(self, tick: Tick) -> None:
        """Invoke all registered tick callbacks."""
        for cb in self._tick_callbacks:
            try:
                cb(tick)
            except Exception as exc:
                log.exception("Tick callback raised: %s", exc)

    # ---------- HTTP polling fallback ----------

    def _poll_loop(self) -> None:
        """Poll Upstox LTP endpoint for subscribed symbols until stopped."""
        log.info("Upstox poll loop started for %d symbols", len(self._subscribed_keys))
        while not self._stop_event.is_set():
            if self._subscribed_keys:
                self._poll_ticks()
            self._stop_event.wait(self._poll_interval)

    def _poll_ticks(self) -> None:
        """Fetch current LTP for all subscribed instruments via REST."""
        # Upstox market-quote/ltp accepts comma-separated instrument keys
        symbol_param = ",".join(self._subscribed_keys[:50])  # API limit
        headers = {
            "Authorization": f"Bearer {self._access_token}",
            "Accept": "application/json",
        }
        try:
            resp = requests.get(
                _QUOTE_URL,
                headers=headers,
                params={"instrument_key": symbol_param},
                timeout=10,
            )
            resp.raise_for_status()
            body = resp.json()
            data = body.get("data", {})
            for key_name, quote in data.items():
                # key_name may be "NSE_EQ:ISIN" (colon-separated in REST vs pipe in WS)
                instrument_key = key_name.replace(":", "|")
                symbol = self._key_to_symbol.get(instrument_key)
                if symbol is None:
                    # Fallback: try to match by stripping exchange prefix
                    for k, s in self._key_to_symbol.items():
                        if k.endswith("|" + key_name.split(":")[-1]):
                            symbol = s
                            break
                if symbol is None:
                    continue

                ltp = float(quote.get("last_price", 0.0))
                if ltp <= 0:
                    continue

                tick = Tick(
                    symbol=symbol,
                    ltp=ltp,
                    bid=ltp,  # LTP endpoint does not return bid/ask separately
                    ask=ltp,
                    volume=int(quote.get("volume", 0)),
                    ts=datetime.utcnow(),
                )
                with self._lock:
                    self._last_tick_ts = time.time()
                self._fire_callbacks(tick)
        except requests.RequestException as exc:
            log.warning("Upstox HTTP poll error: %s", exc)
        except Exception as exc:
            log.exception("Unexpected error in Upstox poll: %s", exc)

    # ---------- Instrument master ----------

    def _load_instrument_master(self) -> None:
        """Download (and cache daily) NSE instrument master, build key maps."""
        cache_file = self._cache_dir / _CACHE_FILENAME
        needs_refresh = True

        if cache_file.exists():
            age = time.time() - cache_file.stat().st_mtime
            if age < _CACHE_MAX_AGE_HOURS * 3600:
                needs_refresh = False

        if needs_refresh:
            log.info("Downloading Upstox NSE instrument master ...")
            try:
                resp = requests.get(_INSTRUMENTS_URL, timeout=30)
                resp.raise_for_status()
                # The file is gzip-compressed
                with gzip.open(BytesIO(resp.content)) as gz:
                    instruments = json.loads(gz.read().decode("utf-8"))
                cache_file.write_text(
                    json.dumps(instruments, separators=(",", ":")),
                    encoding="utf-8",
                )
                log.info("Instrument master downloaded (%d entries)", len(instruments))
            except Exception as exc:
                log.warning("Failed to download Upstox instrument master: %s", exc)
                if cache_file.exists():
                    log.info("Using stale instrument cache")
                    with open(cache_file, encoding="utf-8") as fh:
                        instruments = json.load(fh)
                else:
                    log.error("No instrument master available. Symbol resolution will fail.")
                    return
        else:
            with open(cache_file, encoding="utf-8") as fh:
                instruments = json.load(fh)

        # Build lookup tables
        # Instrument record shape (from Upstox master):
        # {"instrument_key": "NSE_EQ|INE009A01021", "trading_symbol": "INFY", ...}
        count = 0
        for inst in instruments:
            key = inst.get("instrument_key", "")
            sym = inst.get("trading_symbol", "")
            # Only NSE EQ segment
            if not key.startswith("NSE_EQ|"):
                continue
            if sym:
                self._symbol_to_key[sym] = key
                self._key_to_symbol[key] = sym
                count += 1
        log.info("Loaded %d NSE EQ instrument keys", count)

    def _get_instrument_key(self, symbol: str) -> Optional[str]:
        """Resolve an NSE trading symbol to an Upstox instrument key.

        Args:
            symbol: NSE trading symbol e.g. "RELIANCE".

        Returns:
            Instrument key string e.g. "NSE_EQ|INE002A01018", or None if not found.
        """
        return self._symbol_to_key.get(symbol)


# ---------------------------------------------------------------------------
# Lightweight protobuf parser (subset decoder for Upstox MarketDataFeedV3)
# ---------------------------------------------------------------------------
# The Upstox V3 feed sends a FeedResponse protobuf message. We only need:
#   FeedResponse.feeds: map<string, Feed>
#     Feed.ltpc: LTPC { ltp: float, close_price: float, ... }
#     Feed.market_level: MarketLevel { bid_ask: [BidAsk] }
#
# We implement a minimal varint / wire-type parser rather than importing
# the full generated proto classes (which require upstox-python-sdk).
# ---------------------------------------------------------------------------

def _read_varint(buf: bytes, pos: int) -> tuple[int, int]:
    """Read a protobuf varint starting at pos. Returns (value, new_pos)."""
    result = 0
    shift = 0
    while pos < len(buf):
        b = buf[pos]
        pos += 1
        result |= (b & 0x7F) << shift
        if not (b & 0x80):
            break
        shift += 7
    return result, pos


def _read_length_delimited(buf: bytes, pos: int) -> tuple[bytes, int]:
    """Read a length-delimited field. Returns (field_bytes, new_pos)."""
    length, pos = _read_varint(buf, pos)
    return buf[pos: pos + length], pos + length


def _decode_float(buf: bytes) -> float:
    """Decode 4-byte little-endian IEEE 754 float."""
    if len(buf) < 4:
        return 0.0
    return struct.unpack("<f", buf[:4])[0]


def _decode_double(buf: bytes) -> float:
    """Decode 8-byte little-endian IEEE 754 double."""
    if len(buf) < 8:
        return 0.0
    return struct.unpack("<d", buf[:8])[0]


def _parse_ltpc(buf: bytes) -> dict:
    """Parse LTPC message: field 1 = ltp (float), field 2 = close_price (float)."""
    result: dict = {}
    pos = 0
    while pos < len(buf):
        try:
            tag_varint, pos = _read_varint(buf, pos)
            field_number = tag_varint >> 3
            wire_type = tag_varint & 0x7
            if wire_type == 5:  # 32-bit float
                if pos + 4 <= len(buf):
                    val = _decode_float(buf[pos: pos + 4])
                    pos += 4
                    if field_number == 1:
                        result["ltp"] = val
                    elif field_number == 2:
                        result["close_price"] = val
                else:
                    break
            elif wire_type == 1:  # 64-bit double
                if pos + 8 <= len(buf):
                    val = _decode_double(buf[pos: pos + 8])
                    pos += 8
                    if field_number == 1:
                        result["ltp"] = val
                    elif field_number == 2:
                        result["close_price"] = val
                else:
                    break
            elif wire_type == 0:  # varint
                val, pos = _read_varint(buf, pos)
                if field_number == 1:
                    result["ltp"] = float(val)
            elif wire_type == 2:  # length-delimited
                field_bytes, pos = _read_length_delimited(buf, pos)
            else:
                break  # Unknown wire type — stop parsing this message
        except Exception:
            break
    return result


def _parse_bid_ask(buf: bytes) -> dict:
    """Parse BidAsk message: field 1 = bid (float), field 2 = ask (float)."""
    result: dict = {}
    pos = 0
    while pos < len(buf):
        try:
            tag_varint, pos = _read_varint(buf, pos)
            field_number = tag_varint >> 3
            wire_type = tag_varint & 0x7
            if wire_type == 5:  # 32-bit float
                if pos + 4 <= len(buf):
                    val = _decode_float(buf[pos: pos + 4])
                    pos += 4
                    if field_number == 1:
                        result["bid"] = val
                    elif field_number == 2:
                        result["ask"] = val
                else:
                    break
            elif wire_type == 1:  # 64-bit double
                if pos + 8 <= len(buf):
                    val = _decode_double(buf[pos: pos + 8])
                    pos += 8
                    if field_number == 1:
                        result["bid"] = val
                    elif field_number == 2:
                        result["ask"] = val
                else:
                    break
            elif wire_type == 0:
                val, pos = _read_varint(buf, pos)
            elif wire_type == 2:
                _, pos = _read_length_delimited(buf, pos)
            else:
                break
        except Exception:
            break
    return result


def _parse_feed(buf: bytes) -> dict:
    """Parse Feed message extracting ltpc and first bid/ask."""
    result: dict = {"ltp": 0.0, "bid": 0.0, "ask": 0.0, "volume": 0}
    pos = 0
    while pos < len(buf):
        try:
            tag_varint, pos = _read_varint(buf, pos)
            field_number = tag_varint >> 3
            wire_type = tag_varint & 0x7
            if wire_type == 2:  # length-delimited (nested message or string)
                field_bytes, pos = _read_length_delimited(buf, pos)
                if field_number == 1:  # ltpc
                    ltpc = _parse_ltpc(field_bytes)
                    if ltpc.get("ltp"):
                        result["ltp"] = ltpc["ltp"]
                elif field_number == 2:  # market_level — contains bid_ask repeated
                    ba = _parse_bid_ask(field_bytes)
                    if ba.get("bid"):
                        result["bid"] = ba["bid"]
                    if ba.get("ask"):
                        result["ask"] = ba["ask"]
            elif wire_type == 0:
                val, pos = _read_varint(buf, pos)
                if field_number == 6:  # volume (approximate field assignment)
                    result["volume"] = val
            elif wire_type == 5:
                pos += 4
            elif wire_type == 1:
                pos += 8
            else:
                break
        except Exception:
            break
    # Use ltp for bid/ask if not decoded
    if result["bid"] == 0.0:
        result["bid"] = result["ltp"]
    if result["ask"] == 0.0:
        result["ask"] = result["ltp"]
    return result


def _parse_map_entry(buf: bytes) -> tuple[Optional[str], Optional[dict]]:
    """Parse a single map entry: field 1 = key (string), field 2 = value (Feed)."""
    key = None
    value = None
    pos = 0
    while pos < len(buf):
        try:
            tag_varint, pos = _read_varint(buf, pos)
            field_number = tag_varint >> 3
            wire_type = tag_varint & 0x7
            if wire_type == 2:
                field_bytes, pos = _read_length_delimited(buf, pos)
                if field_number == 1:  # map key (instrument_key string)
                    key = field_bytes.decode("utf-8", errors="replace")
                elif field_number == 2:  # map value (Feed message)
                    value = _parse_feed(field_bytes)
            elif wire_type == 0:
                _, pos = _read_varint(buf, pos)
            elif wire_type == 5:
                pos += 4
            elif wire_type == 1:
                pos += 8
            else:
                break
        except Exception:
            break
    return key, value


def _parse_upstox_frame(data: bytes) -> Optional[dict[str, dict]]:
    """Parse a FeedResponse protobuf frame.

    Returns dict mapping instrument_key -> {ltp, bid, ask, volume}, or None on failure.

    FeedResponse fields:
      field 2: map<string, Feed> feeds  (wire type 2, each entry is a MapFieldEntry)
    """
    result: dict[str, dict] = {}
    pos = 0
    while pos < len(data):
        try:
            tag_varint, pos = _read_varint(data, pos)
            field_number = tag_varint >> 3
            wire_type = tag_varint & 0x7
            if wire_type == 2:
                field_bytes, pos = _read_length_delimited(data, pos)
                if field_number == 2:  # feeds map entry
                    key, feed = _parse_map_entry(field_bytes)
                    if key and feed and feed.get("ltp", 0) > 0:
                        result[key] = feed
            elif wire_type == 0:
                _, pos = _read_varint(data, pos)
            elif wire_type == 5:
                pos += 4
            elif wire_type == 1:
                pos += 8
            else:
                break
        except Exception:
            break
    return result if result else None
