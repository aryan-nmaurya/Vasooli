"""The single source of time.

Every business-logic time read goes through this module. Two reasons:

1. The cadence is measured in days past due (Doc §3 Stage 3), but a demo cannot wait
   3, 10, and 21 real days. Two shifts exist for that: `DEMO_TIME_OFFSET_DAYS`, a
   static boot-time offset banned in production, and a runtime offset moved through an
   audited endpoint (see app.services.demo_control). Both only work if nothing calls
   `datetime.now()` directly.
2. Overdue-day counts must match what a merchant in India sees on their calendar, so
   day math is done in IST while storage stays UTC.

Enforced by tests/architecture/test_layering.py: `datetime.now`/`utcnow` are banned
outside this file.
"""

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from app.core.config import settings
from app.core.constants import BUSINESS_TIMEZONE

IST = ZoneInfo(BUSINESS_TIMEZONE)
UTC = ZoneInfo("UTC")


#: Runtime demo offset, in days, on top of the static one from settings.
#:
#: Held in module state rather than read from the database on every call: `utcnow()`
#: runs on essentially every code path, and a query per time read would be both slow
#: and a layering violation — app.core may not reach app.core.db. The durable copy
#: lives in the `demo_settings` table; app.services.demo_control owns writing to both and
#: the app lifespan loads it at startup.
_runtime_offset_days = 0


def set_runtime_offset(days: int) -> None:
    """Move the demo clock. Called only by app.services.demo_control."""
    global _runtime_offset_days
    _runtime_offset_days = max(0, int(days))


def runtime_offset() -> int:
    return _runtime_offset_days


def _offset() -> timedelta:
    return timedelta(days=settings.demo_time_offset_days + _runtime_offset_days)


def utcnow() -> datetime:
    """Timezone-aware UTC now, shifted by the demo offset."""
    return datetime.now(UTC) + _offset()


def real_now_ist() -> datetime:
    """Unshifted wall-clock time in IST, ignoring every demo offset.

    The one legitimate reason to want the real present: showing a reviewer what the
    actual date is next to what the system currently believes it is. Business logic
    must never call this — if it did, the demo clock would apply to some decisions
    and not others, which is worse than not having one.
    """
    return datetime.now(UTC).astimezone(IST)


def now_ist() -> datetime:
    """Timezone-aware IST now, shifted by the demo offset."""
    return utcnow().astimezone(IST)


def today_ist() -> date:
    """The current business date in India."""
    return now_ist().date()


def to_ist_date(moment: datetime) -> date:
    """The IST calendar date a UTC timestamp falls on.

    Naive datetimes are assumed UTC — Postgres TIMESTAMPTZ round-trips as aware, but
    hand-built test fixtures often are not.
    """
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return moment.astimezone(IST).date()


def days_overdue(due_at: datetime, *, as_of: datetime | None = None) -> int:
    """Whole days an invoice is past due, counted on the IST calendar.

    Both sides are collapsed to IST dates first, so an invoice due 2026-08-01 is
    exactly 3 days overdue at any time on 2026-08-04 IST — not 2 days at 09:00 and
    3 days at 18:00, which is what naive UTC subtraction produces for Indian users.
    Never negative: an invoice that is not yet due is 0 days overdue, not -5.
    """
    reference = as_of or utcnow()
    return max(0, (to_ist_date(reference) - to_ist_date(due_at)).days)


def ist_midnight(day: date) -> datetime:
    """The UTC instant at which `day` begins in India.

    Invoice due dates are calendar dates, not instants — "due 1 August" means the
    close of 1 August in the merchant's own timezone. Anchoring to IST midnight and
    storing the UTC equivalent is what makes `days_overdue` tick over at the right
    moment; anchoring to UTC midnight would shift every boundary by 5.5 hours.
    """
    return datetime(day.year, day.month, day.day, tzinfo=IST).astimezone(UTC)


def due_date_for_days_overdue(days: int, *, as_of: date | None = None) -> datetime:
    """The due date an invoice needs in order to be exactly `days` overdue today.

    Used by the seeder to rebase generated ledgers onto the current date, so a demo
    CSV written last week still lands invoices on the tier boundaries today.
    """
    reference = as_of or today_ist()
    return ist_midnight(reference - timedelta(days=days))
