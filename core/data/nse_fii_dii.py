"""Fetch FII/DII daily net flows from NSE India's public API.

FII (Foreign Institutional Investors) and DII (Domestic Institutional Investors)
net buy/sell is a strong leading indicator for Nifty direction.

  FII net > 0 = foreign money flowing in  → bullish
  FII net < 0 = foreign money flowing out → bearish

NSE endpoint: https://www.nseindia.com/api/fiidiiTradeReact
Response: array of objects, each with fields including:
  - date
  - fiiNetDii  (FII net value in crores)
  - diiNetDii  (DII net value in crores)

Usage:
    from core.data.nse_fii_dii import get_fii_dii_flows, get_institutional_sentiment
    flows = get_fii_dii_flows(days=5)    # list[FIIDIIFlow]
    sentiment = get_institutional_sentiment()  # "BULLISH", "BEARISH", or None

Design notes:
- Cached for 30 minutes; flows are daily, published after market close so this
  is safe for intraday usage without hammering the NSE endpoint.
- Returns [] on any fetch/parse failure (fail-open: don't block trading).
- NSE requires a User-Agent and Referer header.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import List, Optional

from core.data.nse_rate_limiter import circuit_breaker, nse_rate_limit, nse_retry

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Module-level cache
# ---------------------------------------------------------------------------
_CACHE_TTL_SECONDS = 30 * 60  # 30 minutes

_cached_flows: Optional[list] = None
_cache_timestamp: float = 0.0

_NSE_FII_DII_URL = "https://www.nseindia.com/api/fiidiiTradeReact"
_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "*/*",
    "Accept-Language": "en-US,en;q=0.5",
    "Referer": "https://www.nseindia.com",
}

# Sentiment threshold: ±500 crore FII net over 3-day average
_BULLISH_THRESHOLD_CR = 500.0
_BEARISH_THRESHOLD_CR = -500.0
_SENTIMENT_LOOKBACK_DAYS = 3


@dataclass
class FIIDIIFlow:
    """One day of FII/DII flow data."""
    date: str                # ISO date string, e.g. "2025-05-30"
    fii_net_inr_cr: float    # FII net buy/sell in Indian Rupee crores
    dii_net_inr_cr: float    # DII net buy/sell in Indian Rupee crores
    combined_net: float      # FII + DII combined net


@nse_retry(max_retries=3, base_delay=1.0)
@nse_rate_limit
def _fetch_flows() -> list[FIIDIIFlow]:
    """Fetch fresh FII/DII flow data from NSE. Returns [] on any error."""
    if circuit_breaker.is_open():
        logger.warning("nse_fii_dii: circuit breaker OPEN — skipping fetch, returning []")
        return []
    try:
        import requests  # imported lazily so the module is importable without requests in CI

        resp = requests.get(_NSE_FII_DII_URL, headers=_HEADERS, timeout=10)
        if resp.status_code in (403, 429):
            circuit_breaker.record_failure(resp.status_code)
            resp.raise_for_status()
        resp.raise_for_status()
        circuit_breaker.record_success()

        data = resp.json()

        if not isinstance(data, list):
            logger.warning("nse_fii_dii: expected list response, got %s", type(data).__name__)
            return []

        flows: list[FIIDIIFlow] = []
        for entry in data:
            try:
                date_str = str(entry.get("date", "")).strip()
                fii_raw = entry.get("fiiNetDii")
                dii_raw = entry.get("diiNetDii")

                if fii_raw is None or dii_raw is None:
                    logger.debug("nse_fii_dii: skipping entry with missing fields: %s", entry)
                    continue

                fii_net = float(fii_raw)
                dii_net = float(dii_raw)
                combined = fii_net + dii_net

                flows.append(FIIDIIFlow(
                    date=date_str,
                    fii_net_inr_cr=fii_net,
                    dii_net_inr_cr=dii_net,
                    combined_net=combined,
                ))
            except (ValueError, TypeError) as exc:
                logger.debug("nse_fii_dii: skipping malformed entry (%s): %s", exc, entry)
                continue

        logger.debug("nse_fii_dii: fetched %d flow records", len(flows))
        return flows

    except Exception as exc:  # noqa: BLE001
        logger.warning("nse_fii_dii: fetch failed (%s), returning [] (fail-open)", exc)
        return []


def get_fii_dii_flows(days: int = 5, *, _now: Optional[float] = None) -> list[FIIDIIFlow]:
    """Return the last *days* records of FII/DII flows, using a 30-minute cache.

    Parameters
    ----------
    days:
        Maximum number of most-recent records to return.
    _now:
        Override the current time (used in tests).

    Returns
    -------
    list[FIIDIIFlow]
        Most-recent records first (as returned by NSE), truncated to *days*.
        Returns [] if the fetch failed (fail-open: caller should not block
        trading on an empty return).
    """
    global _cached_flows, _cache_timestamp

    now = _now if _now is not None else time.monotonic()
    if _cached_flows is not None and (now - _cache_timestamp) < _CACHE_TTL_SECONDS:
        logger.debug("nse_fii_dii: returning %d cached records", len(_cached_flows))
        return _cached_flows[:days]

    fresh = _fetch_flows()
    if fresh:
        _cached_flows = fresh
        _cache_timestamp = now
    # If fetch failed, return stale cache (if any) rather than disrupting trading.
    result = _cached_flows if (not fresh and _cached_flows is not None) else fresh
    if result is None:
        result = []
    return result[:days]


def get_institutional_sentiment(*, _now: Optional[float] = None) -> Optional[str]:
    """Return institutional sentiment based on recent FII net flows.

    Logic:
      - Average FII net over last 3 days > +500 crore  → "BULLISH"
      - Average FII net over last 3 days < -500 crore  → "BEARISH"
      - Otherwise (neutral or insufficient data)        → None

    Parameters
    ----------
    _now:
        Override the current time passed through to get_fii_dii_flows (used in tests).

    Returns
    -------
    "BULLISH", "BEARISH", or None
    """
    flows = get_fii_dii_flows(days=_SENTIMENT_LOOKBACK_DAYS, _now=_now)
    if not flows:
        return None

    avg_fii = sum(f.fii_net_inr_cr for f in flows) / len(flows)
    logger.debug("nse_fii_dii: avg FII net (last %d days) = %.1f cr", len(flows), avg_fii)

    if avg_fii > _BULLISH_THRESHOLD_CR:
        return "BULLISH"
    if avg_fii < _BEARISH_THRESHOLD_CR:
        return "BEARISH"
    return None


def clear_cache() -> None:
    """Reset the module-level cache (useful in tests)."""
    global _cached_flows, _cache_timestamp
    _cached_flows = None
    _cache_timestamp = 0.0
