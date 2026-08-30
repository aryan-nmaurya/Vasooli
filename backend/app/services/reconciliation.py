"""Match incoming money to invoices. Doc §3 Stage 5, §6.

Deliberately free of any language model. Money-matching is deterministic, auditable
logic; a model that is right 97% of the time is wrong about three invoices in every
hundred, and each of those is either a customer chased after paying or revenue counted
that never arrived.

Matching never uses the amount. Two customers owing ₹25,000 is ordinary, and an
amount-based match would resolve it by guessing.
"""

import uuid
from datetime import timedelta
from typing import Any

from sqlmodel import Session, select

from app.core.clock import utcnow
from app.core.constants import InvoiceStatus, PromiseStatus
from app.core.logging import get_logger
from app.models import (
    AuditAction,
    AuditActor,
    AuditLog,
    Invoice,
    PaymentLink,
    PaymentLinkStatus,
    Promise,
    ReconciliationEvent,
)
from app.models.reconciliation_event import MAX_EVENT_ATTEMPTS, EventStatus
from app.services.closure import close_link_for_invoice
from app.services.disputes import open_case_for

log = get_logger("reconciliation")

#: Events that can move money against an invoice.
PAYMENT_EVENTS = frozenset({"payment_link.paid", "payment_link.partially_paid"})


class MatchStrategy:
    """How an invoice was identified. Recorded so a match that only succeeded on a
    fallback path is visible rather than indistinguishable from a clean one."""

    PAYMENT_LINK_ID = "payment_link_id"
    NOTES_INVOICE_ID = "notes.invoice_id"
    REFERENCE_ID = "reference_id"


def _link_entity(payload: dict[str, Any]) -> dict[str, Any]:
    return (payload.get("payload", {}).get("payment_link", {}) or {}).get("entity", {}) or {}


def match_invoice(
    session: Session, payload: dict[str, Any]
) -> tuple[Invoice | None, PaymentLink | None, str | None]:
    """Identify the invoice a payment belongs to.

    Three independent paths, tried in order of directness. Any one of them is
    sufficient; having three means a payload missing one field still reconciles
    instead of landing in the manual queue.
    """
    entity = _link_entity(payload)

    link_id = entity.get("id")
    if link_id:
        link = session.exec(
            select(PaymentLink).where(PaymentLink.razorpay_payment_link_id == link_id)
        ).first()
        if link:
            return session.get(Invoice, link.invoice_id), link, MatchStrategy.PAYMENT_LINK_ID

    notes = entity.get("notes") or {}
    raw_invoice_id = notes.get("invoice_id")
    if raw_invoice_id:
        try:
            invoice = session.get(Invoice, uuid.UUID(str(raw_invoice_id)))
        except ValueError:
            invoice = None
        if invoice:
            link = session.exec(
                select(PaymentLink).where(PaymentLink.invoice_id == invoice.id)
            ).first()
            return invoice, link, MatchStrategy.NOTES_INVOICE_ID

    reference_id = entity.get("reference_id")
    if reference_id:
        link = session.exec(
            select(PaymentLink).where(PaymentLink.reference_id == reference_id)
        ).first()
        if link:
            return session.get(Invoice, link.invoice_id), link, MatchStrategy.REFERENCE_ID

    return None, None, None


def _resolve_active_promise(session: Session, invoice: Invoice, status: str) -> None:
    promise = session.exec(
        select(Promise).where(
            Promise.invoice_id == invoice.id,
            Promise.status == PromiseStatus.ACTIVE,
        )
    ).first()
    if promise is None:
        return
    promise.status = status
    promise.resolved_at = utcnow()
    session.add(promise)
    session.add(
        AuditLog(
            invoice_id=invoice.id,
            actor=AuditActor.SYSTEM,
            action=AuditAction.PROMISE_KEPT,
            detail={"promise_id": str(promise.id), "promised_date": str(promise.promised_date)},
        )
    )


def process_event(session: Session, event: ReconciliationEvent) -> None:
    """Apply one verified, de-duplicated webhook.

    Runs in its own transaction, after the event row is already committed. If this
    raises, the event stays recorded with an error rather than vanishing — a payment
    that failed to reconcile must be visible, not lost.
    """
    payload = event.raw_payload

    if event.event_type not in PAYMENT_EVENTS:
        # Not an error. Cancellations and creations are stored for the audit trail and
        # deliberately move no money.
        event.status = EventStatus.IGNORED
        event.processed_at = utcnow()
        event.next_retry_at = None
        session.add(event)
        session.commit()
        return

    invoice, link, strategy = match_invoice(session, payload)

    if invoice is None:
        # Terminal, not retryable: retrying cannot conjure a matching invoice. It needs
        # a human to identify what this payment was for.
        event.status = EventStatus.FAILED
        event.attempts = MAX_EVENT_ATTEMPTS
        event.next_retry_at = None
        event.processing_error = "unmatched_payment"
        event.processed_at = utcnow()
        session.add(event)
        session.add(
            AuditLog(
                invoice_id=None,
                actor=AuditActor.RAZORPAY,
                action=AuditAction.RECONCILIATION_UNMATCHED,
                detail={
                    "event_id": event.provider_event_id,
                    "payment_link_id": _link_entity(payload).get("id"),
                    "reference_id": _link_entity(payload).get("reference_id"),
                },
            )
        )
        session.commit()
        log.warning("reconciliation.unmatched", event_id=event.provider_event_id)
        return

    entity = _link_entity(payload)
    # Razorpay reports the running total paid against the link, so this is set rather
    # than incremented. Incrementing would double-count a redelivered event, and
    # `max` additionally makes an out-of-order delivery harmless: a stale event
    # carrying a smaller total cannot walk the balance backwards.
    reported_paid = int(entity.get("amount_paid") or 0)

    locked = session.exec(select(Invoice).where(Invoice.id == invoice.id).with_for_update()).one()
    # `max()` applies to the LINK total only. It is what makes a redelivered or
    # out-of-order webhook harmless — a stale event carrying a smaller total cannot walk
    # the balance backwards — but it is only correct about a figure that is genuinely a
    # restatement of one running total. Operator-entered payments are separate,
    # additive transactions, so they live in their own column and are added afterwards.
    # Sharing one column would make a hand-recorded bank transfer look like a newer,
    # larger provider total, and every subsequent real link payment would be discarded
    # by this `max()` as stale.
    previous_link_paid = locked.link_paid_paise
    locked.link_paid_paise = max(previous_link_paid, reported_paid)
    applied_paise = locked.link_paid_paise - previous_link_paid
    previous_paid = locked.amount_paid_paise
    locked.amount_paid_paise = locked.link_paid_paise + locked.external_paid_paise

    # A payment can land while a dispute is being worked, and when it does Razorpay
    # wins. The customer's objection does not stop the money being recorded, the
    # invoice being closed, or the link being cancelled — this is verified payment
    # truth and nothing in the conversation layer outranks it.
    #
    # The dispute case is NOT closed here. Paying under protest is a real thing, and
    # only a person decides an objection was settled. What the case gets is a record
    # that money arrived while it was open, which is exactly what whoever is handling
    # it needs to see next.
    dispute = open_case_for(session, locked.id)

    if locked.is_fully_paid:
        locked.status = InvoiceStatus.RECOVERED
        locked.recovered_at = locked.recovered_at or utcnow()
        _resolve_active_promise(session, locked, PromiseStatus.KEPT)
    elif locked.amount_paid_paise > 0:
        # Partial payment does not close the invoice. The customer paid something,
        # which is worth recording, but the balance is still owed and the invoice
        # stays in the queue.
        locked.status = InvoiceStatus.PARTIALLY_PAID

    if link is not None:
        link.amount_paid_paise = max(link.amount_paid_paise, reported_paid)
        link.status = entity.get("status") or link.status
        if locked.is_fully_paid:
            link.status = PaymentLinkStatus.PAID
        session.add(link)

    event.matched_invoice_id = locked.id
    event.match_strategy = strategy
    event.amount_paise = applied_paise
    event.status = EventStatus.PROCESSED
    event.processing_error = None
    event.next_retry_at = None
    event.processed_at = utcnow()
    session.add(event)
    session.add(locked)

    session.add(
        AuditLog(
            invoice_id=locked.id,
            actor=AuditActor.RAZORPAY,
            action=AuditAction.PAYMENT_RECONCILED,
            detail={
                "event_id": event.provider_event_id,
                "event_type": event.event_type,
                "match_strategy": strategy,
                "applied_paise": applied_paise,
                "stale_provider_total": reported_paid < previous_paid,
                "total_paid_paise": locked.amount_paid_paise,
                "amount_paise": locked.amount_paise,
                "new_status": locked.status,
            },
        )
    )
    if dispute is not None:
        session.add(
            AuditLog(
                invoice_id=locked.id,
                actor=AuditActor.RAZORPAY,
                action=AuditAction.PAYMENT_DURING_DISPUTE,
                detail={
                    "case_id": str(dispute.id),
                    "event_id": event.provider_event_id,
                    "applied_paise": applied_paise,
                    "total_paid_paise": locked.amount_paid_paise,
                    "new_status": locked.status,
                    "dispute_still_open": True,
                    "note": (
                        "Verified payment recorded while a dispute was open. Recovery "
                        "is stopped by the payment; the dispute stays open for a human."
                    ),
                },
            )
        )
        log.warning(
            "reconciliation.payment_during_dispute",
            invoice_number=locked.invoice_number,
            case_id=str(dispute.id),
        )

    session.commit()

    # --- External side effect, strictly after the money is committed ---------
    #
    # The payment is now durable. Closing the link is a separate step that can fail
    # without touching it. Doing this inside the transaction above would hold a lock
    # on the invoice row across an HTTP call, and a Razorpay timeout would roll back a
    # payment that genuinely arrived.
    #
    # Only on FULL payment: a partially paid invoice still needs its link open for the
    # customer to pay the balance.
    if locked.is_fully_paid:
        try:
            close_link_for_invoice(session, locked.id)
        except Exception:  # noqa: BLE001
            # Never let a closure problem surface as a reconciliation failure. The
            # money is recorded; the link is flagged for retry inside close_*.
            log.exception("reconciliation.closure_failed", invoice_number=locked.invoice_number)

    log.info(
        "reconciliation.applied",
        invoice_number=locked.invoice_number,
        total_paid_paise=locked.amount_paid_paise,
        status=locked.status,
        match_strategy=strategy,
    )


def _event_backoff(attempt: int) -> timedelta:
    """Bounded exponential backoff: 30s, 1m, 2m, 4m, 8m, capped at 15m."""
    return timedelta(seconds=min(30 * (2 ** max(0, attempt - 1)), 900))


def begin_attempt(session: Session, event: ReconciliationEvent) -> None:
    """Count an attempt before processing starts.

    Counted here, at the boundary, rather than inside `process_event`: a failure
    raised before the counter would otherwise leave `attempts` at zero, the backoff
    would never widen, and the retry limit would never be reached.
    """
    event.attempts += 1
    event.last_attempt_at = utcnow()
    session.add(event)


def mark_event_failed(session: Session, event: ReconciliationEvent, error: str) -> None:
    """Record a processing failure so it can be found and retried.

    Called when reconciliation raises. The webhook was already acknowledged with 200 —
    that is what stops Razorpay redelivering — so without this the failure would exist
    only in a log line, and a genuinely received payment would look like an unpaid
    invoice forever.
    """
    event.status = EventStatus.FAILED
    event.processing_error = f"{error}"[:500]
    event.last_attempt_at = utcnow()
    event.next_retry_at = (
        utcnow() + _event_backoff(event.attempts)
        if event.attempts < MAX_EVENT_ATTEMPTS
        else None  # exhausted: an operator has to look at it
    )
    session.add(event)
    session.add(
        AuditLog(
            invoice_id=event.matched_invoice_id,
            actor=AuditActor.RAZORPAY,
            action=AuditAction.RECONCILIATION_FAILED,
            detail={
                "event_id": event.provider_event_id,
                "event_type": event.event_type,
                "error": event.processing_error,
                "attempts": event.attempts,
                "next_retry_at": (event.next_retry_at.isoformat() if event.next_retry_at else None),
                "exhausted": event.attempts >= MAX_EVENT_ATTEMPTS,
            },
        )
    )
    session.commit()
    log.warning(
        "reconciliation.failed",
        event_id=event.provider_event_id,
        attempts=event.attempts,
        error=event.processing_error,
    )


def reprocess_event(session: Session, event: ReconciliationEvent) -> bool:
    """Re-run reconciliation for one failed event. Returns True on success.

    Safe to call repeatedly. `process_event` reads the running total Razorpay reports
    and applies it with `max()`, so re-running against an invoice that already settled
    changes nothing — the same property that makes duplicate webhooks harmless makes
    retries harmless.
    """
    if event.status == EventStatus.PROCESSED:
        return True

    begin_attempt(session, event)
    attempts = event.attempts
    session.commit()

    try:
        process_event(session, event)
    except Exception as exc:  # noqa: BLE001
        session.rollback()
        session.refresh(event)
        # The rollback undid the attempt counter too; restore it so the backoff and
        # the retry limit still advance.
        event.attempts = attempts
        mark_event_failed(session, event, f"{type(exc).__name__}: {exc}")
        return False

    session.refresh(event)
    return event.status == EventStatus.PROCESSED


def retry_failed_events(
    session: Session, *, limit: int = 50, force_ids: list[str] | None = None
) -> dict[str, int]:
    """Reprocess failed events whose backoff has elapsed.

    `force_ids` bypasses the backoff for an operator pressing "retry" on a specific
    event — including exhausted ones, which is the whole point of a manual retry.
    """
    if force_ids:
        due = list(
            session.exec(
                select(ReconciliationEvent).where(
                    ReconciliationEvent.provider_event_id.in_(force_ids)  # type: ignore[attr-defined]
                )
            ).all()
        )
    else:
        now = utcnow()
        due = list(
            session.exec(
                select(ReconciliationEvent)
                .where(
                    ReconciliationEvent.status == EventStatus.FAILED,
                    ReconciliationEvent.attempts < MAX_EVENT_ATTEMPTS,
                    ReconciliationEvent.next_retry_at.is_not(None),  # type: ignore[union-attr]
                    ReconciliationEvent.next_retry_at <= now,  # type: ignore[operator]
                )
                .limit(limit)
            ).all()
        )

    recovered = sum(1 for event in due if reprocess_event(session, event))
    if due:
        log.info("reconciliation.retry_complete", attempted=len(due), recovered=recovered)
    return {"attempted": len(due), "recovered": recovered}
