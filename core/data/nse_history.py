"""NSE direct historical OHLC client. No-credential fallback for backtests.

Why this exists: yfinance is blocked on the user's network (corporate SSL inspection),
nsepython has a session-cookie bug, but nseindia.com itself is reachable. This client
mimics what a browser does — visits the homepage to seed session cookies, then calls
the equity-historical API with those cookies attached.

Caveats:
  - NSE rate-limits aggressively; we cap requests per minute and pause between calls.
  - The API path can change without notice; we keep this isolated so a fix is one file.
  - Date range > 1 year requires chunking (NSE caps each request at ~365 days).
  - Index history (Nifty 50) uses a different endpoint than equity history.
"""
from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timedelta
from typing import Optional

import pandas as pd
import requests

log = logging.getLogger("agent.nse_history")

NSE_HOMEPAGE = "https://www.nseindia.com"
NSE_EQUITY_HISTORY = "https://www.nseindia.com/api/historical/cm/equity"
NSE_INDEX_HISTORY = "https://www.nseindia.com/api/historical/indicesHistory"

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.nseindia.com/get-quotes/equity?symbol=RELIANCE",
}


class NseHistory:
    """Browser-session-bootstrapped client for NSE equity + index daily history."""

    def __init__(
        self,
        request_pause_seconds: float = 0.5,
        chunk_days: int = 360,
        timeout_seconds: float = 10.0,
    ):
        self._session: Optional[requests.Session] = None
        self._session_seeded_at: float = 0.0
        self._lock = threading.Lock()
        self._request_pause = request_pause_seconds
        self._chunk_days = chunk_days
        self._timeout = timeout_seconds

    # ---------- public API ----------

    def fetch_equity_daily(self, symbol: str, days: int) -> Optional[pd.DataFrame]:
        """Daily OHLC for an NSE EQ symbol over the last `days` calendar days."""
        end = datetime.now().date()
        start = end - timedelta(days=days)
        return self._fetch_equity_range(symbol, start, end)

    def fetch_nifty_index_daily(self, days: int = 365) -> Optional[pd.DataFrame]:
        """Daily OHLC for the Nifty 50 index. Uses a separate endpoint."""
        end = datetime.now().date()
        start = end - timedelta(days=days)
        return self._fetch_index_range("NIFTY 50", start, end)

    # ---------- internals ----------

    def _ensure_session(self) -> requests.Session:
        with self._lock:
            now = time.time()
            stale = (now - self._session_seeded_at) > 300  # re-seed every 5 min
            if self._session is None or stale:
                s = requests.Session()
                s.headers.update(DEFAULT_HEADERS)
                # Visit homepage to receive the cookies the historical APIs validate against.
                r = s.get(NSE_HOMEPAGE, timeout=self._timeout)
                r.raise_for_status()
                # Visit the equity quote page too — sets additional cookies the API checks.
                s.get(
                    "https://www.nseindia.com/get-quotes/equity?symbol=RELIANCE",
                    timeout=self._timeout,
                )
                self._session = s
                self._session_seeded_at = now
            return self._session

    def _fetch_equity_range(
        self, symbol: str, start: datetime.date, end: datetime.date
    ) -> Optional[pd.DataFrame]:
        chunks: list[pd.DataFrame] = []
        chunk_end = end
        while chunk_end > start:
            chunk_start = max(start, chunk_end - timedelta(days=self._chunk_days))
            df = self._fetch_equity_one_chunk(symbol, chunk_start, chunk_end)
            if df is not None and not df.empty:
                chunks.append(df)
            chunk_end = chunk_start - timedelta(days=1)
            time.sleep(self._request_pause)
        if not chunks:
            return None
        out = pd.concat(chunks).sort_index()
        out = out[~out.index.duplicated(keep="first")]
        return out

    def _fetch_equity_one_chunk(
        self, symbol: str, start: datetime.date, end: datetime.date
    ) -> Optional[pd.DataFrame]:
        s = self._ensure_session()
        params = {
            "symbol": symbol,
            "series": '["EQ"]',
            "from": start.strftime("%d-%m-%Y"),
            "to": end.strftime("%d-%m-%Y"),
        }
        try:
            r = s.get(NSE_EQUITY_HISTORY, params=params, timeout=self._timeout)
            if r.status_code == 401:
                # Session expired mid-flight — re-seed once and retry
                self._session = None
                s = self._ensure_session()
                r = s.get(NSE_EQUITY_HISTORY, params=params, timeout=self._timeout)
            r.raise_for_status()
            data = r.json().get("data") or []
        except (requests.RequestException, ValueError) as e:
            log.warning("NSE equity fetch failed for %s %s..%s: %s",
                        symbol, start, end, e)
            return None
        if not data:
            return None
        df = pd.DataFrame(data)
        col_map = {
            "CH_TIMESTAMP": "ts",
            "CH_OPENING_PRICE": "open",
            "CH_TRADE_HIGH_PRICE": "high",
            "CH_TRADE_LOW_PRICE": "low",
            "CH_CLOSING_PRICE": "close",
            "CH_TOT_TRADED_QTY": "volume",
        }
        missing = [c for c in col_map if c not in df.columns]
        if missing:
            log.warning("Unexpected NSE response shape (missing %s)", missing)
            return None
        df = df.rename(columns=col_map)[list(col_map.values())]
        df["ts"] = pd.to_datetime(df["ts"])
        df = df.set_index("ts").sort_index()
        df = df.astype({
            "open": "float64", "high": "float64",
            "low": "float64", "close": "float64", "volume": "int64",
        })
        return df

    def _fetch_index_range(
        self, index_name: str, start: datetime.date, end: datetime.date
    ) -> Optional[pd.DataFrame]:
        chunks: list[pd.DataFrame] = []
        chunk_end = end
        while chunk_end > start:
            chunk_start = max(start, chunk_end - timedelta(days=self._chunk_days))
            df = self._fetch_index_one_chunk(index_name, chunk_start, chunk_end)
            if df is not None and not df.empty:
                chunks.append(df)
            chunk_end = chunk_start - timedelta(days=1)
            time.sleep(self._request_pause)
        if not chunks:
            return None
        out = pd.concat(chunks).sort_index()
        out = out[~out.index.duplicated(keep="first")]
        return out

    def _fetch_index_one_chunk(
        self, index_name: str, start: datetime.date, end: datetime.date
    ) -> Optional[pd.DataFrame]:
        s = self._ensure_session()
        params = {
            "indexType": index_name,
            "from": start.strftime("%d-%m-%Y"),
            "to": end.strftime("%d-%m-%Y"),
        }
        try:
            r = s.get(NSE_INDEX_HISTORY, params=params, timeout=self._timeout)
            if r.status_code == 401:
                self._session = None
                s = self._ensure_session()
                r = s.get(NSE_INDEX_HISTORY, params=params, timeout=self._timeout)
            r.raise_for_status()
            payload = r.json()
            data = payload.get("data") or payload.get("indexCloseOnlineRecords") or []
        except (requests.RequestException, ValueError) as e:
            log.warning("NSE index fetch failed for %s %s..%s: %s",
                        index_name, start, end, e)
            return None
        if not data:
            return None
        df = pd.DataFrame(data)
        # Schema variations across endpoints — handle both common shapes.
        if "EOD_OPEN_INDEX_VAL" in df.columns:
            col_map = {
                "EOD_TIMESTAMP": "ts",
                "EOD_OPEN_INDEX_VAL": "open",
                "EOD_HIGH_INDEX_VAL": "high",
                "EOD_LOW_INDEX_VAL": "low",
                "EOD_CLOSE_INDEX_VAL": "close",
            }
            df = df.rename(columns=col_map)[list(col_map.values())]
            df["volume"] = 0
        elif "HistoricalDate" in df.columns:
            df = df.rename(columns={
                "HistoricalDate": "ts",
                "OPEN": "open", "HIGH": "high", "LOW": "low", "CLOSE": "close",
            })
            df["volume"] = 0
            df = df[["ts", "open", "high", "low", "close", "volume"]]
        else:
            log.warning("Unknown index response shape: %s", list(df.columns)[:6])
            return None
        df["ts"] = pd.to_datetime(df["ts"])
        df = df.set_index("ts").sort_index()
        df = df.astype({"open": "float64", "high": "float64",
                        "low": "float64", "close": "float64", "volume": "int64"})
        return df
