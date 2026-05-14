"""Angel One SmartAPI historical OHLC client. DATA-ONLY — never used for orders.

SECURITY CONSTRAINT (enforced by `_assert_no_order_methods` below):
  Angel One credentials in this codebase are used EXCLUSIVELY for market data
  (candle history, instrument master, live ticks). They are NEVER used for
  placing, modifying, or cancelling orders. Order execution always goes through
  PaperBroker (in-memory) until a deliberate, separately-credentialed live broker
  integration is added in a future phase.

Uses the same login flow as the live feed (api_key + client_code + password + TOTP),
then calls `getCandleData` to fetch OHLC. Same data source as the live feed,
so backtests and live trading don't drift.
"""
from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import pandas as pd

log = logging.getLogger("agent.angelone_history")

INSTRUMENT_MASTER_URL = (
    "https://margincalculator.angelbroking.com/OpenAPI_File/files/OpenAPIScripMaster.json"
)
ANGEL_INTERVAL_MAP = {
    "1m": "ONE_MINUTE",
    "5m": "FIVE_MINUTE",
    "15m": "FIFTEEN_MINUTE",
    "30m": "THIRTY_MINUTE",
    "1h": "ONE_HOUR",
    "1d": "ONE_DAY",
}


class AngelOneHistory:
    """Authenticated client for Angel One getCandleData.

    Lazy login: only authenticates on first fetch. Reuses one SmartConnect
    session for the lifetime of the instance. Caller is responsible for
    creating a new instance if credentials change.
    """

    def __init__(
        self,
        api_key: str,
        client_code: str,
        password: str,
        totp_secret: str,
        cache_dir: str | Path = "data/cache",
        request_pause_seconds: float = 0.4,
    ):
        if not all([api_key, client_code, password, totp_secret]):
            raise ValueError(
                "Angel One credentials missing. Set ANGEL_ONE_API_KEY / "
                "ANGEL_ONE_CLIENT_CODE / ANGEL_ONE_PASSWORD / "
                "ANGEL_ONE_TOTP_SECRET in .env"
            )
        self._api_key = api_key
        self._client_code = client_code
        self._password = password
        self._totp_secret = totp_secret
        self._cache_dir = Path(cache_dir)
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        self._smart_api = None
        self._symbol_to_token: dict[str, str] = {}
        self._request_pause = request_pause_seconds

    # ---------- public API ----------

    def fetch_daily(self, symbol: str, days: int) -> Optional[pd.DataFrame]:
        return self.fetch_candles(symbol, interval="1d", days=days)

    def fetch_candles(
        self, symbol: str, *, interval: str, days: int
    ) -> Optional[pd.DataFrame]:
        if interval not in ANGEL_INTERVAL_MAP:
            raise ValueError(
                f"Unsupported interval {interval}. Allowed: {list(ANGEL_INTERVAL_MAP)}"
            )
        self._ensure_login()
        token = self._resolve_token(symbol)
        if token is None:
            log.warning("No token found for %s — skipping", symbol)
            return None

        end = datetime.now()
        start = end - timedelta(days=days)
        params = {
            "exchange": "NSE",
            "symboltoken": token,
            "interval": ANGEL_INTERVAL_MAP[interval],
            "fromdate": start.strftime("%Y-%m-%d %H:%M"),
            "todate": end.strftime("%Y-%m-%d %H:%M"),
        }

        try:
            resp = self._smart_api.getCandleData(params)
        except Exception as e:
            log.warning("getCandleData raised for %s: %s", symbol, e)
            return None
        finally:
            time.sleep(self._request_pause)  # respect rate limit

        if not resp or not resp.get("status"):
            log.warning("getCandleData failed for %s: %s", symbol, resp)
            return None

        data = resp.get("data") or []
        if not data:
            return None

        # Each row is [timestamp, open, high, low, close, volume]
        df = pd.DataFrame(data, columns=["ts", "open", "high", "low", "close", "volume"])
        df["ts"] = pd.to_datetime(df["ts"])
        df = df.set_index("ts").sort_index()
        df = df.astype({
            "open": "float64", "high": "float64",
            "low": "float64", "close": "float64", "volume": "int64",
        })
        return df

    # ---------- internals ----------

    def _ensure_login(self) -> None:
        if self._smart_api is not None:
            return
        import pyotp
        from SmartApi import SmartConnect

        self._smart_api = SmartConnect(api_key=self._api_key)
        totp = pyotp.TOTP(self._totp_secret).now()
        resp = self._smart_api.generateSession(self._client_code, self._password, totp)
        if not resp.get("status"):
            raise RuntimeError(f"Angel One login failed: {resp.get('message')}")
        log.info("Angel One historical client logged in (%s) — DATA-ONLY mode", self._client_code)
        self._assert_no_order_methods()

    def _assert_no_order_methods(self) -> None:
        """Defensive: refuse to even hold a reference to order-placing SDK methods.

        If a future change tries to call placeOrder/modifyOrder/cancelOrder via this
        object, we want it to fail loudly. We check method names, not their behaviour,
        so the data-only contract holds even before any order is attempted.
        """
        forbidden = {"placeOrder", "modifyOrder", "cancelOrder", "placeOrderFullResponse"}
        for name in forbidden:
            if hasattr(self._smart_api, name):
                # Replace order methods with a guard that raises if ever called.
                setattr(self._smart_api, name, _refuse_order_call)

    def _resolve_token(self, symbol: str) -> Optional[str]:
        if not self._symbol_to_token:
            self._load_instrument_master()
        return self._symbol_to_token.get(symbol)

    def _load_instrument_master(self) -> None:
        """Load instrument master, cached daily. Same payload format as the live feed."""
        import requests

        cache_file = self._cache_dir / f"angel_instruments_{datetime.now().strftime('%Y%m%d')}.json"
        if cache_file.exists():
            data = json.loads(cache_file.read_text(encoding="utf-8"))
        else:
            log.info("Downloading Angel One instrument master ...")
            r = requests.get(INSTRUMENT_MASTER_URL, timeout=30)
            r.raise_for_status()
            data = r.json()
            cache_file.write_text(r.text, encoding="utf-8")

        for inst in data:
            if inst.get("exch_seg") == "NSE" and inst.get("instrumenttype") in ("", "EQ"):
                sym = inst.get("symbol", "").replace("-EQ", "")
                tok = str(inst.get("token", ""))
                if sym and tok:
                    self._symbol_to_token[sym] = tok
        log.info("Loaded %d NSE EQ instruments", len(self._symbol_to_token))


def _refuse_order_call(*_args, **_kwargs):
    raise RuntimeError(
        "Angel One credentials in this codebase are configured DATA-ONLY. "
        "Order placement is intentionally disabled. "
        "If you need live order execution, add a separate broker integration "
        "with its own credentials."
    )
