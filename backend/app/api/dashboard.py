"""Read endpoints for the dashboard. Doc §7.

Every number is computed on the server from app.services.metrics, and money crosses
the wire as integer paise plus a preformatted string. The frontend never does
arithmetic on currency — a rounding difference between two languages is exactly the
kind of bug that shows up as ₹1 missing on a slide.
"""

import uuid
from datetime import timedelta

from fastapi import APIRouter, HTTPException, Query, status
from sqlmodel import select

from app.api.deps import AdminRequired
from app.core.clock import utcnow
from app.core.constants import InvoiceStatus
from app.core.db import SessionDep
from app.core.money import format_inr
from app.models import (
    AuditAction,
    AuditLog,
    Customer,
    Invoice,
    PaymentLink,
    Promise,
    Reminder,
)
from app.schemas.dashboard import (
    InvoiceDetail,
    PromiseView,
    QueueRow,
    ReminderView,
    TimelineEntry,
)
from app.services.metrics import compute_metrics
from app.services.recovery import escalate_to_human

router = APIRouter(prefix="/api", tags=["dashboard"])

#: Maps an audit actor to the badge shown on the timeline.
_PROVENANCE = {
    "ai": "ai",
    "policy": "policy",
    "razorpay": "razorpay",
    "system": "system",
    "scheduler": "system",
}

_SUMMARIES = {
    AuditAction.INVOICE_INGESTED: "Invoice ingested",
    AuditAction.VA_PROVISIONED: "Payment link created",
    AuditAction.VA_PROVISION_FAILED: "Payment link failed",
    AuditAction.DIAGNOSED: "Reason diagnosed",
    AuditAction.LLM_FAILOVER: "Model failover",
    AuditAction.POLICY_EVALUATED: "Policy approved",
    AuditAction.POLICY_REJECTED: "Policy rejected",
    AuditAction.REMINDER_SENT: "Reminder sent",
    AuditAction.REMINDER_FAILED: "Reminder failed",
    AuditAction.REPLY_RECEIVED: "Customer replied",
    AuditAction.PROMISE_LOGGED: "Promise logged",
    AuditAction.PROMISE_KEPT: "Promise kept",
    AuditAction.PROMISE_BROKEN: "Promise broken",
    AuditAction.ESCALATED_TO_HUMAN: "Escalated to human",
    AuditAction.PAYMENT_RECONCILED: "Payment reconciled",
    AuditAction.RECONCILIATION_UNMATCHED: "Unmatched payment",
    AuditAction.WEBHOOK_DUPLICATE_IGNORED: "Duplicate webhook ignored",
    AuditAction.WEBHOOK_SIGNATURE_INVALID: "Invalid webhook signature",
}


def _summarise(entry: AuditLog) -> str:
    base = _SUMMARIES.get(entry.action, entry.action.replace("_", " ").capitalize())
    detail = entry.detail or {}
    if entry.action == AuditAction.REMINDER_SENT:
        return f"{base} — Tier {detail.get('tier')} ({detail.get('tone')})"
    if entry.action == AuditAction.DIAGNOSED:
        return f"{base} — {detail.get('category')}"
    if entry.action == AuditAction.PAYMENT_RECONCILED:
        return f"{base} — {format_inr(int(detail.get('applied_paise') or 0))}"
    if entry.action == AuditAction.ESCALATED_TO_HUMAN:
        return f"{base} — {detail.get('reason')}"
    if entry.action == AuditAction.PROMISE_LOGGED:
        return f"{base} — by {detail.get('promised_date')}"
    return base


def _next_action(invoice: Invoice) -> str:
    """One line telling a merchant what happens next, without reading the timeline."""
    if invoice.status == InvoiceStatus.RECOVERED:
        return "Recovered"
    if invoice.status == InvoiceStatus.HUMAN_REVIEW:
        return f"Needs a human — {invoice.escalation_reason or 'flagged'}"
    if invoice.status == InvoiceStatus.PROMISE_ACTIVE:
        return "Paused — customer promised to pay"
    if invoice.reminders_sent >= 3:
        return "Cadence exhausted"
    return f"Tier {invoice.current_tier + 1} when due"


def _tier_label(invoice: Invoice) -> str:
    if invoice.status == InvoiceStatus.HUMAN_REVIEW:
        return "Human"
    return f"Tier {invoice.current_tier}" if invoice.current_tier else "—"


@router.get("/dashboard/overview")
def overview(session: SessionDep, days: int = Query(30, ge=1, le=365)) -> dict:
    """Headline figures. Doc §7."""
    metrics = compute_metrics(session, since=utcnow() - timedelta(days=days))
    return {"window_days": days, **metrics.as_dict()}


@router.get("/dashboard/queue", response_model=list[QueueRow])
def queue(
    session: SessionDep,
    status_filter: str | None = Query(None, alias="status"),
    reason: str | None = None,
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> list[QueueRow]:
    """The recovery queue, worst first.

    Ordered by outstanding value rather than age: the merchant's attention is best
    spent on the largest recoverable balance, not the oldest small one.
    """
    query = select(Invoice)
    if status_filter:
        query = query.where(Invoice.status == status_filter)
    if reason:
        query = query.where(Invoice.reason_category == reason)

    invoices = list(session.exec(query).all())
    invoices.sort(key=lambda i: i.outstanding_paise, reverse=True)
    invoices = invoices[offset : offset + limit]

    names = {c.id: c.name for c in session.exec(select(Customer)).all()}
    links = {pl.invoice_id: pl for pl in session.exec(select(PaymentLink)).all()}

    return [
        QueueRow(
            id=i.id,
            invoice_number=i.invoice_number,
            customer_name=names.get(i.customer_id, "—"),
            amount_display=format_inr(i.amount_paise),
            outstanding_paise=i.outstanding_paise,
            days_overdue=i.days_overdue,
            status=str(i.status),
            tier_label=_tier_label(i),
            reason_category=str(i.reason_category) if i.reason_category else None,
            payment_url=links[i.id].short_url if i.id in links else None,
            next_action=_next_action(i),
        )
        for i in invoices
    ]


@router.get("/dashboard/invoices/{invoice_id}", response_model=InvoiceDetail)
def invoice_detail(invoice_id: uuid.UUID, session: SessionDep) -> InvoiceDetail:
    """Everything about one invoice, including the full audit timeline. Doc §7."""
    invoice = session.get(Invoice, invoice_id)
    if invoice is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Invoice not found")

    customer = session.get(Customer, invoice.customer_id)
    link = session.exec(select(PaymentLink).where(PaymentLink.invoice_id == invoice.id)).first()

    reminders = session.exec(
        select(Reminder).where(Reminder.invoice_id == invoice.id).order_by(Reminder.tier)
    ).all()
    promises = session.exec(
        select(Promise).where(Promise.invoice_id == invoice.id).order_by(Promise.created_at)
    ).all()
    entries = session.exec(
        select(AuditLog).where(AuditLog.invoice_id == invoice.id).order_by(AuditLog.created_at)
    ).all()

    return InvoiceDetail(
        id=invoice.id,
        invoice_number=invoice.invoice_number,
        customer_name=customer.name if customer else "—",
        customer_email=customer.email if customer else "—",
        amount_display=format_inr(invoice.amount_paise),
        paid_display=format_inr(invoice.amount_paid_paise),
        outstanding_display=format_inr(invoice.outstanding_paise),
        status=str(invoice.status),
        days_overdue=invoice.days_overdue,
        due_at=invoice.due_at,
        reason_category=str(invoice.reason_category) if invoice.reason_category else None,
        reason_explanation=invoice.reason_explanation,
        reason_confidence=invoice.reason_confidence,
        reason_llm_disagreed=invoice.reason_llm_disagreed,
        reminders_sent=invoice.reminders_sent,
        current_tier=invoice.current_tier,
        escalated_to_human_at=invoice.escalated_to_human_at,
        escalation_reason=invoice.escalation_reason,
        recovered_at=invoice.recovered_at,
        payment_url=link.short_url if link else None,
        payment_link_status=link.status if link else None,
        reminders=[
            ReminderView(
                tier=r.tier,
                tone=str(r.tone),
                subject=r.subject,
                body=r.body,
                generated_by=r.generated_by,
                llm_degraded=r.llm_degraded,
                sent_at=r.sent_at,
                policy_rendered=(r.policy_decision or {}).get("rendered"),
            )
            for r in reminders
        ],
        promises=[
            PromiseView(
                id=p.id,
                invoice_number=invoice.invoice_number,
                customer_name=customer.name if customer else "—",
                promised_date=str(p.promised_date),
                amount_display=format_inr(p.promised_amount_paise or invoice.outstanding_paise),
                status=str(p.status),
                confidence=p.extraction_confidence,
                tier_at_pause=p.tier_at_pause,
                excerpt=p.source_message_excerpt,
            )
            for p in promises
        ],
        timeline=[
            TimelineEntry(
                at=e.created_at,
                actor=e.actor,
                action=e.action,
                provenance=_PROVENANCE.get(e.actor.split(":")[0], "system"),
                summary=_summarise(e),
                detail=e.detail or {},
            )
            for e in entries
        ],
    )


@router.get("/dashboard/promises", response_model=list[PromiseView])
def promise_tracker(
    session: SessionDep, status_filter: str | None = Query(None, alias="status")
) -> list[PromiseView]:
    """Active, kept, and broken promises. Doc §7."""
    query = select(Promise)
    if status_filter:
        query = query.where(Promise.status == status_filter)
    promises = list(session.exec(query.order_by(Promise.created_at.desc())).all())  # type: ignore[attr-defined]

    invoices = {i.id: i for i in session.exec(select(Invoice)).all()}
    names = {c.id: c.name for c in session.exec(select(Customer)).all()}

    rows = []
    for p in promises:
        invoice = invoices.get(p.invoice_id)
        if invoice is None:
            continue
        rows.append(
            PromiseView(
                id=p.id,
                invoice_number=invoice.invoice_number,
                customer_name=names.get(invoice.customer_id, "—"),
                promised_date=str(p.promised_date),
                amount_display=format_inr(p.promised_amount_paise or invoice.outstanding_paise),
                status=str(p.status),
                confidence=p.extraction_confidence,
                tier_at_pause=p.tier_at_pause,
                excerpt=p.source_message_excerpt,
            )
        )
    return rows


@router.get("/dashboard/audit")
def audit_log(
    session: SessionDep,
    invoice_id: uuid.UUID | None = None,
    action: str | None = None,
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> list[dict]:
    """The append-only log, newest first. Doc §3 Stage 6."""
    query = select(AuditLog)
    if invoice_id:
        query = query.where(AuditLog.invoice_id == invoice_id)
    if action:
        query = query.where(AuditLog.action == action)

    entries = session.exec(
        query.order_by(AuditLog.created_at.desc()).offset(offset).limit(limit)  # type: ignore[attr-defined]
    ).all()

    numbers = {i.id: i.invoice_number for i in session.exec(select(Invoice)).all()}
    return [
        {
            "at": e.created_at.isoformat(),
            "invoice_number": numbers.get(e.invoice_id) if e.invoice_id else None,
            "actor": e.actor,
            "action": e.action,
            "provenance": _PROVENANCE.get(e.actor.split(":")[0], "system"),
            "summary": _summarise(e),
            "detail": e.detail or {},
        }
        for e in entries
    ]


@router.post("/dashboard/invoices/{invoice_id}/escalate", dependencies=[AdminRequired])
def manual_escalate(invoice_id: uuid.UUID, session: SessionDep) -> dict:
    """Hand an invoice to a human by hand."""
    invoice = session.get(Invoice, invoice_id)
    if invoice is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Invoice not found")
    escalate_to_human(session, invoice, "manual")
    session.commit()
    return {"invoice_number": invoice.invoice_number, "status": str(invoice.status)}


@router.post("/dashboard/invoices/{invoice_id}/write-off", dependencies=[AdminRequired])
def write_off(invoice_id: uuid.UUID, session: SessionDep) -> dict:
    """Close an invoice as unrecoverable."""
    invoice = session.get(Invoice, invoice_id)
    if invoice is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Invoice not found")
    invoice.status = InvoiceStatus.WRITTEN_OFF
    session.add(invoice)
    session.add(
        AuditLog(
            invoice_id=invoice.id,
            actor="human",
            action="written_off",
            detail={"outstanding_paise": invoice.outstanding_paise},
        )
    )
    session.commit()
    return {"invoice_number": invoice.invoice_number, "status": str(invoice.status)}
