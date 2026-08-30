"""Money an operator records or matches by hand.

Separate router from `dashboard` because the risk profile is different. Everything here
changes what the system believes has been paid, without a provider signature behind it,
so each endpoint records who did it and answers with the resulting balance rather than
just "ok".
"""

import uuid
from datetime import date

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field
from sqlmodel import select

from app.api.deps import Operator, OperatorRequired
from app.core.db import SessionDep
from app.core.money import format_inr
from app.models import (
    AuditAction,
    AuditActor,
    AuditLog,
    ExternalPayment,
    Invoice,
    PaymentMethod,
    ReconciliationEvent,
)
from app.models.reconciliation_event import EventStatus
from app.services.manual_payments import (
    METHOD_LABELS,
    ManualPaymentError,
    payment_view,
    record_external_payment,
    reverse_external_payment,
)

router = APIRouter(prefix="/api", tags=["payments"], dependencies=[OperatorRequired])


class RecordPayment(BaseModel):
    """An operator asserting that money arrived.

    `amount_paise`, not rupees. Money crosses this boundary as an integer or not at
    all — a float amount is how ₹1 goes missing between two languages.
    """

    amount_paise: int = Field(gt=0, le=10_000_000_000)
    method: str
    reference: str = Field(min_length=1, max_length=200)
    received_on: date
    note: str = Field(default="", max_length=1000)


class ReversePayment(BaseModel):
    reason: str = Field(min_length=1, max_length=500)


class MatchEvent(BaseModel):
    """Tie an unmatched Razorpay settlement to an invoice."""

    invoice_id: uuid.UUID
    note: str = Field(default="", max_length=1000)


def _balance_view(invoice: Invoice) -> dict:
    """The three figures separately, never just the total.

    An operator deciding whether to chase needs to know which part of the balance
    Razorpay verified and which part a colleague typed in. Collapsing them into one
    number is how an unverified claim starts reading like a settled payment.
    """
    return {
        "invoice_number": invoice.invoice_number,
        "status": str(invoice.status),
        "amount_display": format_inr(invoice.amount_paise),
        "paid_display": format_inr(invoice.amount_paid_paise),
        "outstanding_display": format_inr(invoice.outstanding_paise),
        "link_paid_display": format_inr(invoice.link_paid_paise),
        "external_paid_display": format_inr(invoice.external_paid_paise),
        "fully_paid": invoice.is_fully_paid,
    }


@router.get("/payments/methods")
def payment_methods() -> list[dict[str, str]]:
    """What the record-a-payment form may offer."""
    return [{"value": value, "label": label} for value, label in METHOD_LABELS.items()]


@router.get("/dashboard/invoices/{invoice_id}/payments")
def list_payments(invoice_id: uuid.UUID, session: SessionDep) -> dict:
    """Every hand-recorded payment against one invoice, reversed entries included."""
    invoice = session.get(Invoice, invoice_id)
    if invoice is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Invoice not found")
    rows = session.exec(
        select(ExternalPayment)
        .where(ExternalPayment.invoice_id == invoice_id)
        .order_by(ExternalPayment.recorded_at.desc())  # type: ignore[attr-defined]
    ).all()
    return {"balance": _balance_view(invoice), "payments": [payment_view(p) for p in rows]}


@router.post("/dashboard/invoices/{invoice_id}/payments", status_code=status.HTTP_201_CREATED)
def add_payment(
    invoice_id: uuid.UUID,
    payload: RecordPayment,
    session: SessionDep,
    operator: Operator,
) -> dict:
    """Record money that arrived outside a Vasooli payment link.

    The answer to "what happens if the customer pays by bank transfer?" — previously,
    nothing did, and the customer kept being chased. Recording the payment settles the
    balance, stops the cadence, resolves any active promise, and closes the payment link
    if the invoice is now paid in full.
    """
    try:
        payment = record_external_payment(
            session,
            invoice_id=invoice_id,
            amount_paise=payload.amount_paise,
            method=payload.method,
            reference=payload.reference,
            received_on=payload.received_on,
            note=payload.note,
            actor=AuditActor.human(operator),
        )
    except ManualPaymentError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc

    invoice = session.get(Invoice, invoice_id)
    return {"payment": payment_view(payment), "balance": _balance_view(invoice)}


@router.post("/dashboard/payments/{payment_id}/reverse")
def undo_payment(
    payment_id: uuid.UUID,
    payload: ReversePayment,
    session: SessionDep,
    operator: Operator,
) -> dict:
    """Retract a recorded payment and put the balance back.

    For a mistyped amount, a payment credited to the wrong invoice, or a cheque that
    bounced. A recovered invoice can return to being owed — which is a state the system
    could not previously represent at all.
    """
    try:
        payment = reverse_external_payment(
            session,
            payment_id=payment_id,
            reason=payload.reason,
            actor=AuditActor.human(operator),
        )
    except ManualPaymentError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc

    invoice = session.get(Invoice, payment.invoice_id)
    return {
        "payment": payment_view(payment),
        "balance": _balance_view(invoice),
        "note": (
            "This invoice is owed again. Its Razorpay link was cancelled when it "
            "settled and cannot be reopened — provision a new one before chasing."
            if not invoice.is_fully_paid
            else "The invoice remains settled."
        ),
    }


@router.post("/dashboard/exceptions/events/{provider_event_id}/match")
def match_event(
    provider_event_id: str,
    payload: MatchEvent,
    session: SessionDep,
    operator: Operator,
) -> dict:
    """Assign an unmatched Razorpay settlement to an invoice.

    The queue previously offered only "retry", which is useless here: retrying cannot
    conjure a payment link that was never in our database, so an unmatched payment sat
    in the exceptions list permanently while the customer who made it kept being chased.

    What this does NOT do is force the payment through link reconciliation. That path
    applies the provider's running total with `max()`, and this event describes a link
    belonging to some other object — treating its total as this invoice's link total
    would corrupt every later webhook for it. Instead the amount is recorded as an
    external payment sourced from the event, which is exactly what it is: money we know
    arrived, tied to an invoice by a person rather than by a provider field. The event
    id becomes the reference, so matching the same event twice is refused.

    **`amount_paid` is a running total, and that is the whole subtlety here.** One link
    that is part-paid and then settled emits two events: `partially_paid` carrying
    5,000 and `paid` carrying 10,000. Both can land unmatched, and an operator working
    the queue will reasonably match both. Recording each at face value credits the
    invoice 15,000 for a customer who paid 10,000 — an overpayment invented by the
    system, which then marks the invoice recovered and stops chasing a balance that is
    still owed. So only the portion of the link total not already attributed to this
    invoice is recorded, and the events themselves are the record of what that is.
    """
    event = session.exec(
        select(ReconciliationEvent).where(
            ReconciliationEvent.provider_event_id == provider_event_id
        )
    ).first()
    if event is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Event not found")
    if event.status == EventStatus.PROCESSED:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "This event has already been reconciled",
        )

    invoice = session.get(Invoice, payload.invoice_id)
    if invoice is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Invoice not found")

    reported_total = _amount_from_event(event)
    if reported_total <= 0:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "This event carries no payment amount to match",
        )

    link_id = _link_id_from_event(event)
    already_credited = _credited_from_link(session, invoice_id=invoice.id, link_id=link_id)
    amount_paise = reported_total - already_credited
    if amount_paise <= 0:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "This link's payments are already fully recorded against "
            f"{invoice.invoice_number} — the event reports a total of "
            f"{format_inr(reported_total)}, all of which is accounted for",
        )

    try:
        payment = record_external_payment(
            session,
            invoice_id=invoice.id,
            amount_paise=amount_paise,
            method=PaymentMethod.RAZORPAY_UNLINKED,
            reference=provider_event_id,
            received_on=event.received_at.date(),
            note=payload.note
            or f"Matched by hand from unmatched Razorpay event {provider_event_id}",
            actor=AuditActor.human(operator),
        )
    except ManualPaymentError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc

    event.matched_invoice_id = invoice.id
    event.match_strategy = "manual"
    event.status = EventStatus.PROCESSED
    event.processing_error = None
    event.next_retry_at = None
    event.amount_paise = amount_paise
    event.processed_at = payment.recorded_at
    session.add(event)
    session.add(
        AuditLog(
            invoice_id=invoice.id,
            actor=AuditActor.human(operator),
            action=AuditAction.RECONCILIATION_MANUALLY_MATCHED,
            detail={
                "event_id": provider_event_id,
                "event_type": event.event_type,
                "payment_link_id": link_id,
                # All three, because "we credited 5,000" is not reviewable on its own.
                # A later reader has to be able to see that the provider reported a
                # 10,000 running total, 5,000 of it was already recorded from an
                # earlier event on the same link, and only the difference was applied.
                "provider_reported_total_paise": reported_total,
                "already_credited_paise": already_credited,
                "amount_paise": amount_paise,
                "external_payment_id": str(payment.id),
                "note": payload.note[:300],
            },
        )
    )
    session.commit()
    session.refresh(invoice)

    return {
        "event_id": provider_event_id,
        "matched": True,
        "payment": payment_view(payment),
        "balance": _balance_view(invoice),
    }


def _link_entity(event: ReconciliationEvent) -> dict:
    """The Razorpay payment-link entity carried by a stored event, if any."""
    payload = event.raw_payload or {}
    return ((payload.get("payload", {}) or {}).get("payment_link", {}) or {}).get(
        "entity", {}
    ) or {}


def _amount_from_event(event: ReconciliationEvent) -> int:
    """The RUNNING TOTAL this event says the link has taken, not an increment.

    Read from the stored payload rather than from anything the operator types. The
    person doing the matching decides WHICH invoice this belongs to; they do not get to
    decide how much Razorpay said was paid.
    """
    return int(_link_entity(event).get("amount_paid") or 0)


def _link_id_from_event(event: ReconciliationEvent) -> str | None:
    return _link_entity(event).get("id") or None


def _credited_from_link(session: SessionDep, *, invoice_id: uuid.UUID, link_id: str | None) -> int:
    """How much of this link's total is already recorded against this invoice.

    Derived from the events rather than stored on the payment row, because the events
    are already the durable record and a second copy of the same fact is a second thing
    that can be wrong. Each manually matched event stores the increment it contributed
    in `amount_paise` — the same meaning that column carries on the automatic path — so
    summing them reconstructs the link's credited total exactly.

    An event with no link id in its payload cannot be grouped with anything, so it is
    treated as standalone. That is the safe direction: it risks recording a payment
    twice only if an operator matches two genuinely unrelated events, which they would
    have to do deliberately, whereas the alternative silently swallows a real payment.
    """
    if not link_id:
        return 0

    prior = session.exec(
        select(ReconciliationEvent).where(
            ReconciliationEvent.matched_invoice_id == invoice_id,
            ReconciliationEvent.match_strategy == "manual",
            ReconciliationEvent.status == EventStatus.PROCESSED,
        )
    ).all()
    # Filtered in Python rather than with a JSONB predicate: the manual queue is a
    # handful of rows by construction, and a hand-written JSON path here would be a
    # subtle place for a schema change to break the arithmetic silently.
    return sum(event.amount_paise or 0 for event in prior if _link_id_from_event(event) == link_id)
