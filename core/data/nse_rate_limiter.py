"""Shared rate limiter, retry decorator, and circuit breaker for NSE API calls.

NSE India's public API will return 403/429 if polled too aggressively.  All
three data modules (nse_vix, nse_pcr, nse_fii_dii) import from here so that
a single global counter governs the overall request rate regardless of which
module triggers a call.

Public API
----------
nse_rate_limit(func)
    Decorator.  Enforces a minimum 2-second gap between any two NSE API calls
    (process-wide, thread-safe).  Sleeps the calling thread if needed.

nse_retry(max_retries=3, base_delay=1.0)
    Decorator factory.  Wraps a function with exponential-backoff retry on any
    exception.  Intended to be stacked on top of nse_rate_limit.

    Usage::

        @nse_retry(max_retries=3, base_delay=1.0)
        @nse_rate_limit
        def _fetch_something() -> ...:
            ...

circuit_breaker
    Module-level singleton.  Call ``circuit_breaker.record_failure(status_code)``
    after a 403/429 response and ``circuit_breaker.record_success()`` on a good
    response.  Call ``circuit_breaker.is_open()`` at the start of every fetch to
    skip the HTTP call while the circuit is open (15-minute lockout after 3
    consecutive 403/429 errors).

reset_for_testing()
    Resets all module-level state.  Use in test ``setup_method`` / ``teardown``.
"""
from __future__ import annotations

import logging
import threading
import time
from functools import wraps
from typing import Any, Callable, TypeVar

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Rate limiter state (module-level, process-wide)
# ---------------------------------------------------------------------------

_rl_lock = threading.Lock()
_last_call: float = 0.0  # monotonic timestamp of the last NSE API call
_MIN_INTERVAL: float = 2.0  # seconds — minimum gap between successive calls


def nse_rate_limit(func: Callable) -> Callable:
    """Decorator: enforce a min 2-second gap between NSE API calls (global, thread-safe).

    If the last call was fewer than ``_MIN_INTERVAL`` seconds ago the decorator
    sleeps the calling thread for the remainder before invoking *func*.
    """
    @wraps(func)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        global _last_call
        with _rl_lock:
            now = time.monotonic()
            wait = _MIN_INTERVAL - (now - _last_call)
            if wait > 0:
                logger.debug("nse_rate_limit: sleeping %.2fs before %s", wait, func.__name__)
                time.sleep(wait)
            _last_call = time.monotonic()
        return func(*args, **kwargs)
    return wrapper


# ---------------------------------------------------------------------------
# Retry with exponential backoff
# ---------------------------------------------------------------------------

_F = TypeVar("_F", bound=Callable[..., Any])


def nse_retry(max_retries: int = 3, base_delay: float = 1.0) -> Callable[[_F], _F]:
    """Decorator factory: retry *func* with exponential backoff on any exception.

    Parameters
    ----------
    max_retries:
        Total number of additional attempts after the first failure.
        ``max_retries=3`` means up to 4 total calls (1 original + 3 retries).
    base_delay:
        Base sleep time in seconds.  Actual delay for attempt *n* (0-indexed) is
        ``base_delay * 2**n`` (capped at 30 s).

    The function must follow the fail-open convention: return ``None`` / ``[]``
    rather than raise on a final failure.  This decorator re-raises the last
    exception only after all retries are exhausted (the outer ``_fetch_*``
    wrappers catch it and return the safe fallback).
    """
    def decorator(func: _F) -> _F:  # type: ignore[return]
        @wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            last_exc: BaseException | None = None
            for attempt in range(max_retries + 1):
                try:
                    return func(*args, **kwargs)
                except Exception as exc:  # noqa: BLE001
                    last_exc = exc
                    if attempt < max_retries:
                        delay = min(base_delay * (2 ** attempt), 30.0)
                        logger.warning(
                            "nse_retry: %s failed (attempt %d/%d): %s — retrying in %.1fs",
                            func.__name__, attempt + 1, max_retries + 1, exc, delay,
                        )
                        time.sleep(delay)
                    else:
                        logger.warning(
                            "nse_retry: %s failed after %d attempts: %s",
                            func.__name__, max_retries + 1, exc,
                        )
            raise last_exc  # type: ignore[misc]
        return wrapper  # type: ignore[return-value]
    return decorator


# ---------------------------------------------------------------------------
# Circuit breaker
# ---------------------------------------------------------------------------

_TRIP_THRESHOLD = 3          # consecutive 403/429 errors before opening
_LOCKOUT_SECONDS = 15 * 60   # 15 minutes


class _CircuitBreaker:
    """Simple fail-fast circuit breaker for NSE API rate-limit errors.

    State machine::

        CLOSED (normal) ──3× 403/429──> OPEN (15-min lockout)
                    <──timeout or success── CLOSED

    Thread-safe.  A single instance ``circuit_breaker`` is exported from this
    module and shared by all NSE data modules.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._consecutive_failures: int = 0
        self._open_since: float = 0.0  # monotonic; 0.0 means CLOSED
        self._is_open: bool = False

    # ------------------------------------------------------------------
    # Public helpers
    # ------------------------------------------------------------------

    def is_open(self) -> bool:
        """Return True if the circuit is currently open (requests should be skipped)."""
        with self._lock:
            if not self._is_open:
                return False
            # Check if the lockout period has expired → auto-reset
            elapsed = time.monotonic() - self._open_since
            if elapsed >= _LOCKOUT_SECONDS:
                logger.info(
                    "nse_circuit_breaker: lockout expired (%.0fs >= %.0fs) — resetting",
                    elapsed, _LOCKOUT_SECONDS,
                )
                self._reset_locked()
                return False
            remaining = _LOCKOUT_SECONDS - elapsed
            logger.debug(
                "nse_circuit_breaker: OPEN — %.0fs remaining in lockout", remaining
            )
            return True

    def record_failure(self, status_code: int | None = None) -> None:
        """Record a 403/429 HTTP error.  Opens the circuit after the threshold."""
        with self._lock:
            if status_code not in (403, 429):
                # Only trip on rate-limit / forbidden responses.
                return
            self._consecutive_failures += 1
            logger.warning(
                "nse_circuit_breaker: HTTP %s — consecutive failures: %d/%d",
                status_code, self._consecutive_failures, _TRIP_THRESHOLD,
            )
            if self._consecutive_failures >= _TRIP_THRESHOLD and not self._is_open:
                self._is_open = True
                self._open_since = time.monotonic()
                logger.error(
                    "nse_circuit_breaker: OPEN — %d consecutive %s errors. "
                    "Suppressing NSE API calls for %d minutes.",
                    self._consecutive_failures, status_code, _LOCKOUT_SECONDS // 60,
                )

    def record_success(self) -> None:
        """Record a successful response.  Resets the consecutive failure counter."""
        with self._lock:
            if self._consecutive_failures > 0 or self._is_open:
                logger.debug("nse_circuit_breaker: success — resetting failure count")
            self._reset_locked()

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _reset_locked(self) -> None:
        """Reset to CLOSED state.  Must be called with ``_lock`` held."""
        self._consecutive_failures = 0
        self._is_open = False
        self._open_since = 0.0


# Module-level singleton
circuit_breaker = _CircuitBreaker()


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------

def reset_for_testing() -> None:
    """Reset all module-level rate-limiter and circuit-breaker state.

    Call this in test ``setup_method`` / ``teardown_method`` to prevent state
    leaking between tests.
    """
    global _last_call
    with _rl_lock:
        _last_call = 0.0
    with circuit_breaker._lock:
        circuit_breaker._reset_locked()
