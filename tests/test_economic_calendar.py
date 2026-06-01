"""Tests for the economic calendar blackout logic."""
from __future__ import annotations

from datetime import date, datetime

import pytest

from core.data.economic_calendar import is_blackout_day


class TestIsBlackoutDay:
    # --- exact event matches (buffer_days=0) ---

    def test_budget_day_is_blackout_buffer0(self):
        """Feb 1 any covered year is a blackout with buffer=0."""
        assert is_blackout_day(date(2025, 2, 1), buffer_days=0) is True

    def test_rbi_mpc_day_is_blackout_buffer0(self):
        """Feb 8 2024 (RBI MPC date) is a blackout with buffer=0."""
        assert is_blackout_day(date(2024, 2, 8), buffer_days=0) is True

    def test_fomc_day_is_blackout_buffer0(self):
        """Sep 18 2024 (FOMC date) is a blackout with buffer=0."""
        assert is_blackout_day(date(2024, 9, 18), buffer_days=0) is True

    # --- buffer days (default = 2) ---

    def test_day_before_budget_is_blackout(self):
        """Jan 31 is 1 day before Feb 1 Budget; within buffer=2."""
        assert is_blackout_day(date(2025, 1, 31)) is True

    def test_two_days_before_budget_is_blackout(self):
        """Jan 30 is exactly 2 days before; should still be blocked."""
        assert is_blackout_day(date(2025, 1, 30)) is True

    def test_day_after_rbi_mpc_is_blackout(self):
        """Feb 9 2024 is 1 day after the Feb 8 RBI date; within buffer=2."""
        assert is_blackout_day(date(2024, 2, 9)) is True

    def test_two_days_after_rbi_mpc_is_blackout(self):
        """Feb 10 2024 is 2 days after; still blocked."""
        assert is_blackout_day(date(2024, 2, 10)) is True

    # --- non-blackout dates ---

    def test_random_day_is_not_blackout(self):
        """March 15 2025 is not near any event and should return False."""
        assert is_blackout_day(date(2025, 3, 15)) is False

    def test_three_days_after_event_is_not_blackout(self):
        """3 days after an event is outside the 2-day buffer."""
        assert is_blackout_day(date(2024, 2, 11)) is False  # Feb 8 + 3

    def test_three_days_before_event_is_not_blackout(self):
        """Jan 26 2025 is 6 days before Budget day (Feb 1) — outside the 2-day buffer.
        Also not near any FOMC or RBI date in that window."""
        assert is_blackout_day(date(2025, 1, 26)) is False

    # --- buffer_days=0 does not leak to nearby days ---

    def test_day_before_rbi_not_blackout_with_buffer0(self):
        """buffer_days=0 should not trigger on Feb 7 when event is Feb 8."""
        assert is_blackout_day(date(2024, 2, 7), buffer_days=0) is False

    def test_day_after_rbi_not_blackout_with_buffer0(self):
        """buffer_days=0 should not trigger on Feb 9 when event is Feb 8."""
        assert is_blackout_day(date(2024, 2, 9), buffer_days=0) is False

    # --- datetime objects are accepted ---

    def test_accepts_datetime_object(self):
        """is_blackout_day should work with a datetime, not just a date."""
        dt = datetime(2025, 2, 1, 9, 15, 0)
        assert is_blackout_day(dt) is True

    def test_accepts_datetime_non_event(self):
        """Datetime for a non-event day returns False."""
        dt = datetime(2025, 3, 15, 10, 0, 0)
        assert is_blackout_day(dt) is False

    # --- invalid input ---

    def test_invalid_type_raises(self):
        """Passing a string should raise TypeError."""
        with pytest.raises(TypeError):
            is_blackout_day("2025-02-01")  # type: ignore[arg-type]
