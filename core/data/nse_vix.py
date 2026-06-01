"""Fetch India VIX from NSE India's public allIndices API.

India VIX is the NSE's volatility index, derived from Nifty options prices.
It reflects near-term expected volatility (30-day).

Usage:
    from core.data.nse_vix import get_vix
    vix = get_vix()   # returns float or None

Design notes:
- Cached for 5 minutes to avoid hammering NSE during live trading.
- Returns None on any fetch/parse failure (fail-open: don't block trading).
- NSE requires a User-Agent and Referer header; no session cookie needed for
  the allIndices endpoint (unlike the option-chain endpoint).
"""
from __future__ import annotations

import logging
import time
from typing import Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Module-level cache (simple in-process cache; sufficient for a single process)
# ---------------------------------------------------------------------------
_CACHE_TTL_SECONDS = 5 * 60  # 5 minutes

_cached_vix: Optional[float] = None
_cache_timestamp: float = 0.0

_NSE_ALL_INDICES_URL = "https://www.nseindia.com/api/allIndices"
_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "*/*",
    "Accept-Language": "en-US,en;q=0.5",
    "Referer": "https://www.nseindia.com",
}


def _fetch_vix() -> Optional[float]:
    """Fetch a fresh India VIX value from NSE. Returns None on any error."""
    try:
        import requests  # imported lazily so the module is importable without requests in CI

        resp = requests.get(_NSE_ALL_INDICES_URL, headers=_HEADERS, timeout=10)
        resp.raise_for_status()

        data = resp.json()
        indices = data.get("data", [])
        for entry in indices:
            if entry.get("index") == "INDIA VIX":
                raw = entry.get("last")
                if raw is None:
                    logger.warning("nse_vix: INDIA VIX entry found but 'last' is None")
                    return None
                vix = float(raw)
                logger.debug("nse_vix: fetched India VIX=%.2f", vix)
                return vix

        logger.warning("nse_vix: INDIA VIX entry not found in allIndices response")
        return None

    except Exception as exc:  # noqa: BLE001
        logger.warning("nse_vix: fetch failed (%s), returning None (fail-open)", exc)
        return None


def get_vix(*, _now: Optional[float] = None) -> Optional[float]:
    """Return the latest India VIX, using a 5-minute in-process cache.

    Parameters
    ----------
    _now:
        Override the current time (used in tests).

    Returns
    -------
    float or None
        India VIX value, or None if the fetch failed (fail-open: caller should
        not block trading on a None return).
    """
    global _cached_vix, _cache_timestamp

    now = _now if _now is not None else time.monotonic()
    if _cached_vix is not None and (now - _cache_timestamp) < _CACHE_TTL_SECONDS:
        logger.debug("nse_vix: returning cached VIX=%.2f", _cached_vix)
        return _cached_vix

    fresh = _fetch_vix()
    if fresh is not None:
        _cached_vix = fresh
        _cache_timestamp = now
    # If fetch failed, return the stale cache value (if any) rather than None,
    # so a transient network glitch doesn't disrupt live trading.
    return _cached_vix if fresh is None else fresh


def clear_cache() -> None:
    """Reset the module-level cache (useful in tests)."""
    global _cached_vix, _cache_timestamp
    _cached_vix = None
    _cache_timestamp = 0.0
