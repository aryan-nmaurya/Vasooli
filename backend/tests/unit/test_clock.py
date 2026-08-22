"""Time is the easiest thing to get subtly wrong in a cadence-driven system."""

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import pytest

from app.core.clock import IST, days_overdue, to_ist_date

DUE = datetime(2026, 8, 1, 18, 30, tzinfo=UTC)  # 2026-08-02 00:00 IST


@pytest.mark.parametrize(
    ("as_of_ist", "expected"),
    [
        ("2026-08-02 00:30", 0),  # just became due
        ("2026-08-02 23:59", 0),
        ("2026-08-05 00:01", 3),  # Tier 1 becomes eligible
        ("2026-08-05 23:59", 3),  # ...and stays eligible all day
        ("2026-08-12 10:00", 10),  # Tier 2
        ("2026-08-23 10:00", 21),  # Tier 3
    ],
)
def test_days_overdue_is_stable_across_the_ist_day(as_of_ist, expected):
    """The count must not tick over mid-day for an Indian merchant.

    Naive UTC subtraction gives 2 days at 09:00 IST and 3 at 18:00 on the same date,
    which would fire Tier 1 at different times for different invoices.
    """
    as_of = datetime.fromisoformat(as_of_ist).replace(tzinfo=IST)
    assert days_overdue(DUE, as_of=as_of) == expected


def test_not_yet_due_is_zero_never_negative():
    as_of = datetime(2026, 7, 20, tzinfo=UTC)
    assert days_overdue(DUE, as_of=as_of) == 0


def test_naive_datetimes_are_treated_as_utc():
    assert to_ist_date(datetime(2026, 8, 1, 18, 30)) == to_ist_date(DUE)


def test_utc_evening_is_already_the_next_ist_day():
    """The +5:30 boundary — 20:00 UTC is tomorrow in India."""
    assert to_ist_date(datetime(2026, 8, 1, 20, 0, tzinfo=UTC)).day == 2


def test_ist_offset_is_correct():
    assert ZoneInfo("Asia/Kolkata").utcoffset(datetime(2026, 8, 1)).total_seconds() == 19800
