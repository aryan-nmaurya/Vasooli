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

from app.api.deps import OperatorRequired
from app.core.clock import utcnow
from app.core.constants import InvoiceStatus, PromiseStatus
from app.core.db import SessionDep
from app.core.money import format_inr
from app.models import (
    AuditAction,
    AuditLog,
    Customer,
    Invoice,
    PaymentLink,
    Promise,
    ReconciliationEvent,
    Reminder,
)
from app.models.reconciliation_event import EventStatus
from app.schemas.dashboard import InvoiceDetail, PromiseView, QueueRow, ReminderView, TimelineEntry
from app.services.explain import Explanation, explain
from app.services.messaging import retry_failed_deliveries
from app.services.metrics import compute_metrics
from app.services.reconciliation import reprocess_event
from app.services.recovery import escalate_to_human

# Every endpoint here is gated. These reads expose customer names, email
# addresses, amounts owed and the audit trail — that is a breach if it is
# public, whether or not the caller can also change anything.
router = APIRouter(prefix="/api", tags=["dashboard"], dependencies=[OperatorRequired])

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
    AuditAction.PAYMENT_LINK_CREATED: "Payment link created",
    AuditAction.PAYMENT_LINK_FAILED: "Payment link failed",
    AuditAction.DIAGNOSED: "Reason diagnosed",
    AuditAction.LLM_FAILOVER: "Model failover",
    AuditAction.LLM_UNAVAILABLE: "AI unavailable — deterministic fallback",
    AuditAction.LLM_OUTPUT_REJECTED: "AI output rejected",
    AuditAction.DETERMINISTIC_FALLBACK: "Template used — no model available",
    AuditAction.PAYMENT_LINK_CLOSED: "Payment link closed",
    AuditAction.PAYMENT_LINK_CLOSE_FAILED: "Payment link close failed",
    AuditAction.RECONCILIATION_FAILED: "Reconciliation failed",
    AuditAction.RECONCILIATION_RETRIED: "Reconciliation retried",
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


def _explain_for(session, invoice: Invoice) -> Explanation:
    """Build the explanation for one invoice from its current state."""
    promise = session.exec(
        select(Promise).where(
            Promise.invoice_id == invoice.id, Promise.status == PromiseStatus.ACTIVE
        )
    ).first()

    days_since = None
    if invoice.last_reminder_at:
        days_since = (utcnow() - invoice.last_reminder_at).days

    has_failed_delivery = bool(
        session.exec(
            select(Reminder).where(
                Reminder.invoice_id == invoice.id,
                Reminder.sent_at.is_(None),  # type: ignore[union-attr]
            )
        ).first()
    )

    return explain(
        status=str(invoice.status),
        days_overdue=invoice.days_overdue,
        reminders_sent=invoice.reminders_sent,
        current_tier=invoice.current_tier,
        reason_category=str(invoice.reason_category) if invoice.reason_category else None,
        escalation_reason=invoice.escalation_reason,
        amount_paise=invoice.amount_paise,
        amount_paid_paise=invoice.amount_paid_paise,
        active_promise_date=promise.promised_date if promise else None,
        days_since_last_reminder=days_since,
        has_failed_delivery=has_failed_delivery,
    )


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
    reasons = {i.id: _explain_for(session, i) for i in invoices}

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
            why=(reasons[i.id]).headline,
            why_next=(reasons[i.id]).next_step,
            why_state=(reasons[i.id]).state,
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

    reason = _explain_for(session, invoice)

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
        why=reason.headline,
        why_next=reason.next_step,
        why_state=reason.state,
        reply_count=invoice.reply_count,
        last_reply_at=invoice.last_reply_at,
        last_reply_excerpt=invoice.last_reply_excerpt,
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


@router.post("/dashboard/invoices/{invoice_id}/escalate")
def manual_escalate(invoice_id: uuid.UUID, session: SessionDep) -> dict:
    """Hand an invoice to a human by hand."""
    invoice = session.get(Invoice, invoice_id)
    if invoice is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Invoice not found")
    escalate_to_human(session, invoice, "manual")
    session.commit()
    return {"invoice_number": invoice.invoice_number, "status": str(invoice.status)}


@router.post("/dashboard/invoices/{invoice_id}/write-off")
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


# ---------------------------------------------------------------------------
# Operational exceptions. P0 — things that failed and need a person.
# ---------------------------------------------------------------------------


@router.get("/dashboard/exceptions")
def exceptions(session: SessionDep) -> dict:
    """Everything that failed and has not recovered on its own.

    Two queues, deliberately separate because they mean different things to a finance
    operator: money that arrived but could not be matched, and messages that never
    reached a customer. Both were previously invisible outside application logs.
    """
    numbers = {i.id: i.invoice_number for i in session.exec(select(Invoice)).all()}
    names = {c.id: c.name for c in session.exec(select(Customer)).all()}
    invoices = {i.id: i for i in session.exec(select(Invoice)).all()}

    failed_events = session.exec(
        select(ReconciliationEvent)
        .where(ReconciliationEvent.status == EventStatus.FAILED)
        .order_by(ReconciliationEvent.received_at.desc())  # type: ignore[attr-defined]
    ).all()

    reconciliation = [
        {
            "id": str(event.id),
            "event_id": event.provider_event_id,
            "event_type": event.event_type,
            "invoice_number": numbers.get(event.matched_invoice_id)
            if event.matched_invoice_id
            else None,
            "amount_display": format_inr(event.amount_paise) if event.amount_paise else None,
            "error": event.processing_error,
            "attempts": event.attempts,
            "last_attempt_at": (
                event.last_attempt_at.isoformat() if event.last_attempt_at else None
            ),
            "next_retry_at": event.next_retry_at.isoformat() if event.next_retry_at else None,
            "exhausted": event.is_exhausted,
            "received_at": event.received_at.isoformat(),
        }
        for event in failed_events
    ]

    stuck_reminders = session.exec(
        select(Reminder)
        .where(Reminder.sent_at.is_(None))  # type: ignore[union-attr]
        .order_by(Reminder.created_at.desc())  # type: ignore[attr-defined]
    ).all()

    communication = []
    for reminder in stuck_reminders:
        invoice = invoices.get(reminder.invoice_id)
        if invoice is None:
            continue
        communication.append(
            {
                "id": str(reminder.id),
                "invoice_number": invoice.invoice_number,
                "customer_name": names.get(invoice.customer_id, "—"),
                "tier": reminder.tier,
                "tone": str(reminder.tone),
                "error": reminder.send_error,
                "attempts": reminder.attempt_count,
                "last_attempt_at": (
                    reminder.last_attempt_at.isoformat() if reminder.last_attempt_at else None
                ),
                "next_retry_at": (
                    reminder.next_retry_at.isoformat() if reminder.next_retry_at else None
                ),
                "exhausted": not reminder.needs_retry,
            }
        )

    #: Recovered invoices whose payment link was never confirmed closed. Each one is a
    #: customer who could still pay into a settled invoice.
    open_links = [
        {
            "id": str(link.id),
            "invoice_number": numbers.get(link.invoice_id, "—"),
            "payment_link_id": link.razorpay_payment_link_id,
            "error": link.closure_error,
            "attempts": link.closure_attempts,
            "next_retry_at": (
                link.next_closure_retry_at.isoformat() if link.next_closure_retry_at else None
            ),
        }
        for link in session.exec(
            select(PaymentLink).where(PaymentLink.closure_error.is_not(None))  # type: ignore[union-attr]
        ).all()
        if link.cancelled_at is None
    ]

    return {
        "reconciliation": reconciliation,
        "communication": communication,
        "unclosed_links": open_links,
        "total": len(reconciliation) + len(communication) + len(open_links),
    }


@router.post("/dashboard/exceptions/events/{provider_event_id}/retry")
def retry_event(provider_event_id: str, session: SessionDep) -> dict:
    """Reprocess one failed webhook now, ignoring its backoff.

    Idempotent: reconciliation applies the running total Razorpay reports with max(),
    so retrying an event whose payment already landed changes nothing.
    """
    event = session.exec(
        select(ReconciliationEvent).where(
            ReconciliationEvent.provider_event_id == provider_event_id
        )
    ).first()
    if event is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Event not found")

    session.add(
        AuditLog(
            invoice_id=event.matched_invoice_id,
            actor="human",
            action=AuditAction.RECONCILIATION_RETRIED,
            detail={"event_id": provider_event_id, "attempts_before": event.attempts},
        )
    )
    session.commit()

    ok = reprocess_event(session, event)
    session.refresh(event)
    return {
        "event_id": provider_event_id,
        "recovered": ok,
        "status": event.status,
        "error": event.processing_error,
        "attempts": event.attempts,
    }


@router.post("/dashboard/exceptions/reminders/{reminder_id}/retry")
def retry_reminder(reminder_id: uuid.UUID, session: SessionDep) -> dict:
    """Re-attempt one failed delivery now, ignoring its backoff."""
    reminder = session.get(Reminder, reminder_id)
    if reminder is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Reminder not found")
    if reminder.sent_at is not None:
        return {"reminder_id": str(reminder_id), "recovered": True, "note": "already sent"}

    # Make it due, then run the normal path rather than a special one.
    reminder.next_retry_at = utcnow()
    session.add(reminder)
    session.commit()

    report = retry_failed_deliveries(session, limit=200)
    session.refresh(reminder)
    return {
        "reminder_id": str(reminder_id),
        "recovered": reminder.sent_at is not None,
        "attempts": reminder.attempt_count,
        "error": reminder.send_error,
        "swept": report["attempted"],
    }
