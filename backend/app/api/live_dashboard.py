"""Tenant-scoped recovery workspace for live merchants.

The guided demo and live product deliberately have different authentication, but they
share the same ledger and view models.  Every object without its own ``merchant_id``
is reached through an invoice that has already been constrained to the authenticated
merchant; this keeps the boundary explicit even when the database role bypasses RLS.
"""

import uuid
from datetime import date, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field, model_validator
from sqlmodel import select

from app.api.dashboard import (
    _PROVENANCE,
    _explain_for,
    _next_action,
    _summarise,
    _tier_label,
    build_invoice_detail,
)
from app.core.clock import utcnow
from app.core.constants import DisputeStatus
from app.core.db import SessionDep
from app.core.money import format_inr
from app.models import (
    AuditActor,
    AuditLog,
    BillingPlan,
    BillingSubscription,
    Customer,
    DisputeCase,
    ExternalPayment,
    InboundMessage,
    Invoice,
    PaymentLink,
    Promise,
    ReconciliationEvent,
    Reminder,
)
from app.models.reconciliation_event import EventStatus
from app.schemas.dashboard import InvoiceDetail, PromiseView, QueueRow
from app.services.authorization import LiveContext, get_scoped_object, require_live_permission
from app.services.disputes import resolve_dispute
from app.services.manual_payments import (
    ManualPaymentError,
    payment_view,
    record_external_payment,
    reverse_external_payment,
)
from app.services.metrics import compute_metrics
from app.services.reconciliation import reprocess_event
from app.services.recovery import escalate_to_human
from app.services.replies import reprocess_inbound

router = APIRouter(prefix="/api/live/workspace", tags=["live-workspace"])


def _invoice_ids(session: SessionDep, merchant_id: uuid.UUID) -> list[uuid.UUID]:
    return list(session.exec(select(Invoice.id).where(Invoice.merchant_id == merchant_id)).all())


def _actor(context: LiveContext) -> str:
    return AuditActor.human(context.user.email)


@router.get("/profile")
def workspace_profile(
    session: SessionDep,
    context: Annotated[LiveContext, Depends(require_live_permission("invoice.read"))],
) -> dict:
    """Return the authenticated tenant identity and its current commercial plan."""
    subscription = session.exec(
        select(BillingSubscription)
        .where(BillingSubscription.merchant_id == context.merchant.id)
        .order_by(BillingSubscription.updated_at.desc())  # type: ignore[attr-defined]
    ).first()
    plan = session.get(BillingPlan, subscription.plan_id) if subscription else None
    trial_ends_at = (context.merchant.onboarding_state or {}).get("trial_ends_at")
    return {
        "business_name": context.merchant.legal_name or context.merchant.name,
        "subscription": {
            "label": plan.name if plan else "Free trial",
            "slug": plan.slug if plan else "trial",
            "status": subscription.status if subscription else "trialing",
            "trial_ends_at": trial_ends_at if subscription is None else None,
        },
    }


@router.get("/overview")
def overview(
    session: SessionDep,
    context: Annotated[LiveContext, Depends(require_live_permission("invoice.read"))],
    days: int = Query(30, ge=1, le=365),
) -> dict:
    metrics = compute_metrics(
        session,
        since=utcnow() - timedelta(days=days),
        merchant_id=context.merchant.id,
    )
    return {"window_days": days, **metrics.as_dict()}


@router.get("/queue", response_model=list[QueueRow])
def queue(
    session: SessionDep,
    context: Annotated[LiveContext, Depends(require_live_permission("invoice.read"))],
    status_filter: str | None = Query(None, alias="status"),
    reason: str | None = None,
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> list[QueueRow]:
    query = select(Invoice).where(Invoice.merchant_id == context.merchant.id)
    if status_filter:
        query = query.where(Invoice.status == status_filter)
    if reason:
        query = query.where(Invoice.reason_category == reason)
    invoices = sorted(
        session.exec(query).all(), key=lambda invoice: invoice.outstanding_paise, reverse=True
    )[offset : offset + limit]
    ids = [invoice.id for invoice in invoices]
    customer_ids = [invoice.customer_id for invoice in invoices]
    names = {
        customer.id: customer.name
        for customer in session.exec(
            select(Customer).where(
                Customer.merchant_id == context.merchant.id,
                Customer.id.in_(customer_ids),  # type: ignore[union-attr]
            )
        ).all()
    }
    links = {
        link.invoice_id: link
        for link in session.exec(
            select(PaymentLink).where(PaymentLink.invoice_id.in_(ids))  # type: ignore[union-attr]
        ).all()
    }
    disputed = {
        case.invoice_id
        for case in session.exec(
            select(DisputeCase).where(
                DisputeCase.invoice_id.in_(ids),  # type: ignore[union-attr]
                DisputeCase.status == DisputeStatus.OPEN,
            )
        ).all()
    }
    reasons = {invoice.id: _explain_for(session, invoice) for invoice in invoices}
    return [
        QueueRow(
            id=invoice.id,
            invoice_number=invoice.invoice_number,
            customer_name=names.get(invoice.customer_id, "—"),
            amount_display=format_inr(invoice.amount_paise),
            outstanding_paise=invoice.outstanding_paise,
            days_overdue=invoice.days_overdue,
            status=str(invoice.status),
            tier_label=_tier_label(invoice),
            reason_category=(str(invoice.reason_category) if invoice.reason_category else None),
            payment_url=links[invoice.id].short_url if invoice.id in links else None,
            next_action=_next_action(invoice),
            dispute_open=invoice.id in disputed,
            recovered_at=invoice.recovered_at,
            why=reasons[invoice.id].headline,
            why_next=reasons[invoice.id].next_step,
            why_state=reasons[invoice.id].state,
        )
        for invoice in invoices
    ]


@router.get("/invoices/{invoice_id}", response_model=InvoiceDetail)
def invoice_detail(
    invoice_id: uuid.UUID,
    session: SessionDep,
    context: Annotated[LiveContext, Depends(require_live_permission("invoice.read"))],
) -> InvoiceDetail:
    invoice = get_scoped_object(session, Invoice, invoice_id, context.merchant.id)
    if invoice is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Invoice not found")
    return build_invoice_detail(session, invoice)


@router.get("/promises", response_model=list[PromiseView])
def promises(
    session: SessionDep,
    context: Annotated[LiveContext, Depends(require_live_permission("reminder.read"))],
    status_filter: str | None = Query(None, alias="status"),
) -> list[PromiseView]:
    invoices = {
        invoice.id: invoice
        for invoice in session.exec(
            select(Invoice).where(Invoice.merchant_id == context.merchant.id)
        ).all()
    }
    query = select(Promise).where(Promise.invoice_id.in_(list(invoices)))  # type: ignore[union-attr]
    if status_filter:
        query = query.where(Promise.status == status_filter)
    names = {
        customer.id: customer.name
        for customer in session.exec(
            select(Customer).where(Customer.merchant_id == context.merchant.id)
        ).all()
    }
    return [
        PromiseView(
            id=promise.id,
            invoice_number=invoices[promise.invoice_id].invoice_number,
            customer_name=names.get(invoices[promise.invoice_id].customer_id, "—"),
            promised_date=str(promise.promised_date),
            amount_display=format_inr(
                promise.promised_amount_paise or invoices[promise.invoice_id].outstanding_paise
            ),
            status=str(promise.status),
            confidence=promise.extraction_confidence,
            tier_at_pause=promise.tier_at_pause,
            excerpt=promise.source_message_excerpt,
        )
        for promise in session.exec(query.order_by(Promise.created_at.desc())).all()  # type: ignore[attr-defined]
        if promise.invoice_id in invoices
    ]


@router.get("/disputes")
def disputes(
    session: SessionDep,
    context: Annotated[LiveContext, Depends(require_live_permission("reminder.read"))],
) -> list[dict]:
    invoices = {
        invoice.id: invoice
        for invoice in session.exec(
            select(Invoice).where(Invoice.merchant_id == context.merchant.id)
        ).all()
    }
    cases = session.exec(
        select(DisputeCase)
        .where(
            DisputeCase.invoice_id.in_(list(invoices)),  # type: ignore[union-attr]
            DisputeCase.status == DisputeStatus.OPEN,
        )
        .order_by(DisputeCase.opened_at.desc())  # type: ignore[attr-defined]
    ).all()
    names = {
        customer.id: customer.name
        for customer in session.exec(
            select(Customer).where(Customer.merchant_id == context.merchant.id)
        ).all()
    }
    return [
        {
            "case_id": str(case.id),
            "invoice_id": str(case.invoice_id),
            "invoice_number": invoices[case.invoice_id].invoice_number,
            "customer_name": names.get(invoices[case.invoice_id].customer_id, "—"),
            "outstanding_display": format_inr(invoices[case.invoice_id].outstanding_paise),
            "reason": case.reason,
            "summary": case.summary,
            "confidence_display": f"{round(case.confidence * 100)}%",
            "opened_at": case.opened_at,
            "detected_by": case.detected_by,
        }
        for case in cases
    ]


@router.get("/audit")
def audit(
    session: SessionDep,
    context: Annotated[LiveContext, Depends(require_live_permission("audit.read"))],
    invoice_id: uuid.UUID | None = None,
    action: str | None = None,
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> list[dict]:
    ids = _invoice_ids(session, context.merchant.id)
    query = select(AuditLog).where(AuditLog.invoice_id.in_(ids))  # type: ignore[union-attr]
    if invoice_id:
        if invoice_id not in ids:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Invoice not found")
        query = query.where(AuditLog.invoice_id == invoice_id)
    if action:
        query = query.where(AuditLog.action == action)
    numbers = {
        invoice.id: invoice.invoice_number
        for invoice in session.exec(
            select(Invoice).where(Invoice.merchant_id == context.merchant.id)
        ).all()
    }
    return [
        {
            "at": entry.created_at.isoformat(),
            "invoice_id": str(entry.invoice_id) if entry.invoice_id else None,
            "invoice_number": numbers.get(entry.invoice_id),
            "actor": entry.actor,
            "action": entry.action,
            "provenance": _PROVENANCE.get(entry.actor.split(":")[0], "system"),
            "summary": _summarise(entry),
            "detail": entry.detail or {},
        }
        for entry in session.exec(
            query.order_by(AuditLog.created_at.desc()).offset(offset).limit(limit)  # type: ignore[attr-defined]
        ).all()
    ]


@router.get("/exceptions")
def exceptions(
    session: SessionDep,
    context: Annotated[LiveContext, Depends(require_live_permission("invoice.read"))],
) -> dict:
    invoices = {
        invoice.id: invoice
        for invoice in session.exec(
            select(Invoice).where(Invoice.merchant_id == context.merchant.id)
        ).all()
    }
    ids = list(invoices)
    names = {
        customer.id: customer.name
        for customer in session.exec(
            select(Customer).where(Customer.merchant_id == context.merchant.id)
        ).all()
    }
    failed_events = session.exec(
        select(ReconciliationEvent).where(
            ReconciliationEvent.matched_invoice_id.in_(ids),  # type: ignore[union-attr]
            ReconciliationEvent.status == EventStatus.FAILED,
        )
    ).all()
    reminders = session.exec(
        select(Reminder).where(
            Reminder.invoice_id.in_(ids),  # type: ignore[union-attr]
            Reminder.sent_at.is_(None),  # type: ignore[union-attr]
        )
    ).all()
    links = session.exec(
        select(PaymentLink).where(
            PaymentLink.invoice_id.in_(ids),  # type: ignore[union-attr]
            PaymentLink.closure_error.is_not(None),  # type: ignore[union-attr]
            PaymentLink.cancelled_at.is_(None),  # type: ignore[union-attr]
        )
    ).all()
    inbound = session.exec(
        select(InboundMessage).where(
            InboundMessage.invoice_id.in_(ids),  # type: ignore[union-attr]
            InboundMessage.processed_at.is_(None),  # type: ignore[union-attr]
            InboundMessage.processing_error.is_not(None),  # type: ignore[union-attr]
        )
    ).all()
    result = {
        "reconciliation": [
            {
                "id": str(event.id),
                "event_id": event.provider_event_id,
                "invoice_number": invoices[event.matched_invoice_id].invoice_number,
                "amount_display": format_inr(event.amount_paise or 0),
                "error": event.processing_error,
                "attempts": event.attempts,
                "exhausted": event.is_exhausted,
            }
            for event in failed_events
            if event.matched_invoice_id in invoices
        ],
        "communication": [
            {
                "id": str(reminder.id),
                "invoice_number": invoices[reminder.invoice_id].invoice_number,
                "customer_name": names.get(invoices[reminder.invoice_id].customer_id, "—"),
                "tier": reminder.tier,
                "error": reminder.send_error,
                "attempts": reminder.attempt_count,
                "exhausted": not reminder.needs_retry,
            }
            for reminder in reminders
        ],
        "unclosed_links": [
            {
                "id": str(link.id),
                "invoice_number": invoices[link.invoice_id].invoice_number,
                "error": link.closure_error,
                "attempts": link.closure_attempts,
            }
            for link in links
        ],
        "inbound": [
            {
                "id": str(message.id),
                "invoice_number": invoices[message.invoice_id].invoice_number,
                "sender": message.sender,
                "subject": message.subject,
                "excerpt": message.body_text[:200],
                "error": message.processing_error,
                "attempts": message.processing_attempts,
                "exhausted": message.is_exhausted,
            }
            for message in inbound
        ],
    }
    result["total"] = sum(len(value) for value in result.values())
    return result


class RecordPayment(BaseModel):
    amount_paise: int = Field(gt=0, le=10_000_000_000)
    method: str
    reference: str = Field(min_length=1, max_length=200)
    received_on: date
    note: str = Field(default="", max_length=1000)


class ReversePayment(BaseModel):
    reason: str = Field(min_length=1, max_length=500)


class ResolveDispute(BaseModel):
    note: str = Field(default="", max_length=1000)
    resume_recovery: bool = False

    @model_validator(mode="after")
    def require_resume_reason(self) -> "ResolveDispute":
        if self.resume_recovery and not self.note.strip():
            raise ValueError("A decision note is required before recovery can resume")
        return self


@router.post("/invoices/{invoice_id}/payments", status_code=status.HTTP_201_CREATED)
def add_payment(
    invoice_id: uuid.UUID,
    payload: RecordPayment,
    session: SessionDep,
    context: Annotated[LiveContext, Depends(require_live_permission("invoice.write"))],
) -> dict:
    invoice = get_scoped_object(session, Invoice, invoice_id, context.merchant.id)
    if invoice is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Invoice not found")
    try:
        payment = record_external_payment(
            session,
            invoice_id=invoice.id,
            amount_paise=payload.amount_paise,
            method=payload.method,
            reference=payload.reference,
            received_on=payload.received_on,
            note=payload.note,
            actor=_actor(context),
        )
    except ManualPaymentError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    session.refresh(invoice)
    return {"payment": payment_view(payment), "invoice": build_invoice_detail(session, invoice)}


@router.post("/payments/{payment_id}/reverse")
def reverse_payment(
    payment_id: uuid.UUID,
    payload: ReversePayment,
    session: SessionDep,
    context: Annotated[LiveContext, Depends(require_live_permission("invoice.write"))],
) -> dict:
    payment = session.get(ExternalPayment, payment_id)
    invoice = (
        get_scoped_object(session, Invoice, payment.invoice_id, context.merchant.id)
        if payment
        else None
    )
    if payment is None or invoice is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Payment not found")
    try:
        payment = reverse_external_payment(
            session,
            payment_id=payment.id,
            reason=payload.reason,
            actor=_actor(context),
        )
    except ManualPaymentError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    session.refresh(invoice)
    return {"payment": payment_view(payment), "invoice": build_invoice_detail(session, invoice)}


@router.post("/invoices/{invoice_id}/escalate")
def escalate(
    invoice_id: uuid.UUID,
    session: SessionDep,
    context: Annotated[LiveContext, Depends(require_live_permission("invoice.write"))],
) -> dict:
    invoice = get_scoped_object(session, Invoice, invoice_id, context.merchant.id)
    if invoice is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Invoice not found")
    escalate_to_human(session, invoice, "manual", actor=_actor(context))
    session.commit()
    return {"invoice_number": invoice.invoice_number, "status": str(invoice.status)}


@router.post("/disputes/{case_id}/resolve")
def resolve_case(
    case_id: uuid.UUID,
    payload: ResolveDispute,
    session: SessionDep,
    context: Annotated[LiveContext, Depends(require_live_permission("reminder.pause"))],
) -> dict:
    case = session.get(DisputeCase, case_id)
    invoice = (
        get_scoped_object(session, Invoice, case.invoice_id, context.merchant.id) if case else None
    )
    if case is None or invoice is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Dispute case not found")
    if case.is_open:
        case, resumed = resolve_dispute(
            session,
            case,
            resolved_by=_actor(context),
            note=payload.note,
            resume_recovery=payload.resume_recovery,
        )
        session.commit()
    else:
        resumed = case.recovery_resumed_at is not None
    return {"case_id": str(case.id), "status": str(case.status), "resumed": resumed}


@router.post("/exceptions/events/{provider_event_id}/retry")
def retry_reconciliation(
    provider_event_id: str,
    session: SessionDep,
    context: Annotated[LiveContext, Depends(require_live_permission("invoice.write"))],
) -> dict:
    event = session.exec(
        select(ReconciliationEvent).where(
            ReconciliationEvent.provider_event_id == provider_event_id
        )
    ).first()
    invoice = (
        get_scoped_object(session, Invoice, event.matched_invoice_id, context.merchant.id)
        if event and event.matched_invoice_id
        else None
    )
    if event is None or invoice is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Reconciliation event not found")
    recovered = reprocess_event(session, event)
    return {"event_id": provider_event_id, "recovered": recovered}


@router.post("/exceptions/inbound/{message_id}/retry")
def retry_inbound(
    message_id: uuid.UUID,
    session: SessionDep,
    context: Annotated[LiveContext, Depends(require_live_permission("reminder.pause"))],
) -> dict:
    message = session.get(InboundMessage, message_id)
    invoice = (
        get_scoped_object(session, Invoice, message.invoice_id, context.merchant.id)
        if message
        else None
    )
    if message is None or invoice is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Inbound message not found")
    if message.processed_at is None:
        reprocess_inbound(session, message)
    return {"message_id": str(message.id), "processed": message.processed_at is not None}
