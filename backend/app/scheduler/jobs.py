"""Scheduled jobs. Phase 8.

Thin wrappers that own a database session and delegate to app.services. The cycle
logic lives in services because it writes; the scheduler's only job is to decide when.
"""

import httpx
from sqlmodel import Session

from app.core.config import settings
from app.core.db import engine
from app.core.logging import get_logger
from app.services.closure import retry_pending_closures
from app.services.messaging import retry_failed_deliveries
from app.services.reconciliation import retry_failed_events
from app.services.recovery import run_recovery_cycle
from app.services.sync import sync_payment_links

log = get_logger("scheduler")


def _heartbeat(url: str, *, check: str) -> None:
    """Ping an external dead-man monitor; the secret URL is never logged."""
    if not url:
        return
    response = httpx.get(url, timeout=5.0)
    response.raise_for_status()
    log.info("scheduler.heartbeat_sent", check=check)


def recovery_cycle_job() -> None:
    """The daily chase. Errors are logged, never raised.

    An exception escaping into APScheduler would kill the job and, depending on
    configuration, stop it rescheduling — a silent halt is worse than a bad day.
    """
    try:
        with Session(engine) as session:
            report = run_recovery_cycle(session)
        log.info("scheduler.recovery_cycle_done", **report.as_dict())
        _heartbeat(settings.ops_recovery_heartbeat_url, check="recovery_cycle")
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


def retry_operations_job() -> None:
    """Run due retry backoffs independently of the once-daily recovery cycle."""
    try:
        with Session(engine) as session:
            deliveries = retry_failed_deliveries(session)
            closures = retry_pending_closures(session)
            events = retry_failed_events(session)
        if deliveries["attempted"] or closures["attempted"] or events["attempted"]:
            log.info(
                "scheduler.retry_sweep_done",
                deliveries=deliveries,
                closures=closures,
                events=events,
            )
    except Exception:
        log.exception("scheduler.retry_sweep_failed")


def service_heartbeat_job() -> None:
    """External proof that the process, scheduler thread, and network are alive."""
    try:
        _heartbeat(settings.ops_heartbeat_url, check="service")
    except Exception:
        log.exception("scheduler.heartbeat_failed", check="service")
