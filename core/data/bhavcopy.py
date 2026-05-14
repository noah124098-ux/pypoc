"""NSE Bhavcopy historical OHLC loader. No-credential, network-friendly.

NSE publishes a daily 'sec_bhavdata_full' CSV at archives.nseindia.com containing
OHLCV for every NSE equity symbol. Unlike www.nseindia.com (which has bot protection),
the archives subdomain serves files freely.

Strategy:
  - Pull one CSV per trading day, cache to disk.
  - Index by (symbol, date) for fast lookup.
  - Build per-symbol DataFrames on demand by stitching daily rows.
  - All NSE EQ symbols come from one file, so a single backtest uses ~250 files
    for 1 year of history (~85 MB), all cacheable indefinitely.

Caveats:
  - Filename format changed once around 2020. We support the current format
    (sec_bhavdata_full_DDMMYYYY.csv) — older history would need a separate path.
  - Holidays: trying a holiday returns 404; we treat that as 'no data, move on'.
  - First-run cost: 250+ HTTP requests. We rate-limit to avoid abuse complaints.
"""
from __future__ import annotations

import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

import pandas as pd
import requests

log = logging.getLogger("agent.bhavcopy")

ARCHIVE_URL_TEMPLATE = (
    "https://archives.nseindia.com/products/content/sec_bhavdata_full_{ddmmyyyy}.csv"
)
INDEX_URL_TEMPLATE = (
    "https://archives.nseindia.com/content/indices/ind_close_all_{ddmmyyyy}.csv"
)
HEADERS = {"User-Agent": "Mozilla/5.0"}


class BhavcopyHistory:
    def __init__(
        self,
        cache_dir: str | Path = "data/bhavcopy_cache",
        request_pause_seconds: float = 0.05,
        max_workers: int = 4,
        timeout_seconds: float = 15.0,
    ):
        self._cache = Path(cache_dir)
        self._cache.mkdir(parents=True, exist_ok=True)
        self._pause = request_pause_seconds
        self._workers = max_workers
        self._timeout = timeout_seconds
        # In-memory cache of parsed daily frames: date -> DataFrame indexed by SYMBOL
        self._daily_frames: dict[date, pd.DataFrame] = {}
        # Same idea for the daily index file: date -> DataFrame indexed by Index Name
        self._daily_index_frames: dict[date, pd.DataFrame] = {}

    # ---------- public API ----------

    def fetch_equity_daily(self, symbol: str, days: int) -> Optional[pd.DataFrame]:
        """Daily OHLC for a single symbol over the last `days` calendar days."""
        end = date.today()
        start = end - timedelta(days=days)
        return self._build_symbol_frame(symbol, start, end)

    def fetch_index_daily(self, index_name: str, days: int) -> Optional[pd.DataFrame]:
        """Daily OHLC for an index (e.g. 'Nifty 50') over the last `days` days."""
        end = date.today()
        start = end - timedelta(days=days)
        rows: list[dict] = []
        for d in self._iter_business_days(start, end):
            day_df = self._daily_index_frames.get(d)
            if day_df is None:
                day_df = self._load_index_day(d)
                if day_df is not None:
                    self._daily_index_frames[d] = day_df
            if day_df is None:
                continue
            try:
                row = day_df.loc[index_name]
                rows.append({
                    "ts": pd.Timestamp(d),
                    "open": float(row["Open Index Value"]),
                    "high": float(row["High Index Value"]),
                    "low": float(row["Low Index Value"]),
                    "close": float(row["Closing Index Value"]),
                    "volume": 0,
                })
            except KeyError:
                continue
        if not rows:
            return None
        return pd.DataFrame(rows).set_index("ts").sort_index()

    def warmup_range(self, start: date, end: date) -> int:
        """Pre-fetch all daily Bhavcopies in [start, end]. Returns count of days loaded."""
        dates = list(self._iter_business_days(start, end))
        loaded = 0
        with ThreadPoolExecutor(max_workers=self._workers) as ex:
            futures = {ex.submit(self._load_day, d): d for d in dates}
            for fut in as_completed(futures):
                d = futures[fut]
                try:
                    df = fut.result()
                    if df is not None:
                        self._daily_frames[d] = df
                        loaded += 1
                except Exception as e:
                    log.warning("Bhavcopy load failed for %s: %s", d, e)
        log.info("Bhavcopy warmup loaded %d/%d trading days", loaded, len(dates))
        return loaded

    # ---------- internals ----------

    def _build_symbol_frame(
        self, symbol: str, start: date, end: date
    ) -> Optional[pd.DataFrame]:
        rows: list[dict] = []
        for d in self._iter_business_days(start, end):
            day_df = self._daily_frames.get(d)
            if day_df is None:
                day_df = self._load_day(d)
                if day_df is not None:
                    self._daily_frames[d] = day_df
            if day_df is None:
                continue
            try:
                row = day_df.loc[symbol]
                rows.append({
                    "ts": pd.Timestamp(d),
                    "open": float(row["OPEN_PRICE"]),
                    "high": float(row["HIGH_PRICE"]),
                    "low": float(row["LOW_PRICE"]),
                    "close": float(row["CLOSE_PRICE"]),
                    "volume": int(row["TTL_TRD_QNTY"]),
                })
            except KeyError:
                continue  # symbol not traded that day
        if not rows:
            return None
        out = pd.DataFrame(rows).set_index("ts").sort_index()
        return out

    def _load_day(self, d: date) -> Optional[pd.DataFrame]:
        """Load (and cache) a single day's Bhavcopy. Returns DataFrame indexed by SYMBOL,EQ."""
        cache_file = self._cache / f"sec_bhavdata_full_{d.strftime('%d%m%Y')}.csv"
        if not cache_file.exists():
            url = ARCHIVE_URL_TEMPLATE.format(ddmmyyyy=d.strftime("%d%m%Y"))
            try:
                r = requests.get(url, headers=HEADERS, timeout=self._timeout)
            except requests.RequestException as e:
                log.debug("Bhavcopy GET failed for %s: %s", d, e)
                return None
            time.sleep(self._pause)
            if r.status_code == 404:
                return None  # holiday
            if r.status_code != 200:
                log.debug("Bhavcopy non-200 for %s: %s", d, r.status_code)
                return None
            cache_file.write_bytes(r.content)
        try:
            df = pd.read_csv(cache_file)
        except Exception as e:
            log.warning("Bhavcopy CSV parse failed for %s: %s", d, e)
            return None
        # NSE Bhavcopy has leading spaces in column names — strip them.
        df.columns = [c.strip() for c in df.columns]
        # Strip leading whitespace from string columns we'll read.
        for col in ("SYMBOL", "SERIES"):
            if col in df.columns:
                df[col] = df[col].astype(str).str.strip()
        # Keep only EQ series, index by symbol for O(1) lookup.
        if "SERIES" in df.columns:
            df = df[df["SERIES"] == "EQ"]
        if "SYMBOL" not in df.columns:
            return None
        return df.set_index("SYMBOL")

    def _load_index_day(self, d: date) -> Optional[pd.DataFrame]:
        cache_file = self._cache / f"ind_close_all_{d.strftime('%d%m%Y')}.csv"
        if not cache_file.exists():
            url = INDEX_URL_TEMPLATE.format(ddmmyyyy=d.strftime("%d%m%Y"))
            try:
                r = requests.get(url, headers=HEADERS, timeout=self._timeout)
            except requests.RequestException:
                return None
            time.sleep(self._pause)
            if r.status_code != 200:
                return None
            cache_file.write_bytes(r.content)
        try:
            df = pd.read_csv(cache_file)
        except Exception:
            return None
        df.columns = [c.strip() for c in df.columns]
        if "Index Name" not in df.columns:
            return None
        df["Index Name"] = df["Index Name"].astype(str).str.strip()
        return df.set_index("Index Name")

    @staticmethod
    def _iter_business_days(start: date, end: date):
        d = start
        while d <= end:
            if d.weekday() < 5:  # Mon=0..Fri=4
                yield d
            d += timedelta(days=1)
