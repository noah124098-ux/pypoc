"""Economic calendar blackout dates.

Prevents new LONG entries within ``buffer_days`` calendar days before or after
high-impact macro events that tend to spike volatility and widen spreads:
  - RBI MPC meetings (6 per year, every ~6-8 weeks)
  - Union Budget day (Feb 1 each year)
  - US Fed FOMC meetings (8 per year; affect FII flows into India)

Dates are hardcoded for 2024-2026.  They are publicly known well in advance and
do not change.  Add future years as they are announced.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta

# RBI Monetary Policy Committee decision dates (last day of each meeting)
RBI_MPC_DATES: list[str] = [
    "2024-02-08", "2024-04-05", "2024-06-07", "2024-08-08", "2024-10-09", "2024-12-06",
    "2025-02-07", "2025-04-09", "2025-06-06", "2025-08-06", "2025-10-08", "2025-12-05",
    "2026-02-06", "2026-04-09", "2026-06-05",
]

# Union Budget presentation date (Feb 1 each year)
BUDGET_DATES: list[str] = [
    "2024-02-01", "2025-02-01", "2026-02-01",
]

# US Federal Open Market Committee decision dates (last day of each meeting)
FOMC_DATES: list[str] = [
    "2024-01-31", "2024-03-20", "2024-05-01", "2024-06-12", "2024-07-31",
    "2024-09-18", "2024-11-07", "2024-12-18",
    "2025-01-29", "2025-03-19", "2025-05-07", "2025-06-18", "2025-07-30",
    "2025-09-17", "2025-11-05", "2025-12-17",
    "2026-01-28", "2026-03-18", "2026-05-06", "2026-06-17",
]

# Parse all dates once at import time.
_ALL_EVENT_DATES: list[date] = [
    date.fromisoformat(d)
    for d in RBI_MPC_DATES + BUDGET_DATES + FOMC_DATES
]


def is_blackout_day(dt: datetime | date, buffer_days: int = 2) -> bool:
    """Return True if *dt* falls within *buffer_days* of any high-impact event.

    Parameters
    ----------
    dt:
        The date to check.  A ``datetime`` object is accepted and its date
        component is used.
    buffer_days:
        Number of calendar days before **and** after each event to block.
        Default is 2.  Pass 0 to match exact dates only.
    """
    if isinstance(dt, datetime):
        check = dt.date()
    elif isinstance(dt, date):
        check = dt
    else:
        raise TypeError(f"Expected date or datetime, got {type(dt)!r}")

    for event in _ALL_EVENT_DATES:
        if abs((check - event).days) <= buffer_days:
            return True
    return False
