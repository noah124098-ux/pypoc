"""Angel One SmartAPI live data feed. DATA-ONLY — never used for orders.

SECURITY CONSTRAINT: Angel One credentials in this codebase are used
EXCLUSIVELY for market data (live ticks, instrument master). They are NEVER
used for placing, modifying, or cancelling orders. See angelone_history.py
for the full constraint statement and order-method guard.

Authenticates with API key + client code + password + TOTP, then opens a
SmartWebSocketV2 connection for live ticks. Implements auto-reconnect with
exponential backoff and exposes ticks via the ILiveFeed contract.

Symbol-token mapping: Angel One uses numeric tokens, not symbol names. We resolve via
their instrument-master CSV (cached daily).
"""
from __future__ import annotations

import csv
import logging
import threading
import time
from datetime import datetime
from io import StringIO
from pathlib import Path
from typing import Callable, Optional

from core.data.feed_base import ILiveFeed
from core.types import Tick

log = logging.getLogger("agent.angelone")

INSTRUMENT_MASTER_URL = "https://margincalculator.angelbroking.com/OpenAPI_File/files/OpenAPIScripMaster.json"


class AngelOneFeed(ILiveFeed):
    def __init__(
        self,
        api_key: str,
        client_code: str,
        password: str,
        totp_secret: str,
        reconnect_max_attempts: int = 5,
        reconnect_backoff_seconds: int = 2,
        cache_dir: str = "data/cache",
    ):
        if not all([api_key, client_code, password, totp_secret]):
            raise ValueError(
                "Angel One credentials missing. Set ANGEL_ONE_API_KEY / "
                "ANGEL_ONE_CLIENT_CODE / ANGEL_ONE_PASSWORD / ANGEL_ONE_TOTP_SECRET in .env"
            )
        self._api_key = api_key
        self._client_code = client_code
        self._password = password
        self._totp_secret = totp_secret
        self._reconnect_max = reconnect_max_attempts
        self._reconnect_backoff = reconnect_backoff_seconds
        self._cache_dir = Path(cache_dir)
        self._cache_dir.mkdir(parents=True, exist_ok=True)

        self._smart_api = None
        self._sws = None
        self._connected = False
        self._auth_token: Optional[str] = None
        self._feed_token: Optional[str] = None
        self._last_tick_ts: float = 0.0
        self._tick_callbacks: list[Callable[[Tick], None]] = []
        self._symbol_to_token: dict[str, str] = {}
        self._token_to_symbol: dict[str, str] = {}
        self._subscribed_symbols: list[str] = []
        self._lock = threading.Lock()

    # ---------- ILiveFeed ----------

    def connect(self) -> None:
        self._login_with_retry()
        self._load_instrument_master()
        self._open_websocket()

    def disconnect(self) -> None:
        try:
            if self._sws:
                self._sws.close_connection()
        except Exception as e:
            log.warning("Error closing WS: %s", e)
        self._connected = False

    def subscribe(self, symbols: list[str]) -> None:
        if not self._sws:
            raise RuntimeError("Feed not connected. Call connect() first.")
        token_list = []
        resolved = []
        for s in symbols:
            tok = self._symbol_to_token.get(s)
            if tok is None:
                log.warning("No token found for symbol %s — skipping", s)
                continue
            token_list.append(tok)
            resolved.append(s)
        if not token_list:
            log.error("No subscribable symbols resolved")
            return

        # Mode 2 = Quote (LTP + bid/ask + volume). Mode 3 = SnapQuote (deeper book).
        subscribe_payload = {
            "action": 1,
            "params": {
                "mode": 2,
                "tokenList": [{"exchangeType": 1, "tokens": token_list}],  # 1 = NSE_CM
            },
        }
        self._sws.subscribe("nse_subscribe", 2, [{"exchangeType": 1, "tokens": token_list}])
        self._subscribed_symbols.extend(resolved)
        log.info("Subscribed to %d symbols", len(resolved))

    def on_tick(self, callback: Callable[[Tick], None]) -> None:
        self._tick_callbacks.append(callback)

    def is_connected(self) -> bool:
        return self._connected

    def last_tick_age_seconds(self) -> float:
        if self._last_tick_ts == 0.0:
            return float("inf")
        return time.time() - self._last_tick_ts

    # ---------- Internals ----------

    def _neutralize_order_methods(self) -> None:
        """Refuse to even hold a reference to order-placing SDK methods.

        DATA-ONLY contract: this codebase does not place orders via Angel One.
        Future refactors that try to call placeOrder/modifyOrder/cancelOrder via
        this object will fail loudly instead of silently transmitting orders.
        """
        for name in ("placeOrder", "modifyOrder", "cancelOrder", "placeOrderFullResponse"):
            if self._smart_api is not None and hasattr(self._smart_api, name):
                setattr(self._smart_api, name, _refuse_order_call)

    def _login_with_retry(self) -> None:
        import pyotp
        from SmartApi import SmartConnect

        attempt = 0
        while attempt < self._reconnect_max:
            try:
                self._smart_api = SmartConnect(api_key=self._api_key)
                totp = pyotp.TOTP(self._totp_secret).now()
                resp = self._smart_api.generateSession(self._client_code, self._password, totp)
                if not resp.get("status"):
                    raise RuntimeError(f"Login failed: {resp.get('message')}")
                self._auth_token = resp["data"]["jwtToken"]
                self._feed_token = self._smart_api.getfeedToken()
                self._neutralize_order_methods()
                log.info("Angel One login OK for client %s — DATA-ONLY mode", self._client_code)
                return
            except Exception as e:
                attempt += 1
                wait = self._reconnect_backoff * (2 ** (attempt - 1))
                log.warning("Login attempt %d failed: %s — retrying in %ds", attempt, e, wait)
                time.sleep(wait)
        raise RuntimeError("Angel One login failed after retries")

    def _load_instrument_master(self) -> None:
        """Download (cached daily) instrument master and build symbol->token map for NSE EQ."""
        import requests

        cache_file = self._cache_dir / f"angel_instruments_{datetime.now().strftime('%Y%m%d')}.json"
        if cache_file.exists():
            import json
            data = json.loads(cache_file.read_text(encoding="utf-8"))
        else:
            log.info("Downloading Angel One instrument master ...")
            r = requests.get(INSTRUMENT_MASTER_URL, timeout=30)
            r.raise_for_status()
            data = r.json()
            cache_file.write_text(r.text, encoding="utf-8")

        for inst in data:
            if inst.get("exch_seg") == "NSE" and inst.get("instrumenttype") in ("", "EQ"):
                # Symbol typically appears as "RELIANCE-EQ"
                sym = inst.get("symbol", "").replace("-EQ", "")
                tok = str(inst.get("token", ""))
                if sym and tok:
                    self._symbol_to_token[sym] = tok
                    self._token_to_symbol[tok] = sym
        log.info("Loaded %d NSE EQ instruments", len(self._symbol_to_token))

    def _open_websocket(self) -> None:
        from SmartApi.smartWebSocketV2 import SmartWebSocketV2

        self._sws = SmartWebSocketV2(
            auth_token=self._auth_token,
            api_key=self._api_key,
            client_code=self._client_code,
            feed_token=self._feed_token,
        )
        self._sws.on_open = self._on_open
        self._sws.on_data = self._on_data
        self._sws.on_error = self._on_error
        self._sws.on_close = self._on_close

        t = threading.Thread(target=self._sws.connect, daemon=True, name="angelone-ws")
        t.start()
        # Wait for the open handshake briefly
        for _ in range(20):
            if self._connected:
                break
            time.sleep(0.5)

    def _on_open(self, _wsapp):
        self._connected = True
        log.info("Angel One WS opened")

    def _on_data(self, _wsapp, message):
        # SmartWebSocketV2 decodes binary frames into a dict for us.
        try:
            token = str(message.get("token", ""))
            symbol = self._token_to_symbol.get(token)
            if not symbol:
                return
            ltp = float(message.get("last_traded_price", 0)) / 100.0  # paise -> rupees
            best_bid = float(message.get("best_5_buy_data", [{}])[0].get("price", ltp * 100)) / 100.0 \
                if message.get("best_5_buy_data") else ltp
            best_ask = float(message.get("best_5_sell_data", [{}])[0].get("price", ltp * 100)) / 100.0 \
                if message.get("best_5_sell_data") else ltp
            volume = int(message.get("volume_trade_for_the_day", 0))

            tick = Tick(
                symbol=symbol,
                ltp=ltp,
                bid=best_bid,
                ask=best_ask,
                volume=volume,
                ts=datetime.utcnow(),
            )
            with self._lock:
                self._last_tick_ts = time.time()
            for cb in self._tick_callbacks:
                try:
                    cb(tick)
                except Exception as e:
                    log.exception("Tick callback raised: %s", e)
        except Exception as e:
            log.exception("Error processing tick message: %s", e)

    def _on_error(self, _wsapp, err):
        log.error("Angel One WS error: %s", err)
        self._connected = False

    def _on_close(self, _wsapp):
        log.warning("Angel One WS closed")
        self._connected = False


def _refuse_order_call(*_args, **_kwargs):
    raise RuntimeError(
        "Angel One credentials in this codebase are configured DATA-ONLY. "
        "Order placement is intentionally disabled. "
        "If you need live order execution, add a separate broker integration "
        "with its own credentials."
    )
