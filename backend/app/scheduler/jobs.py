"""Scheduled jobs. Phase 8.

Thin wrappers that own a database session and delegate to app.services. The cycle
logic lives in services because it writes; the scheduler's only job is to decide when.
"""

from sqlmodel import Session

from app.core.db import engine
from app.core.logging import get_logger
from app.services.recovery import run_recovery_cycle
from app.services.sync import sync_payment_links

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
    delivery is lost entirely — a payment made while the receiver was unreachable.
    Razorpay eventually stops retrying, and without this the money would sit
    unrecorded indefinitely.
    """
    try:
        with Session(engine) as session:
            report = sync_payment_links(session)
        if report["checked"]:
            log.info("scheduler.payment_link_sync_done", **report)
    except Exception:
        log.exception("scheduler.payment_link_sync_failed")
