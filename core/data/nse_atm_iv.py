"""Fetch Nifty ATM (at-the-money) implied volatility from NSE option chain API.

ATM IV is derived from the CE (call) implied volatility of the strike closest
to the current Nifty spot price.  It is more precise than India VIX for
short-term regime classification because it reflects the exact cost of hedging
the nearest expiry rather than a 30-day interpolated measure.

Usage:
    from core.data.nse_atm_iv import get_atm_iv
    iv = get_atm_iv()   # returns float or None (percentage, e.g. 12.5 means 12.5%)

Design notes:
- Cached for 5 minutes (same cadence as nse_vix) to avoid hammering NSE.
- Returns None on any fetch/parse failure (fail-open: don't block trading).
- NSE requires a session cookie obtained by first GETting the home page.
- The option chain payload structure::

    {
      "records": {
        "underlyingValue": 24500.0,   # current Nifty spot price
        "data": [
          {
            "strikePrice": 24500,
            "CE": {"impliedVolatility": 12.5, ...},
            "PE": {"impliedVolatility": 12.3, ...},
          },
          ...
        ]
      }
    }
"""
from __future__ import annotations

import logging
import time
from typing import Optional

from core.data.nse_rate_limiter import circuit_breaker, nse_rate_limit, nse_retry

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Module-level cache (simple in-process cache; sufficient for a single process)
# ---------------------------------------------------------------------------
_CACHE_TTL_SECONDS = 5 * 60  # 5 minutes

_cached_iv: Optional[float] = None
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


@nse_retry(max_retries=3, base_delay=1.0)
@nse_rate_limit
def _fetch_atm_iv() -> Optional[float]:
    """Fetch a fresh ATM IV value from the NSE option chain.  Returns None on any error."""
    if circuit_breaker.is_open():
        logger.warning("nse_atm_iv: circuit breaker OPEN — skipping fetch, returning None")
        return None
    try:
        import requests  # imported lazily so the module is importable without requests in CI

        session = requests.Session()
        session.headers.update(_HEADERS)

        # Step 1: hit the home page to obtain session cookies that NSE requires
        home_resp = session.get(_NSE_HOME, timeout=10)
        if home_resp.status_code in (403, 429):
            circuit_breaker.record_failure(home_resp.status_code)
            home_resp.raise_for_status()
        home_resp.raise_for_status()

        # Step 2: fetch the option-chain data
        oc_resp = session.get(_NSE_OC_URL, timeout=10)
        if oc_resp.status_code in (403, 429):
            circuit_breaker.record_failure(oc_resp.status_code)
            oc_resp.raise_for_status()
        oc_resp.raise_for_status()
        circuit_breaker.record_success()

        data = oc_resp.json()
        records = data.get("records", {})

        # Extract current spot price
        spot = records.get("underlyingValue")
        if spot is None:
            logger.warning("nse_atm_iv: missing underlyingValue in response")
            return None
        spot = float(spot)

        # Find the strike closest to spot
        strikes_data = records.get("data", [])
        if not strikes_data:
            logger.warning("nse_atm_iv: empty data array in response")
            return None

        best_entry = None
        best_dist = float("inf")
        for entry in strikes_data:
            strike = entry.get("strikePrice")
            if strike is None:
                continue
            dist = abs(float(strike) - spot)
            if dist < best_dist:
                best_dist = dist
                best_entry = entry

        if best_entry is None:
            logger.warning("nse_atm_iv: could not find any strike close to spot=%.2f", spot)
            return None

        ce_data = best_entry.get("CE", {})
        iv = ce_data.get("impliedVolatility")
        if iv is None:
            logger.warning(
                "nse_atm_iv: CE impliedVolatility missing for ATM strike %.0f",
                best_entry.get("strikePrice", 0),
            )
            return None

        iv_float = float(iv)
        logger.debug(
            "nse_atm_iv: fetched ATM IV=%.2f%% (spot=%.2f, strike=%.0f)",
            iv_float, spot, best_entry.get("strikePrice", 0),
        )
        return iv_float

    except Exception as exc:  # noqa: BLE001
        logger.warning("nse_atm_iv: fetch failed (%s), returning None (fail-open)", exc)
        return None


def get_atm_iv(*, _now: Optional[float] = None) -> Optional[float]:
    """Return the latest Nifty ATM CE implied volatility, using a 5-minute cache.

    Parameters
    ----------
    _now:
        Override the current time (used in tests).

    Returns
    -------
    float or None
        ATM IV as a percentage (e.g. 12.5 means 12.5%), or None if the fetch
        failed (fail-open: caller should not block trading on a None return).
    """
    global _cached_iv, _cache_timestamp

    now = _now if _now is not None else time.monotonic()
    if _cached_iv is not None and (now - _cache_timestamp) < _CACHE_TTL_SECONDS:
        logger.debug("nse_atm_iv: returning cached ATM IV=%.2f%%", _cached_iv)
        return _cached_iv

    fresh = _fetch_atm_iv()
    if fresh is not None:
        _cached_iv = fresh
        _cache_timestamp = now
    # If fetch failed, return the stale cache value (if any) rather than None,
    # so a transient network glitch doesn't disrupt live trading.
    return _cached_iv if fresh is None else fresh


def clear_cache() -> None:
    """Reset the module-level cache (useful in tests)."""
    global _cached_iv, _cache_timestamp
    _cached_iv = None
    _cache_timestamp = 0.0
