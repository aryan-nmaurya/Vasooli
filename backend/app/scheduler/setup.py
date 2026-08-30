"""APScheduler wiring."""

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from app.core.config import settings
from app.core.constants import BUSINESS_TIMEZONE
from app.core.logging import get_logger
from app.scheduler.jobs import (
    billing_reconciliation_job,
    payment_link_sync_job,
    recovery_cycle_job,
    retry_operations_job,
    service_heartbeat_job,
)

log = get_logger("scheduler")

_scheduler: BackgroundScheduler | None = None


def get_scheduler() -> BackgroundScheduler | None:
    """The scheduler running in THIS process, if any.

    Read by the automation-health endpoint. Returns None when the scheduler is disabled
    or lives in another worker — which the caller reports as "not running here" rather
    than as a fault, because the job history in the database is the cross-process
    source of truth.
    """
    return _scheduler


def start_scheduler() -> BackgroundScheduler | None:
    """Start the background scheduler, unless disabled.

    Runs at 10:00 IST: late enough that overnight bank transfers have settled and
    been reconciled, so the cycle does not chase someone who paid last night, and
    early enough to land in a working day.
    """
    global _scheduler
    if not settings.scheduler_enabled or settings.process_role == "worker":
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
    # The safety net for webhooks that never arrived. Hourly rather than daily: a
    # payment made while the receiver was unreachable should be picked up in an hour,
    # not tomorrow. Razorpay stops retrying long before that.
    scheduler.add_job(
        payment_link_sync_job,
        CronTrigger(minute=17, timezone=BUSINESS_TIMEZONE),
        id="payment_link_sync",
        name="Hourly Razorpay sync",
        max_instances=1,
        coalesce=True,
        misfire_grace_time=1800,
        replace_existing=True,
    )
    scheduler.add_job(
        retry_operations_job,
        IntervalTrigger(minutes=1, timezone=BUSINESS_TIMEZONE),
        id="retry_operations",
        name="Due delivery, closure and webhook retries",
        max_instances=1,
        coalesce=True,
        misfire_grace_time=60,
        replace_existing=True,
    )
    scheduler.add_job(
        service_heartbeat_job,
        IntervalTrigger(minutes=5, timezone=BUSINESS_TIMEZONE),
        id="service_heartbeat",
        name="External service dead-man heartbeat",
        max_instances=1,
        coalesce=True,
        misfire_grace_time=120,
        replace_existing=True,
    )
    scheduler.add_job(
        billing_reconciliation_job,
        CronTrigger(hour=3, minute=15, timezone=BUSINESS_TIMEZONE),
        id="billing_reconciliation",
        name="Daily billing reconciliation",
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
