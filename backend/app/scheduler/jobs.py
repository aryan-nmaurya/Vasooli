"""Scheduled jobs. Phase 8.

Thin wrappers that own a database session and delegate to app.services. The cycle
logic lives in services because it writes; the scheduler's only job is to decide when.
"""

from sqlmodel import Session

from app.core.db import engine
from app.core.logging import get_logger
from app.services.recovery import run_recovery_cycle

log = get_logger("scheduler")


def recovery_cycle_job() -> None:
    """The daily chase. Errors are logged, never raised.

    An exception escaping into APScheduler would kill the job and, depending on
    configuration, stop it rescheduling — a silent halt is worse than a bad day.
    """
    try:
        with Session(engine) as session:
            report = run_recovery_cycle(session)
        log.info("scheduler.recovery_cycle_done", **report.as_dict())
    except Exception:
        log.exception("scheduler.recovery_cycle_failed")


def payment_link_sync_job() -> None:
    """Reconcile against Razorpay directly, as a safety net for missed webhooks.

    Webhooks are the primary path and are idempotent, so this only matters when a
    delivery is lost entirely. Implemented in a later phase; the slot exists now so
    the schedule is visible.
    """
    log.debug("scheduler.payment_link_sync_noop")
