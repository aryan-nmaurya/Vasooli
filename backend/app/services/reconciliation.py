"""Match incoming money to invoices. Doc §3 Stage 5, §6.

Deliberately free of any language model. Money-matching is deterministic, auditable
logic; a model that is right 97% of the time is wrong about three invoices in every
hundred, and each of those is either a customer chased after paying or revenue counted
that never arrived.

Matching never uses the amount. Two customers owing ₹25,000 is ordinary, and an
amount-based match would resolve it by guessing.
"""

import uuid
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
        event.processed_at = utcnow()
        session.add(event)
        session.commit()
        return

    invoice, link, strategy = match_invoice(session, payload)

    if invoice is None:
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
    previous_paid = locked.amount_paid_paise
    locked.amount_paid_paise = max(previous_paid, reported_paid)

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
    event.amount_paise = reported_paid - previous_paid
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
                "applied_paise": reported_paid - previous_paid,
                "total_paid_paise": locked.amount_paid_paise,
                "amount_paise": locked.amount_paise,
                "new_status": locked.status,
            },
        )
    )
    session.commit()

    log.info(
        "reconciliation.applied",
        invoice_number=locked.invoice_number,
        total_paid_paise=locked.amount_paid_paise,
        status=locked.status,
        match_strategy=strategy,
    )
