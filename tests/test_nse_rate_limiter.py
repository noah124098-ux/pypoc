"""Tests for core/data/nse_rate_limiter.py.

Covers:
- nse_rate_limit: enforces minimum interval between calls
- nse_retry: retries on exception with exponential backoff; re-raises after max_retries
- _CircuitBreaker: opens after _TRIP_THRESHOLD consecutive 403/429 failures,
  auto-resets after _LOCKOUT_SECONDS, records_success resets counter,
  non-rate-limit error codes do not trip the breaker
- Integration: circuit breaker is open → decorated fetch returns None/[]
"""
from __future__ import annotations

import time
from unittest.mock import MagicMock, call, patch

import pytest

import core.data.nse_rate_limiter as rl


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _reset():
    """Reset all module-level state before each test."""
    rl.reset_for_testing()


# ---------------------------------------------------------------------------
# nse_rate_limit
# ---------------------------------------------------------------------------

class TestNseRateLimit:
    def setup_method(self):
        _reset()

    def test_first_call_is_not_delayed(self):
        """The very first call (no prior call) should not be delayed."""
        calls = []

        @rl.nse_rate_limit
        def fn():
            calls.append(time.monotonic())
            return "ok"

        t0 = time.monotonic()
        result = fn()
        elapsed = time.monotonic() - t0

        assert result == "ok"
        # Should complete very quickly (well under MIN_INTERVAL)
        assert elapsed < 1.5, f"First call took too long: {elapsed:.2f}s"

    def test_rapid_second_call_is_throttled(self):
        """A second call immediately after the first must be delayed by ~MIN_INTERVAL."""
        call_times: list[float] = []

        @rl.nse_rate_limit
        def fn():
            call_times.append(time.monotonic())
            return "ok"

        fn()  # first call — sets _last_call
        t_between = time.monotonic()
        fn()  # second call — should sleep

        gap = call_times[1] - call_times[0]
        assert gap >= rl._MIN_INTERVAL * 0.9, (
            f"Expected >= {rl._MIN_INTERVAL}s gap between calls; got {gap:.3f}s"
        )

    def test_call_after_interval_is_not_delayed(self):
        """A call made more than MIN_INTERVAL after the previous one should not sleep."""
        @rl.nse_rate_limit
        def fn():
            return time.monotonic()

        fn()
        # Wait slightly longer than the minimum interval
        time.sleep(rl._MIN_INTERVAL + 0.1)
        t0 = time.monotonic()
        fn()
        elapsed = time.monotonic() - t0
        assert elapsed < 0.5, f"Call after interval gap was unexpectedly delayed: {elapsed:.2f}s"

    def test_return_value_is_preserved(self):
        @rl.nse_rate_limit
        def fn(x, y=1):
            return x + y

        # Just the first call — no delay needed
        assert fn(3, y=4) == 7

    def test_exception_propagates(self):
        @rl.nse_rate_limit
        def fn():
            raise ValueError("boom")

        with pytest.raises(ValueError, match="boom"):
            fn()

    def test_funcname_is_preserved(self):
        @rl.nse_rate_limit
        def my_special_fn():
            pass

        assert my_special_fn.__name__ == "my_special_fn"


# ---------------------------------------------------------------------------
# nse_retry
# ---------------------------------------------------------------------------

class TestNseRetry:
    def setup_method(self):
        _reset()

    def test_succeeds_on_first_try(self):
        mock_fn = MagicMock(return_value=42)

        @rl.nse_retry(max_retries=3, base_delay=0.0)
        def fn():
            return mock_fn()

        assert fn() == 42
        assert mock_fn.call_count == 1

    def test_retries_on_failure_then_succeeds(self):
        mock_fn = MagicMock(side_effect=[RuntimeError("err"), RuntimeError("err"), 99])

        @rl.nse_retry(max_retries=3, base_delay=0.0)
        def fn():
            return mock_fn()

        assert fn() == 99
        assert mock_fn.call_count == 3

    def test_reraises_after_all_retries_exhausted(self):
        mock_fn = MagicMock(side_effect=ConnectionError("down"))

        @rl.nse_retry(max_retries=2, base_delay=0.0)
        def fn():
            return mock_fn()

        with pytest.raises(ConnectionError, match="down"):
            fn()

        assert mock_fn.call_count == 3  # 1 original + 2 retries

    def test_funcname_preserved(self):
        @rl.nse_retry(max_retries=1, base_delay=0.0)
        def my_fetcher():
            pass

        assert my_fetcher.__name__ == "my_fetcher"

    def test_no_retry_when_max_retries_zero(self):
        mock_fn = MagicMock(side_effect=IOError("gone"))

        @rl.nse_retry(max_retries=0, base_delay=0.0)
        def fn():
            return mock_fn()

        with pytest.raises(IOError):
            fn()

        assert mock_fn.call_count == 1

    def test_exponential_backoff_delays(self):
        """Verify sleep durations follow base_delay * 2**n sequence."""
        mock_fn = MagicMock(side_effect=[RuntimeError(), RuntimeError(), RuntimeError()])
        sleep_calls: list[float] = []

        @rl.nse_retry(max_retries=2, base_delay=1.0)
        def fn():
            return mock_fn()

        with patch("core.data.nse_rate_limiter.time.sleep", side_effect=lambda s: sleep_calls.append(s)):
            with pytest.raises(RuntimeError):
                fn()

        # Attempt 0 fails → sleep 1.0 * 2^0 = 1.0
        # Attempt 1 fails → sleep 1.0 * 2^1 = 2.0
        # Attempt 2 fails → re-raise (no more sleep)
        assert len(sleep_calls) == 2
        assert abs(sleep_calls[0] - 1.0) < 1e-9
        assert abs(sleep_calls[1] - 2.0) < 1e-9

    def test_delay_capped_at_30_seconds(self):
        """Exponential delay should never exceed 30 seconds."""
        mock_fn = MagicMock(side_effect=RuntimeError())
        sleep_calls: list[float] = []

        @rl.nse_retry(max_retries=10, base_delay=100.0)  # would be 100 * 2^n without cap
        def fn():
            return mock_fn()

        with patch("core.data.nse_rate_limiter.time.sleep", side_effect=lambda s: sleep_calls.append(s)):
            with pytest.raises(RuntimeError):
                fn()

        assert all(s <= 30.0 for s in sleep_calls), f"Some delays exceeded 30s: {sleep_calls}"


# ---------------------------------------------------------------------------
# Circuit breaker
# ---------------------------------------------------------------------------

class TestCircuitBreaker:
    def setup_method(self):
        _reset()

    def test_initially_closed(self):
        assert not rl.circuit_breaker.is_open()

    def test_single_403_does_not_trip(self):
        rl.circuit_breaker.record_failure(403)
        assert not rl.circuit_breaker.is_open()

    def test_two_failures_do_not_trip(self):
        rl.circuit_breaker.record_failure(429)
        rl.circuit_breaker.record_failure(429)
        assert not rl.circuit_breaker.is_open()

    def test_three_consecutive_failures_open_circuit(self):
        for _ in range(rl._TRIP_THRESHOLD):
            rl.circuit_breaker.record_failure(403)
        assert rl.circuit_breaker.is_open()

    def test_non_rate_limit_code_does_not_trip(self):
        """500 / None errors should not trip the circuit breaker."""
        for _ in range(10):
            rl.circuit_breaker.record_failure(500)
            rl.circuit_breaker.record_failure(None)
        assert not rl.circuit_breaker.is_open()

    def test_success_resets_failure_counter(self):
        rl.circuit_breaker.record_failure(403)
        rl.circuit_breaker.record_failure(403)
        rl.circuit_breaker.record_success()
        # After success, two more failures should not yet trip
        rl.circuit_breaker.record_failure(403)
        rl.circuit_breaker.record_failure(403)
        assert not rl.circuit_breaker.is_open()

    def test_circuit_auto_resets_after_lockout(self):
        """After _LOCKOUT_SECONDS the circuit should auto-reset to CLOSED."""
        for _ in range(rl._TRIP_THRESHOLD):
            rl.circuit_breaker.record_failure(429)
        assert rl.circuit_breaker.is_open()

        # Simulate lockout expiry by overriding _open_since
        past = time.monotonic() - (rl._LOCKOUT_SECONDS + 1)
        rl.circuit_breaker._open_since = past

        assert not rl.circuit_breaker.is_open()  # auto-reset

    def test_circuit_still_open_before_lockout_expires(self):
        for _ in range(rl._TRIP_THRESHOLD):
            rl.circuit_breaker.record_failure(403)

        # Only 1 second has passed — still within lockout
        still_recent = time.monotonic() - 1.0
        rl.circuit_breaker._open_since = still_recent
        assert rl.circuit_breaker.is_open()

    def test_mixed_403_and_429_trip_together(self):
        rl.circuit_breaker.record_failure(403)
        rl.circuit_breaker.record_failure(429)
        rl.circuit_breaker.record_failure(403)
        assert rl.circuit_breaker.is_open()

    def test_success_closes_open_circuit(self):
        """record_success while OPEN should close it."""
        for _ in range(rl._TRIP_THRESHOLD):
            rl.circuit_breaker.record_failure(403)
        assert rl.circuit_breaker.is_open()
        rl.circuit_breaker.record_success()
        assert not rl.circuit_breaker.is_open()


# ---------------------------------------------------------------------------
# reset_for_testing
# ---------------------------------------------------------------------------

class TestResetForTesting:
    def test_reset_clears_last_call(self):
        # Simulate a recent call
        with rl._rl_lock:
            rl._last_call = time.monotonic()
        rl.reset_for_testing()
        assert rl._last_call == 0.0

    def test_reset_closes_open_circuit(self):
        for _ in range(rl._TRIP_THRESHOLD):
            rl.circuit_breaker.record_failure(429)
        assert rl.circuit_breaker.is_open()
        rl.reset_for_testing()
        assert not rl.circuit_breaker.is_open()


# ---------------------------------------------------------------------------
# Integration: circuit open → nse_vix / nse_pcr / nse_fii_dii return fallback
# ---------------------------------------------------------------------------

class TestCircuitBreakerIntegration:
    """Verify that when the circuit is open, the fetch functions return None/[]
    without making HTTP calls."""

    def setup_method(self):
        _reset()

    def test_vix_returns_none_when_circuit_open(self):
        import importlib
        import core.data.nse_vix as vix_mod

        # Open the circuit
        for _ in range(rl._TRIP_THRESHOLD):
            rl.circuit_breaker.record_failure(403)
        assert rl.circuit_breaker.is_open()

        mock_requests = MagicMock()
        mock_requests.get.side_effect = AssertionError("Should not have made HTTP call")

        with patch.dict("sys.modules", {"requests": mock_requests}):
            importlib.reload(vix_mod)
            vix_mod.clear_cache()
            result = vix_mod._fetch_vix()

        assert result is None
        mock_requests.get.assert_not_called()

    def test_pcr_returns_none_when_circuit_open(self):
        import importlib
        import core.data.nse_pcr as pcr_mod

        for _ in range(rl._TRIP_THRESHOLD):
            rl.circuit_breaker.record_failure(429)

        mock_requests = MagicMock()
        mock_session = MagicMock()
        mock_requests.Session.return_value = mock_session
        mock_session.get.side_effect = AssertionError("Should not have made HTTP call")

        with patch.dict("sys.modules", {"requests": mock_requests}):
            importlib.reload(pcr_mod)
            pcr_mod.clear_cache()
            result = pcr_mod._fetch_pcr()

        assert result is None
        mock_session.get.assert_not_called()

    def test_fii_dii_returns_empty_when_circuit_open(self):
        import importlib
        import core.data.nse_fii_dii as fii_mod

        for _ in range(rl._TRIP_THRESHOLD):
            rl.circuit_breaker.record_failure(403)

        mock_requests = MagicMock()
        mock_requests.get.side_effect = AssertionError("Should not have made HTTP call")

        with patch.dict("sys.modules", {"requests": mock_requests}):
            importlib.reload(fii_mod)
            fii_mod.clear_cache()
            result = fii_mod._fetch_flows()

        assert result == []
        mock_requests.get.assert_not_called()
