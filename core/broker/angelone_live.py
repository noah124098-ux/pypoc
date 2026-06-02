"""Angel One live broker — full order-execution integration via SmartAPI.

SECURITY DESIGN:
  - Uses SEPARATE credentials (ANGEL_ONE_LIVE_*) from the data-feed app
    (ANGEL_ONE_API_KEY / ANGEL_ONE_CLIENT_CODE / ...).
  - The data-feed credentials MUST NOT be passed here. Different apps, different
    permissions, different env vars.
  - Refuses to initialise if ANGEL_ONE_LIVE_API_KEY is empty OR if it equals
    the data-feed ANGEL_ONE_API_KEY (cross-contamination guard).
  - Credentials are stored internally and NEVER logged.

DEPLOYMENT CHECKLIST before enabling live capital:
  1. Create a separate Angel One app with order permissions (NEVER the data-feed app).
  2. Set ANGEL_ONE_LIVE_* env vars to the new app's credentials.
  3. Run exhaustive integration tests on Angel One paper-trade environment.
  4. Backtest gate must pass (Sharpe >= 1.2) on a fresh walk-forward (< 30 days old).
  5. Obtain explicit user approval to flip mode to live.
"""
from __future__ import annotations

import json
import logging
import os
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional

from core.broker.base import IBroker
from core.config import ExecutionCfg
from core.types import Order, OrderStatus, OrderType, Position, Side

log = logging.getLogger("agent.angelone_live")

# Environment variable names for live-broker credentials.
# These MUST be different from the data-feed vars (ANGEL_ONE_API_KEY etc.).
LIVE_API_KEY_ENV = "ANGEL_ONE_LIVE_API_KEY"
LIVE_CLIENT_CODE_ENV = "ANGEL_ONE_LIVE_CLIENT_CODE"
LIVE_PASSWORD_ENV = "ANGEL_ONE_LIVE_PASSWORD"
LIVE_TOTP_SECRET_ENV = "ANGEL_ONE_LIVE_TOTP_SECRET"

# Data-feed env var names — rejected at startup if mistakenly passed.
_DATA_FEED_API_KEY_ENV = "ANGEL_ONE_API_KEY"

# Instrument master URL (same as data feed; cached daily under data/cache/).
_INSTRUMENT_MASTER_URL = (
    "https://margincalculator.angelbroking.com/OpenAPI_File/files/OpenAPIScripMaster.json"
)


class AngelOneLiveBroker(IBroker):
    """Live Angel One broker. Places real orders via SmartAPI.

    Security contract:
    - Credentials accepted here must come from ANGEL_ONE_LIVE_* env vars only.
    - The data-feed app credentials (ANGEL_ONE_API_KEY etc.) must NEVER be used here.
    - On __init__, verify api_key is NOT empty and NOT equal to ANGEL_ONE_API_KEY.
    - Credentials are stored internally and never logged.

    Requires ANGEL_ONE_LIVE_API_KEY / ANGEL_ONE_LIVE_CLIENT_CODE /
    ANGEL_ONE_LIVE_PASSWORD / ANGEL_ONE_LIVE_TOTP_SECRET in .env.

    Call connect() before any trading methods.
    """

    def __init__(
        self,
        api_key: str,
        client_code: str,
        password: str,
        totp_secret: str,
        exec_cfg: ExecutionCfg,
        cache_dir: str | Path = "data/cache",
    ) -> None:
        if not api_key:
            raise ValueError(
                f"Live broker requires {LIVE_API_KEY_ENV} to be set. "
                "Do NOT reuse the data-feed API key (ANGEL_ONE_API_KEY). "
                "Create a separate Angel One app with order permissions."
            )
        # Cross-contamination guard: reject if someone accidentally passes data-feed creds.
        _data_feed_key = os.getenv(_DATA_FEED_API_KEY_ENV, "")
        if _data_feed_key and api_key == _data_feed_key:
            raise ValueError(
                f"The api_key passed to AngelOneLiveBroker matches {_DATA_FEED_API_KEY_ENV}. "
                "You MUST use a SEPARATE Angel One app for order execution. "
                "The data-feed credentials are DATA-ONLY and must never be used for trading."
            )

        # Store credentials — never log them.
        self._api_key = api_key
        self._client_code = client_code
        self._password = password
        self._totp_secret = totp_secret
        self._exec = exec_cfg
        self._cache_dir = Path(cache_dir)
        self._cache_dir.mkdir(parents=True, exist_ok=True)

        self._smart_api = None
        self._connected: bool = False
        self._cash: float = 0.0
        self._equity: float = 0.0

        # Instrument master: symbol -> token (populated on connect / lazily).
        self._symbol_to_token: dict[str, str] = {}

        log.info(
            "AngelOneLiveBroker initialised for client %s — call connect() before trading.",
            self._client_code,
        )

    # ------------------------------------------------------------------
    # Connection lifecycle
    # ------------------------------------------------------------------

    def connect(self) -> None:
        """Authenticate with Angel One using TOTP, populate account balance.

        Must be called before any order or position methods.
        """
        import pyotp
        from SmartApi import SmartConnect

        log.info("AngelOneLiveBroker: connecting for client %s", self._client_code)
        self._smart_api = SmartConnect(api_key=self._api_key)
        totp = pyotp.TOTP(self._totp_secret).now()
        resp = self._smart_api.generateSession(self._client_code, self._password, totp)
        if not resp.get("status"):
            raise RuntimeError(
                f"Angel One live-broker login failed: {resp.get('message')}"
            )
        log.info("AngelOneLiveBroker: session established for %s", self._client_code)
        self._connected = True
        self._load_instrument_master()
        self._refresh_balance()

    def disconnect(self) -> None:
        """Terminate the Angel One session."""
        if self._smart_api is not None:
            try:
                self._smart_api.terminateSession(self._client_code)
            except Exception as exc:
                log.warning("AngelOneLiveBroker: terminateSession raised: %s", exc)
        self._connected = False
        self._smart_api = None
        log.info("AngelOneLiveBroker: disconnected.")

    # ------------------------------------------------------------------
    # IBroker interface — full implementations
    # ------------------------------------------------------------------

    def place_order(
        self,
        symbol: str,
        side: Side,
        qty: int,
        order_type: OrderType,
        stop_loss: float,
        target: Optional[float],
        limit_price: Optional[float] = None,
        strategy: str = "",
    ) -> Order:
        """Place an order via SmartAPI. Returns an Order with FILLED or REJECTED status.

        On SmartAPI exceptions the order is returned with REJECTED status (not raised)
        so the caller (orchestrator / guardrails) can record the failure cleanly.
        """
        order = Order(
            id=str(uuid.uuid4()),
            symbol=symbol,
            side=side,
            qty=qty,
            order_type=order_type,
            limit_price=limit_price,
            stop_loss=stop_loss,
            target=target,
            strategy=strategy,
        )

        if not self._connected or self._smart_api is None:
            order.status = OrderStatus.REJECTED
            order.rejection_reason = "broker_not_connected"
            log.error("place_order called but broker not connected. Order rejected.")
            return order

        try:
            token = self._get_token(symbol)
        except ValueError as exc:
            order.status = OrderStatus.REJECTED
            order.rejection_reason = str(exc)
            log.warning("place_order: token lookup failed for %s: %s", symbol, exc)
            return order

        # Map our enums to Angel One API strings.
        ao_side = "BUY" if side == Side.BUY else "SELL"
        ao_order_type = "MARKET" if order_type == OrderType.MARKET else "LIMIT"

        payload = {
            "variety": "NORMAL",
            "tradingsymbol": symbol,
            "symboltoken": token,
            "transactiontype": ao_side,
            "exchange": "NSE",
            "ordertype": ao_order_type,
            "producttype": "INTRADAY",
            "duration": "DAY",
            "quantity": str(qty),
        }
        if order_type == OrderType.LIMIT and limit_price is not None:
            payload["price"] = str(limit_price)

        try:
            resp = self._smart_api.placeOrder(payload)
            log.debug("placeOrder response: %s", resp)
        except Exception as exc:
            order.status = OrderStatus.REJECTED
            order.rejection_reason = f"smartapi_exception: {exc}"
            log.warning(
                "place_order: SmartAPI exception for %s %s x%d: %s",
                ao_side, symbol, qty, exc,
            )
            return order

        if not resp or not resp.get("status"):
            msg = (resp or {}).get("message", "unknown_error")
            order.status = OrderStatus.REJECTED
            order.rejection_reason = f"smartapi_rejected: {msg}"
            log.warning(
                "place_order: SmartAPI rejected %s %s x%d — %s",
                ao_side, symbol, qty, msg,
            )
            return order

        # Successful order placement.
        order_id = (resp.get("data") or {}).get("orderid", order.id)
        order.id = str(order_id)
        order.status = OrderStatus.FILLED
        # Angel One MARKET orders fill at market price; we record zero here since
        # we don't have a real-time fill price in the synchronous response.
        # The orchestrator should call update_market_prices() to track costs.
        order.filled_qty = qty
        log.info(
            "place_order: %s %s x%d placed — order_id=%s",
            ao_side, symbol, qty, order.id,
        )
        return order

    def cancel_order(self, order_id: str) -> bool:
        """Cancel a pending order. Returns True on success, False otherwise."""
        if not self._connected or self._smart_api is None:
            log.error("cancel_order called but broker not connected.")
            return False
        try:
            resp = self._smart_api.cancelOrder(order_id, "NORMAL")
            success = bool(resp and resp.get("status"))
            if not success:
                log.warning(
                    "cancel_order: SmartAPI returned non-success for %s: %s", order_id, resp
                )
            return success
        except Exception as exc:
            log.warning("cancel_order: SmartAPI exception for %s: %s", order_id, exc)
            return False

    def get_positions(self) -> list[Position]:
        """Fetch open positions from Angel One and map to Position dataclass list."""
        if not self._connected or self._smart_api is None:
            log.warning("get_positions called but broker not connected — returning []")
            return []
        try:
            resp = self._smart_api.getPosition()
        except Exception as exc:
            log.warning("get_positions: SmartAPI exception: %s", exc)
            return []

        if not resp or not resp.get("status"):
            log.warning("get_positions: SmartAPI returned non-success: %s", resp)
            return []

        raw_positions = resp.get("data") or []
        positions: list[Position] = []
        for raw in raw_positions:
            try:
                qty = int(raw.get("netqty", 0))
                if qty == 0:
                    continue  # flat position — skip
                avg_price = float(raw.get("netavgprice", 0.0))
                symbol = str(raw.get("tradingsymbol", "")).replace("-EQ", "")
                ltp = float(raw.get("ltp", avg_price))
                positions.append(
                    Position(
                        symbol=symbol,
                        qty=abs(qty),
                        avg_price=avg_price,
                        stop_loss=0.0,  # stop-loss managed by guardrails, not broker
                        target=None,
                        strategy="",
                        opened_at=datetime.utcnow(),
                        last_price=ltp,
                    )
                )
            except (ValueError, TypeError, KeyError) as exc:
                log.warning("get_positions: failed to parse position row %s: %s", raw, exc)
        return positions

    def get_position(self, symbol: str) -> Optional[Position]:
        """Return the open position for a symbol, or None if flat."""
        for pos in self.get_positions():
            if pos.symbol == symbol or pos.symbol == symbol.replace("-EQ", ""):
                return pos
        return None

    def equity(self) -> float:
        """Total account equity (net available + used margin + unrealised P&L).

        Calls getRMS() to get the current margin state.
        Falls back to cached value if API call fails.
        """
        self._refresh_balance()
        return self._equity

    def cash(self) -> float:
        """Available cash (net available balance from RMS).

        Falls back to cached value if API call fails.
        """
        self._refresh_balance()
        return self._cash

    def update_market_prices(self, prices: dict[str, float]) -> None:
        """No-op for the live broker.

        Market prices come from the live AngelOneFeed tick stream, not the broker.
        Positions' last_price is updated by the orchestrator directly.
        """
        # Intentionally a no-op. Prices are pushed by the live feed, not polled here.

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _refresh_balance(self) -> None:
        """Fetch current margin/balance from getRMS. Updates self._cash / self._equity."""
        if not self._connected or self._smart_api is None:
            return
        try:
            resp = self._smart_api.getRMS()
        except Exception as exc:
            log.warning("_refresh_balance: getRMS raised: %s", exc)
            return

        if not resp or not resp.get("status"):
            log.warning("_refresh_balance: getRMS returned non-success: %s", resp)
            return

        data = resp.get("data") or {}
        try:
            self._cash = float(data.get("availablecash", self._cash))
            # net is total equity (cash + positions); fall back to cash if absent.
            self._equity = float(data.get("net", self._cash))
        except (ValueError, TypeError) as exc:
            log.warning("_refresh_balance: could not parse RMS data: %s", exc)

    def _get_token(self, symbol: str) -> str:
        """Look up the numeric token for a symbol in the cached instrument master.

        Raises ValueError if symbol is not found.
        """
        if not self._symbol_to_token:
            self._load_instrument_master()
        token = self._symbol_to_token.get(symbol)
        if token is None:
            # Try with -EQ suffix (Angel One normalisation).
            token = self._symbol_to_token.get(symbol + "-EQ")
        if token is None:
            raise ValueError(
                f"Symbol {symbol} not in instrument master. "
                "Refresh the cache or check the symbol name."
            )
        return token

    def _load_instrument_master(self) -> None:
        """Load NSE EQ instrument master from cache (downloaded daily by the data feed).

        Reuses the same cached file written by AngelOneFeed / AngelOneHistory to
        avoid redundant downloads. Falls back to downloading if cache is absent.
        """
        import requests

        cache_file = self._cache_dir / f"angel_instruments_{datetime.now().strftime('%Y%m%d')}.json"
        if cache_file.exists():
            data = json.loads(cache_file.read_text(encoding="utf-8"))
        else:
            log.info("AngelOneLiveBroker: downloading instrument master ...")
            try:
                r = requests.get(_INSTRUMENT_MASTER_URL, timeout=30)
                r.raise_for_status()
                data = r.json()
                cache_file.write_text(r.text, encoding="utf-8")
            except Exception as exc:
                log.warning(
                    "AngelOneLiveBroker: instrument master download failed: %s", exc
                )
                self._symbol_to_token = {}
                return

        self._symbol_to_token.clear()
        for inst in data:
            if inst.get("exch_seg") == "NSE" and inst.get("instrumenttype") in ("", "EQ"):
                sym = inst.get("symbol", "").replace("-EQ", "")
                tok = str(inst.get("token", ""))
                if sym and tok:
                    self._symbol_to_token[sym] = tok
        log.info(
            "AngelOneLiveBroker: loaded %d NSE EQ instruments", len(self._symbol_to_token)
        )

    # ------------------------------------------------------------------
    # Factory
    # ------------------------------------------------------------------

    @classmethod
    def from_env(cls, exec_cfg: ExecutionCfg) -> "AngelOneLiveBroker":
        """Construct from ANGEL_ONE_LIVE_* environment variables.

        Will raise ValueError if ANGEL_ONE_LIVE_API_KEY is not set.
        Uses dotenv if .env is present, but does NOT fall back to the
        data-feed variables (ANGEL_ONE_API_KEY etc.).
        """
        from dotenv import load_dotenv

        load_dotenv(override=False)
        return cls(
            api_key=os.getenv(LIVE_API_KEY_ENV, ""),
            client_code=os.getenv(LIVE_CLIENT_CODE_ENV, ""),
            password=os.getenv(LIVE_PASSWORD_ENV, ""),
            totp_secret=os.getenv(LIVE_TOTP_SECRET_ENV, ""),
            exec_cfg=exec_cfg,
        )
