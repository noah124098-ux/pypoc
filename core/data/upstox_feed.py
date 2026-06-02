"""Upstox live data feed stub. Implements ILiveFeed interface.

STATUS: STUB — not yet functional. This module scaffolds the interface for a
future Upstox WebSocket v3 integration. All network operations raise
NotImplementedError until the implementation is complete.

WHAT NEEDS TO BE IMPLEMENTED:
  1. OAuth2 authentication flow — Upstox uses auth-code + access token (not basic credentials).
     See: https://upstox.com/developer/api-documentation/authentication
  2. WebSocket v3 connection — wss://api.upstox.com/v3/feed/market-data-feed
     See: https://upstox.com/developer/api-documentation/market-data-feed-v3
  3. Instrument key resolution — Upstox uses keys like "NSE_EQ|INE009A01021" (not numeric tokens).
     Download from: https://assets.upstox.com/market-quote/instruments/exchange/NSE.json.gz
  4. Binary protobuf frame decoding — the v3 feed sends MarketFullFeed protobuf messages.
     Install: upstox-python-sdk (which pulls in protobuf). Guard with try/except below.
  5. Reconnect logic with exponential backoff (same pattern as AngelOneFeed).

SECURITY: Upstox credentials must be kept in .env only. Do NOT log token values.
         If adding order placement in a future phase, create a separate broker module
         with its own credentials — never reuse these data-feed credentials.

DEPENDENCY: upstox-python-sdk is NOT installed by default in this project.
            When the stub is promoted to a real implementation, add it to requirements.txt.
"""
from __future__ import annotations

import logging
import os
import time
from typing import Callable, Optional

from dotenv import load_dotenv

from core.data.feed_base import ILiveFeed
from core.types import Tick

# Optional SDK import — not installed yet.
try:
    import upstox_client  # type: ignore
    _UPSTOX_SDK_AVAILABLE = True
except ImportError:
    _UPSTOX_SDK_AVAILABLE = False

log = logging.getLogger("agent.upstox")


class UpstoxFeed(ILiveFeed):
    """Upstox WebSocket v3 live tick feed (STUB).

    When complete, this class will:
    - Authenticate via OAuth2 access token (obtained externally and passed in).
    - Open a WebSocket v3 connection to stream NSE EQ ticks.
    - Resolve symbol names to Upstox instrument keys (e.g. "NSE_EQ|INE009A01021").
    - Decode protobuf MarketFullFeed frames into Tick objects.
    - Reconnect automatically with exponential backoff.
    - Call registered on_tick() callbacks with each decoded Tick.
    """

    def __init__(
        self,
        api_key: str,
        access_token: str,
        reconnect_max_attempts: int = 5,
        reconnect_backoff_seconds: int = 2,
    ):
        """Initialise the feed with Upstox credentials.

        Args:
            api_key: Upstox API key (from developer console).
            access_token: Short-lived OAuth2 access token obtained via auth flow.
            reconnect_max_attempts: How many reconnect retries before giving up.
            reconnect_backoff_seconds: Base seconds for exponential reconnect backoff.

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

        self._connected: bool = False
        self._last_tick_ts: float = 0.0
        self._tick_callbacks: list[Callable[[Tick], None]] = []
        self._subscribed_symbols: list[str] = []

        # Will hold the WebSocket client once connect() is implemented.
        self._ws = None

    # ---------- ILiveFeed ----------

    def connect(self) -> None:
        """Open WebSocket v3 connection to Upstox market-data-feed endpoint.

        NOT YET IMPLEMENTED.

        When implemented, this method will:
        1. Configure upstox_client with the access token.
        2. Open wss://api.upstox.com/v3/feed/market-data-feed.
        3. Register on_open / on_data / on_error / on_close handlers.
        4. Start a daemon thread that calls ws.run_forever().
        5. Wait briefly for the handshake and set self._connected = True.

        Raises:
            NotImplementedError: Always, until implemented.
        """
        raise NotImplementedError(
            "UpstoxFeed.connect() is not yet implemented. "
            "See module docstring for what needs to be built: "
            "OAuth2 flow, WebSocket v3, protobuf frame decoding."
        )

    def disconnect(self) -> None:
        """Close the WebSocket connection gracefully."""
        if self._ws is not None:
            try:
                self._ws.close()
            except Exception as e:
                log.warning("Error closing Upstox WS: %s", e)
        self._connected = False
        log.info("Upstox feed disconnected")

    def subscribe(self, symbols: list[str]) -> None:
        """Subscribe to live ticks for a list of NSE symbols.

        NOT YET IMPLEMENTED.

        When implemented, this method will:
        1. Resolve each symbol name to an Upstox instrument key (NSE_EQ|ISIN).
        2. Send a subscription request over the open WebSocket.
        3. Track subscribed symbols in self._subscribed_symbols.

        Args:
            symbols: List of NSE symbol names, e.g. ["RELIANCE", "INFY"].

        Raises:
            NotImplementedError: Always, until implemented.
        """
        raise NotImplementedError(
            "UpstoxFeed.subscribe() is not yet implemented. "
            "Instrument key resolution (symbol -> NSE_EQ|ISIN) and "
            "WebSocket subscription message format need to be built first."
        )

    def on_tick(self, callback: Callable[[Tick], None]) -> None:
        """Register a callback to be invoked on every decoded tick.

        Args:
            callback: Function accepting a single Tick argument.
        """
        self._tick_callbacks.append(callback)

    def is_connected(self) -> bool:
        """Return True if the WebSocket connection is currently open."""
        return self._connected

    def last_tick_age_seconds(self) -> float:
        """Seconds since the last tick was received (any symbol).

        Used by the stale-feed guardrail. Returns inf if no tick has been
        received yet (feed never connected or no data flowing).
        """
        if self._last_tick_ts == 0.0:
            return float("inf")
        return time.time() - self._last_tick_ts

    # ---------- Class factory ----------

    @classmethod
    def from_env(cls) -> "UpstoxFeed":
        """Construct UpstoxFeed by reading credentials from environment variables.

        Reads:
            UPSTOX_API_KEY       — Upstox API key from developer console.
            UPSTOX_ACCESS_TOKEN  — OAuth2 access token (must be refreshed daily).

        Loads .env file if present (via python-dotenv), so local development works
        without manually exporting vars.

        Returns:
            UpstoxFeed instance ready for connect() to be called.

        Raises:
            ValueError: If either env var is absent or empty.
        """
        load_dotenv(override=False)
        api_key = os.getenv("UPSTOX_API_KEY", "")
        access_token = os.getenv("UPSTOX_ACCESS_TOKEN", "")
        return cls(api_key=api_key, access_token=access_token)

    # ---------- Internal helpers (to be implemented) ----------

    def _on_open(self) -> None:
        """WebSocket open handler — sets self._connected = True."""
        self._connected = True
        log.info("Upstox WS opened")

    def _on_close(self) -> None:
        """WebSocket close handler — marks disconnected."""
        self._connected = False
        log.warning("Upstox WS closed")

    def _on_error(self, error: Exception) -> None:
        """WebSocket error handler."""
        log.error("Upstox WS error: %s", error)
        self._connected = False

    def _on_data(self, raw_bytes: bytes) -> None:
        """Decode a protobuf MarketFullFeed frame and fire tick callbacks.

        NOT YET IMPLEMENTED — needs upstox_client protobuf decoder and
        instrument-key-to-symbol reverse mapping.
        """
        # TODO: decode raw_bytes using upstox_client MarketDataFeedV3 protobuf schema.
        # TODO: map instrument_key back to symbol name via cached instrument master.
        # TODO: construct Tick and call each callback in self._tick_callbacks.
        log.debug("Raw frame received (%d bytes) — decoder not yet implemented", len(raw_bytes))
