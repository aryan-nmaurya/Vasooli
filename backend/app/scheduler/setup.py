"""APScheduler wiring. Phase 8."""

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from app.core.config import settings
from app.core.constants import BUSINESS_TIMEZONE
from app.core.logging import get_logger
from app.scheduler.jobs import recovery_cycle_job

log = get_logger("scheduler")

_scheduler: BackgroundScheduler | None = None


def start_scheduler() -> BackgroundScheduler | None:
    """Start the background scheduler, unless disabled.

    Runs at 10:00 IST: late enough that overnight bank transfers have settled and
    been reconciled, so the cycle does not chase someone who paid last night, and
    early enough to land in a working day.
    """
    global _scheduler
    if not settings.scheduler_enabled:
        log.info("scheduler.disabled")
        return None
    if _scheduler is not None:
        return _scheduler

    scheduler = BackgroundScheduler(timezone=BUSINESS_TIMEZONE)
    scheduler.add_job(
        recovery_cycle_job,
        CronTrigger(hour=10, minute=0, timezone=BUSINESS_TIMEZONE),
        id="recovery_cycle",
        name="Daily recovery cycle",
        # One at a time, and a missed run collapses into a single catch-up rather
        # than firing once per hour it was down.
        max_instances=1,
        coalesce=True,
        misfire_grace_time=3600,
        replace_existing=True,
    )
    scheduler.start()
    _scheduler = scheduler
    log.info("scheduler.started", jobs=[j.id for j in scheduler.get_jobs()])
    return scheduler


def shutdown_scheduler() -> None:
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None
        log.info("scheduler.stopped")
