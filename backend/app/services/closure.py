"""Close a payment link once its invoice is recovered. Doc §3 Stage 5.

"Stop recovery" has to mean stopping the payment route as well, not just the emails.
A recovered invoice whose link is still live is a customer who can pay a second time
into a bill that is already settled — a refund conversation nobody wants to have.

**Ordering is the whole design.** Razorpay is called only AFTER the reconciliation
transaction has committed. Holding an external HTTP call open inside a database
transaction turns a slow third party into a lock on the invoice row, and a timeout into
a rolled-back payment. So the money is recorded first and irreversibly; closing the
link is a separate, retryable step that cannot undo it.
"""

import contextlib
from datetime import timedelta

from sqlmodel import Session, select

from app.core.clock import utcnow
from app.core.logging import get_logger
from app.integrations.razorpay_client import (
    RazorpayClient,
    RazorpayPermanentError,
    RazorpayTransientError,
    get_razorpay_client,
)
from app.models import AuditAction, AuditActor, AuditLog, Invoice, PaymentLink
from app.models.payment_link import MAX_CLOSURE_ATTEMPTS, PaymentLinkStatus

log = get_logger("closure")

#: Razorpay's wording when a link is already in a state that cannot be cancelled —
#: verified against the live test API: "cannot cancel or expire a cancelled link".
#: This is not a failure. The goal is that no further money can arrive, and an already
#: cancelled, paid, or expired link satisfies that. Treating it as an error would
#: retry forever against a link that is already exactly where we want it.
_ALREADY_CLOSED_MARKERS = ("cannot cancel", "already cancelled", "already been cancelled")


def _closure_backoff(attempt: int) -> timedelta:
    """Bounded: 1m, 2m, 4m, 8m, capped at 30m."""
    return timedelta(seconds=min(60 * (2 ** max(0, attempt - 1)), 1800))


def _record_closed(session: Session, link: PaymentLink, status: str, note: str) -> None:
    link.status = status
    link.cancelled_at = link.cancelled_at or utcnow()
    link.closure_error = None
    link.next_closure_retry_at = None
    session.add(link)
    session.add(
        AuditLog(
            invoice_id=link.invoice_id,
            actor=AuditActor.RAZORPAY,
            action=AuditAction.PAYMENT_LINK_CLOSED,
            detail={
                "payment_link_id": link.razorpay_payment_link_id,
                "status": status,
                "attempts": link.closure_attempts,
                "note": note,
            },
        )
    )


def close_payment_link(
    session: Session,
    link: PaymentLink,
    *,
    client: RazorpayClient | None = None,
) -> bool:
    """Attempt to close one link. Returns True if it is now closed.

    Called outside any open transaction, and commits its own result. A failure here
    leaves the invoice recovered and the link flagged for retry — never the reverse.
    """
    client = client or get_razorpay_client()
    link.closure_attempts += 1

    try:
        result = client.cancel_payment_link(link.razorpay_payment_link_id)
        _record_closed(session, link, result.status or PaymentLinkStatus.CANCELLED, "cancelled")
        session.commit()
        log.info("closure.cancelled", link_id=link.razorpay_payment_link_id)
        return True

    except RazorpayPermanentError as exc:
        message = str(exc)
        if any(m in message.lower() for m in _ALREADY_CLOSED_MARKERS):
            # Already closed upstream. Confirm what it actually is rather than
            # assuming, so the stored status matches Razorpay's.
            status = PaymentLinkStatus.CANCELLED
            # Best effort. If the confirming fetch also fails we still record it as
            # closed: Razorpay just told us it cannot be cancelled, which means it
            # already is in a state where no further money can arrive.
            with contextlib.suppress(RazorpayPermanentError, RazorpayTransientError):
                status = client.fetch_payment_link(link.razorpay_payment_link_id).status or status
            _record_closed(session, link, status, "already closed upstream")
            session.commit()
            return True

        _record_failure(session, link, message, retryable=False)
        session.commit()
        return False

    except RazorpayTransientError as exc:
        _record_failure(session, link, str(exc), retryable=True)
        session.commit()
        return False


def _record_failure(session: Session, link: PaymentLink, message: str, *, retryable: bool) -> None:
    link.closure_error = message[:500]
    exhausted = link.closure_attempts >= MAX_CLOSURE_ATTEMPTS
    link.next_closure_retry_at = (
        utcnow() + _closure_backoff(link.closure_attempts) if retryable and not exhausted else None
    )
    session.add(link)
    session.add(
        AuditLog(
            invoice_id=link.invoice_id,
            actor=AuditActor.RAZORPAY,
            action=AuditAction.PAYMENT_LINK_CLOSE_FAILED,
            detail={
                "payment_link_id": link.razorpay_payment_link_id,
                "error": message[:300],
                "attempts": link.closure_attempts,
                "retryable": retryable,
                "exhausted": exhausted,
                "next_retry_at": (
                    link.next_closure_retry_at.isoformat() if link.next_closure_retry_at else None
                ),
            },
        )
    )
    log.warning(
        "closure.failed",
        link_id=link.razorpay_payment_link_id,
        error=message[:200],
        retryable=retryable,
    )


def close_link_for_invoice(
    session: Session, invoice_id, *, client: RazorpayClient | None = None
) -> bool:
    """Close the link belonging to a recovered invoice, if there is one to close."""
    link = session.exec(select(PaymentLink).where(PaymentLink.invoice_id == invoice_id)).first()
    if link is None:
        return True

    # Idempotent on what WE recorded, not on the status we inferred from the webhook.
    #
    # Reconciliation marks the link "paid" from the payload, and "paid" is terminal —
    # so a check on `is_open` skips the call entirely and Razorpay is never told. That
    # is nearly harmless (a paid link takes no more money) but it means no
    # cancellation is attempted, no audit record exists, and "recovery stops" rests on
    # an assumption about Razorpay rather than a confirmation from it. `cancelled_at`
    # is written only by this module, so it is the honest marker.
    if link.cancelled_at is not None:
        return True

    return close_payment_link(session, link, client=client)


def retry_pending_closures(
    session: Session, *, client: RazorpayClient | None = None, limit: int = 50
) -> dict[str, int]:
    """Re-attempt closures that failed and whose backoff has elapsed.

    Run by the recovery cycle. Without it a transient Razorpay outage during a payment
    would leave a live link on a settled invoice indefinitely, discoverable only by a
    customer paying twice.
    """
    now = utcnow()
    due = session.exec(
        select(PaymentLink)
        .where(
            PaymentLink.next_closure_retry_at.is_not(None),  # type: ignore[union-attr]
            PaymentLink.next_closure_retry_at <= now,  # type: ignore[operator]
            PaymentLink.closure_attempts < MAX_CLOSURE_ATTEMPTS,
        )
        .limit(limit)
    ).all()

    closed = 0
    for link in due:
        invoice = session.get(Invoice, link.invoice_id)
        if invoice is None or not invoice.is_fully_paid:
            # No longer eligible — the invoice was adjusted, or this row is stale.
            link.next_closure_retry_at = None
            session.add(link)
            continue
        if close_payment_link(session, link, client=client):
            closed += 1

    session.commit()
    if due:
        log.info("closure.retry_complete", attempted=len(due), closed=closed)
    return {"attempted": len(due), "closed": closed}
