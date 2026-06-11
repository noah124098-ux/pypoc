"""Historical OHLC loader for the backtest engine.

Loads daily OHLC for the universe + Nifty proxy, caches to Parquet so subsequent
runs are fast. Walk-forward integrity is enforced at the engine level — this
loader just hands raw history to the engine, which slices it by date.

Source priority:
  1. Angel One SmartAPI (if creds in .env) — same source as the live feed
  2. NSE Bhavcopy archives (no creds) — daily CSV per trading day
  3. NSE direct API (no creds, browser-session bootstrap) — currently blocked by bot detection
  4. yfinance / nsepython — public fallbacks of last resort
"""
from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import Optional

import pandas as pd

from core.config import Secrets
from core.data.historical import fetch_daily as fetch_daily_public

log = logging.getLogger("backtest.data")


class HistoricalLoader:
    def __init__(self, cache_dir: str | Path = "data/backtest_cache"):
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._angel_client = None
        self._angel_init_failed = False
        self._nse_client = None
        self._bhav_client = None

    @staticmethod
    def _tz_naive(df: Optional[pd.DataFrame]) -> Optional[pd.DataFrame]:
        """Normalize a DatetimeIndex to tz-naive.

        Sources are mixed: Angel One returns tz-aware (+05:30) indices while
        Bhavcopy/NSE/yfinance return tz-naive. Mixing them breaks `.loc[:date]`
        slicing in the engine (pandas refuses tz-naive vs tz-aware comparison).
        """
        if df is not None and not df.empty and getattr(df.index, "tz", None) is not None:
            df = df.copy()
            df.index = df.index.tz_localize(None)
        return df

    def load(self, symbol: str, days: int) -> Optional[pd.DataFrame]:
        cache_file = self._cache_path(symbol, days)
        if cache_file.exists():
            try:
                df = pd.read_parquet(cache_file)
                if not df.empty:
                    return self._tz_naive(df)
            except Exception as e:
                log.warning("Cache read failed for %s: %s", symbol, e)

        df = self._fetch_from_angel(symbol, days)
        if df is None or df.empty:
            df = self._fetch_from_bhavcopy(symbol, days)
        if df is None or df.empty:
            df = self._fetch_from_nse(symbol, days)
        if df is None or df.empty:
            df = fetch_daily_public(symbol, days=days)
        if df is None or df.empty:
            return None
        df = self._tz_naive(df.sort_index())
        try:
            df.to_parquet(cache_file)
        except Exception as e:
            log.warning("Cache write failed for %s: %s", symbol, e)
        return df

    def load_universe(self, symbols: list[str], days: int) -> dict[str, pd.DataFrame]:
        # Optimization: warm up the Bhavcopy daily-frame cache first, so per-symbol
        # lookups become O(1) dict reads instead of N redundant downloads.
        try:
            from datetime import date, timedelta as _td
            end = date.today()
            start = end - _td(days=days)
            self._bhav().warmup_range(start, end)
        except Exception as e:
            log.debug("Bhavcopy warmup skipped: %s", e)

        out: dict[str, pd.DataFrame] = {}
        missing: list[str] = []
        for s in symbols:
            df = self.load(s, days)
            if df is None or df.empty:
                missing.append(s)
                continue
            out[s] = df
        if missing:
            log.warning("Missing history for %d symbols: %s", len(missing), missing[:5])
        return out

    def load_nifty(self, days: int) -> Optional[pd.DataFrame]:
        """Best-effort Nifty 50 index. Try Angel One -> Bhavcopy -> NSE direct -> yfinance/ETF."""
        df = self._fetch_nifty_index_from_angel(days)
        if df is not None and not df.empty:
            return self._tz_naive(df)
        try:
            df = self._bhav().fetch_index_daily("Nifty 50", days=days)
            if df is not None and not df.empty:
                return self._tz_naive(df)
        except Exception as e:
            log.debug("Bhavcopy index fetch failed: %s", e)
        df = self._fetch_nifty_index_from_nse(days)
        if df is not None and not df.empty:
            return self._tz_naive(df)
        for sym in ("^NSEI", "NIFTYBEES"):
            df = self.load(sym, days)
            if df is not None and not df.empty:
                return self._tz_naive(df)
        return None

    # ---------- internals ----------

    def _angel_or_none(self):
        if self._angel_client is not None:
            return self._angel_client
        if self._angel_init_failed:
            return None
        secrets = Secrets.from_env()
        if not all([
            secrets.angel_one_api_key,
            secrets.angel_one_client_code,
            secrets.angel_one_password,
            secrets.angel_one_totp_secret,
        ]):
            self._angel_init_failed = True
            return None
        try:
            from core.data.angelone_history import AngelOneHistory

            self._angel_client = AngelOneHistory(
                api_key=secrets.angel_one_api_key,
                client_code=secrets.angel_one_client_code,
                password=secrets.angel_one_password,
                totp_secret=secrets.angel_one_totp_secret,
            )
            return self._angel_client
        except Exception as e:
            log.warning("Angel One historical client init failed: %s — falling back to public sources", e)
            self._angel_init_failed = True
            return None

    def _bhav(self):
        if self._bhav_client is None:
            from core.data.bhavcopy import BhavcopyHistory

            self._bhav_client = BhavcopyHistory()
        return self._bhav_client

    def _fetch_from_bhavcopy(self, symbol: str, days: int) -> Optional[pd.DataFrame]:
        try:
            return self._bhav().fetch_equity_daily(symbol, days)
        except Exception as e:
            log.warning("Bhavcopy fetch failed for %s: %s", symbol, e)
            return None

    def _nse(self):
        if self._nse_client is None:
            from core.data.nse_history import NseHistory

            self._nse_client = NseHistory()
        return self._nse_client

    def _fetch_from_nse(self, symbol: str, days: int) -> Optional[pd.DataFrame]:
        try:
            return self._nse().fetch_equity_daily(symbol, days)
        except Exception as e:
            log.warning("NSE direct fetch failed for %s: %s", symbol, e)
            return None

    def _fetch_nifty_index_from_nse(self, days: int) -> Optional[pd.DataFrame]:
        try:
            return self._nse().fetch_nifty_index_daily(days)
        except Exception as e:
            log.warning("NSE direct Nifty fetch failed: %s", e)
            return None

    def _fetch_from_angel(self, symbol: str, days: int) -> Optional[pd.DataFrame]:
        client = self._angel_or_none()
        if client is None:
            return None
        try:
            return client.fetch_daily(symbol, days=days)
        except Exception as e:
            log.warning("Angel One fetch_daily failed for %s: %s", symbol, e)
            return None

    def _fetch_nifty_index_from_angel(self, days: int) -> Optional[pd.DataFrame]:
        """Nifty 50 index is not under EQ — needs a direct token lookup."""
        client = self._angel_or_none()
        if client is None:
            return None
        try:
            # Angel One stores the index under 'Nifty 50' / instrumenttype 'AMXIDX' / exchange NSE.
            # Token 99926000 is documented; we resolve via the instrument master to be safe.
            client._ensure_login()  # noqa: SLF001
            if not client._symbol_to_token:  # noqa: SLF001
                client._load_instrument_master()  # noqa: SLF001
            # Try several name variants known to appear in the master.
            for sym in ("NIFTY 50", "Nifty 50", "NIFTY"):
                df = client.fetch_candles(sym, interval="1d", days=days)
                if df is not None and not df.empty:
                    return df
        except Exception as e:
            log.warning("Angel One Nifty index fetch failed: %s", e)
        return None

    def _cache_path(self, symbol: str, days: int) -> Path:
        safe = symbol.replace("/", "_").replace("&", "_").replace(" ", "_")
        date_tag = datetime.now().strftime("%Y%m%d")
        return self.cache_dir / f"{safe}_{days}d_{date_tag}.parquet"
