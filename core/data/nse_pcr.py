"""Fetch Nifty Put-Call Ratio (PCR) from NSE India's public option-chain API.

PCR = total Nifty put OI / total Nifty call OI

Interpretation:
  PCR > 1.3  — strong bullish sentiment (protective puts dominant)
  PCR 0.7–1.3 — neutral
  PCR < 0.7  — bearish sentiment (calls dominant; speculative / distribution phase)

Usage:
    from core.data.nse_pcr import get_nifty_pcr
    pcr = get_nifty_pcr()   # returns float or None

Design notes:
- Cached for 15 minutes to avoid hammering NSE during live trading.
- Returns None on any fetch/parse failure (fail-open: don't block trading).
- NSE requires a session cookie obtained by first GETting the home page.
"""
from __future__ import annotations

import logging
import time
from typing import Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Module-level cache (simple in-process cache; sufficient for a single process)
# ---------------------------------------------------------------------------
_CACHE_TTL_SECONDS = 15 * 60  # 15 minutes

_cached_pcr: Optional[float] = None
_cache_timestamp: float = 0.0

_NSE_HOME = "https://www.nseindia.com"
_NSE_OC_URL = "https://www.nseindia.com/api/option-chain-indices?symbol=NIFTY"
_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "*/*",
    "Accept-Language": "en-US,en;q=0.5",
    "Referer": "https://www.nseindia.com/option-chain",
}


def _fetch_pcr() -> Optional[float]:
    """Fetch a fresh PCR value from NSE. Returns None on any error."""
    try:
        import requests  # imported lazily so the module is importable without requests in CI

        session = requests.Session()
        session.headers.update(_HEADERS)

        # Step 1: hit the home page to obtain session cookies that NSE requires
        home_resp = session.get(_NSE_HOME, timeout=10)
        home_resp.raise_for_status()

        # Step 2: fetch the option-chain data
        oc_resp = session.get(_NSE_OC_URL, timeout=10)
        oc_resp.raise_for_status()

        data = oc_resp.json()
        filtered = data.get("filtered", {})
        pe_oi = filtered.get("PE", {}).get("totOI")
        ce_oi = filtered.get("CE", {}).get("totOI")

        if pe_oi is None or ce_oi is None:
            logger.warning("nse_pcr: missing PE/CE totOI in response")
            return None
        if ce_oi == 0:
            logger.warning("nse_pcr: CE totOI is zero, cannot compute PCR")
            return None

        pcr = float(pe_oi) / float(ce_oi)
        logger.debug("nse_pcr: fetched PCR=%.3f (PE_OI=%s, CE_OI=%s)", pcr, pe_oi, ce_oi)
        return pcr

    except Exception as exc:  # noqa: BLE001
        logger.warning("nse_pcr: fetch failed (%s), returning None (fail-open)", exc)
        return None


def get_nifty_pcr(*, _now: Optional[float] = None) -> Optional[float]:
    """Return the latest Nifty PCR, using a 15-minute in-process cache.

    Parameters
    ----------
    _now:
        Override the current time (used in tests).

    Returns
    -------
    float or None
        PCR value, or None if the fetch failed (fail-open: caller should not
        block trading on a None return).
    """
    global _cached_pcr, _cache_timestamp

    now = _now if _now is not None else time.monotonic()
    if _cached_pcr is not None and (now - _cache_timestamp) < _CACHE_TTL_SECONDS:
        logger.debug("nse_pcr: returning cached PCR=%.3f", _cached_pcr)
        return _cached_pcr

    fresh = _fetch_pcr()
    if fresh is not None:
        _cached_pcr = fresh
        _cache_timestamp = now
    # If fetch failed, return the stale cache value (if any) rather than None,
    # so a transient network glitch doesn't disrupt live trading.
    return _cached_pcr if fresh is None else fresh


def clear_cache() -> None:
    """Reset the module-level cache (useful in tests)."""
    global _cached_pcr, _cache_timestamp
    _cached_pcr = None
    _cache_timestamp = 0.0
