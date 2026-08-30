"""Read endpoints for the dashboard. Doc §7.

Every number is computed on the server from app.services.metrics, and money crosses
the wire as integer paise plus a preformatted string. The frontend never does
arithmetic on currency — a rounding difference between two languages is exactly the
kind of bug that shows up as ₹1 missing on a slide.
"""

import asyncio
import json
import uuid
from datetime import timedelta

from fastapi import APIRouter, HTTPException, Query, Request, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, model_validator
from sqlmodel import Session, select

from app.api.deps import Operator, OperatorRequired
from app.core.clock import utcnow
from app.core.config import settings
from app.core.constants import DisputeStatus, InvoiceStatus, PromiseStatus
from app.core.db import SessionDep
from app.core.logging import get_logger
from app.core.money import format_inr
from app.models import (
    AuditAction,
    AuditActor,
    AuditLog,
    Customer,
    DisputeCase,
    InboundMessage,
    Invoice,
    PaymentLink,
    Promise,
    ReconciliationEvent,
    Reminder,
)
from app.models.reconciliation_event import EventStatus
from app.schemas.dashboard import (
    ConversationEntry,
    DisputeView,
    InvoiceDetail,
    PromiseView,
    QueueRow,
    ReminderView,
    TimelineEntry,
)
from app.services.automation import automation_health
from app.services.closure import close_link_for_invoice, close_payment_link
from app.services.disputes import cases_for, resolve_dispute
from app.services.events import after_id
from app.services.explain import Explanation, explain
from app.services.manual_payments import payment_view, payments_for
from app.services.messaging import retry_failed_deliveries
from app.services.metrics import compute_metrics
from app.services.reconciliation import reprocess_event
from app.services.recovery import escalate_to_human
from app.services.replies import retry_failed_inbound

# Every endpoint here is gated. These reads expose customer names, email
# addresses, amounts owed and the audit trail — that is a breach if it is
# public, whether or not the caller can also change anything.
router = APIRouter(prefix="/api", tags=["dashboard"], dependencies=[OperatorRequired])
log = get_logger("dashboard")


@router.get("/events/stream")
async def events_stream(
    request: Request, last_event_id: int = Query(default=0, ge=0)
) -> StreamingResponse:
    """Stream committed reconciliation updates; clients can fall back to polling."""

    async def generate():
        cursor = last_event_id
        for _ in range(120):
            if await request.is_disconnected():
                return
            emitted = False
            for event_id, event in after_id(cursor):
                cursor = event_id
                emitted = True
                yield f"id: {event_id}\nevent: {event['type']}\ndata: {json.dumps(event)}\n\n"
            if not emitted:
                yield ": heartbeat\n\n"
            await asyncio.sleep(0.25)

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


class RuntimeSafety(BaseModel):
    environment: str
    scheduler: str
    email: str
    razorpay: str
    ai: str
    inbound_email: str


@router.get("/dashboard/runtime", response_model=RuntimeSafety)
def runtime_safety() -> RuntimeSafety:
    """Non-secret operating modes the UI must disclose before an action is taken."""
    if settings.email_dry_run:
        email = "dry_run"
    elif settings.email_redirect_to:
        email = "redirected"
    else:
        email = "direct_customer"
    return RuntimeSafety(
        environment=settings.environment,
        scheduler="enabled" if settings.scheduler_enabled else "disabled",
        email=email,
        razorpay=("live" if settings.razorpay_key_id.startswith("rzp_live_") else "test"),
        ai="enabled" if settings.google_api_key else "deterministic_fallback",
        inbound_email=(
            "native_resend"
            if settings.resend_inbound_webhook_secret
            else ("signed_adapter" if settings.inbound_email_webhook_secret else "simulation_only")
        ),
    )


@router.get("/dashboard/automation")
def automation(session: SessionDep) -> dict:
    """Proof the agent is running, not a statement that it is configured to.

    `/dashboard/runtime` above reports configuration — "scheduler: enabled" — which is
    what the audit correctly refused to accept as evidence. APScheduler runs inside the
    API process; if its thread dies, this API stays healthy, /health stays green, and
    no invoice is ever chased again. Configuration would still say "enabled".

    This reads the `job_runs` table instead: when each job last started, last succeeded,
    how long it took, what it did, and when it is due next.
    """
    return automation_health(session)


#: Maps an audit actor to the badge shown on the timeline.
_PROVENANCE = {
    "ai": "ai",
    "policy": "policy",
    "razorpay": "razorpay",
    "system": "system",
    "scheduler": "system",
    # Human actions are stored as "human:<username>", so the prefix split below yields
    # "human". Without this entry it fell through to the default and every write-off,
    # dispute resolution, and hand-recorded payment was badged SYSTEM on the timeline —
    # the audit trail attributing a person's decision to the machine, which is the one
    # thing an audit trail exists not to do.
    "human": "human",
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
    AuditAction.PAYMENT_LINK_CLOSE_RETRIED: "Payment link closure retried",
    AuditAction.RECONCILIATION_FAILED: "Reconciliation failed",
    AuditAction.RECONCILIATION_RETRIED: "Reconciliation retried",
    AuditAction.POLICY_EVALUATED: "Policy approved",
    AuditAction.POLICY_REJECTED: "Policy rejected",
    AuditAction.REMINDER_SENT: "Reminder sent",
    AuditAction.REMINDER_FAILED: "Reminder failed",
    AuditAction.REPLY_RECEIVED: "Customer replied",
    AuditAction.PROMISE_LOGGED: "Promise logged",
    AuditAction.PROMISE_KEPT: "Promise kept",
    AuditAction.INVOICE_WRITTEN_OFF: "Written off",
    AuditAction.PROMISE_BROKEN: "Promise broken",
    AuditAction.ESCALATED_TO_HUMAN: "Escalated to human",
    AuditAction.DISPUTE_DETECTED: "Dispute detected in customer reply",
    AuditAction.RECOVERY_PAUSED: "Recovery paused",
    AuditAction.DISPUTE_CASE_OPENED: "Human-review case opened",
    AuditAction.DISPUTE_ALREADY_OPEN: "Repeat message — case already open",
    AuditAction.DISPUTE_RESOLVED: "Dispute resolved",
    AuditAction.RECOVERY_RESUMED: "Recovery resumed",
    AuditAction.PAYMENT_DURING_DISPUTE: "Payment received while dispute open",
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
    if entry.action == AuditAction.DISPUTE_DETECTED:
        return f"{base} — {detail.get('reason')}"
    if entry.action == AuditAction.RECOVERY_PAUSED:
        return f"{base} — {detail.get('reason')}"
    if entry.action == AuditAction.PAYMENT_DURING_DISPUTE:
        return f"{base} — {format_inr(int(detail.get('applied_paise') or 0))}"
    return base


# ---------------------------------------------------------------------------
# The conversation view.
# ---------------------------------------------------------------------------

#: Audit actions that are part of the conversation, and how each one is shown.
#: Anything not listed here is machinery — a policy evaluation, a link provisioning,
#: a retry — and belongs on the technical timeline rather than in a conversation a
#: merchant reads to understand what was said.
_CONVERSATION_KINDS: dict[str, tuple[str, str]] = {
    AuditAction.REPLY_RECEIVED: ("customer_message", "Customer"),
    AuditAction.REMINDER_SENT: ("system_message", "Vasooli"),
    AuditAction.REMINDER_FAILED: ("system_message", "Vasooli"),
    AuditAction.DISPUTE_DETECTED: ("ai_analysis", "AI analysis"),
    AuditAction.PROMISE_LOGGED: ("ai_analysis", "AI analysis"),
    AuditAction.RECOVERY_PAUSED: ("policy_decision", "Policy"),
    AuditAction.DISPUTE_CASE_OPENED: ("policy_decision", "Policy"),
    AuditAction.DISPUTE_ALREADY_OPEN: ("policy_decision", "Policy"),
    AuditAction.ESCALATED_TO_HUMAN: ("policy_decision", "Policy"),
    AuditAction.PROMISE_BROKEN: ("policy_decision", "Policy"),
    AuditAction.PROMISE_KEPT: ("policy_decision", "Policy"),
    AuditAction.DISPUTE_RESOLVED: ("human_action", "Merchant"),
    AuditAction.RECOVERY_RESUMED: ("human_action", "Merchant"),
    AuditAction.PAYMENT_RECONCILED: ("payment_event", "Razorpay"),
    AuditAction.PAYMENT_DURING_DISPUTE: ("payment_event", "Razorpay"),
    AuditAction.PAYMENT_LINK_CLOSED: ("payment_event", "Razorpay"),
}


def _conversation_body(
    entry: AuditLog,
    reminders_by_tier: dict[int, Reminder],
    inbound_by_id: dict[str, InboundMessage],
) -> str | None:
    """The actual words for this entry, where there were any.

    A conversation with the messages left out is a status log. The customer's excerpt
    is on the audit row itself; a reminder's body lives on the reminder, which is why
    the tier index is passed in rather than the timeline doing a query per row.
    """
    detail = entry.detail or {}
    if entry.action == AuditAction.REPLY_RECEIVED:
        inbound = inbound_by_id.get(str(detail.get("inbound_message_id") or ""))
        if inbound is not None:
            return inbound.body_text
        excerpt = detail.get("excerpt")
        return str(excerpt) if excerpt else None
    if entry.action in (AuditAction.REMINDER_SENT, AuditAction.REMINDER_FAILED):
        reminder = reminders_by_tier.get(int(detail.get("tier") or 0))
        return reminder.body if reminder else None
    if entry.action == AuditAction.DISPUTE_DETECTED:
        summary = detail.get("summary")
        return str(summary) if summary else None
    if entry.action == AuditAction.DISPUTE_RESOLVED:
        note = detail.get("note")
        return str(note) if note else None
    return None


def _conversation_meta(entry: AuditLog) -> dict:
    """The supporting detail for one entry, filtered to what a merchant would read.

    Deliberately a small allowlist rather than the whole `detail` blob. The audit row
    is the complete record and is still rendered in full on the technical timeline;
    this view is meant to be readable, and a raw JSON dump is not.
    """
    detail = entry.detail or {}
    keep = ("reason", "confidence", "facts", "model", "degraded", "tier", "tone", "case_id")
    meta = {k: detail[k] for k in keep if k in detail and detail[k] not in (None, [], "")}
    if entry.action == AuditAction.PAYMENT_RECONCILED:
        meta["amount_display"] = format_inr(int(detail.get("applied_paise") or 0))
    if entry.action == AuditAction.PAYMENT_DURING_DISPUTE:
        meta["amount_display"] = format_inr(int(detail.get("applied_paise") or 0))
    return meta


def _build_conversation(
    entries: list[AuditLog], reminders: list[Reminder], inbound: list[InboundMessage]
) -> list[ConversationEntry]:
    """Reshape the audit log into a conversation, in order.

    Ordering is by `created_at` and nothing else, so a customer message always appears
    before the analysis of it and the pause that followed. Rows written inside one
    transaction share a timestamp closely enough that the database's insertion order
    settles ties, which is the order they happened in.
    """
    by_tier = {r.tier: r for r in reminders}
    inbound_by_id = {str(message.id): message for message in inbound}
    conversation: list[ConversationEntry] = []

    for entry in sorted(entries, key=lambda e: e.created_at):
        mapping = _CONVERSATION_KINDS.get(entry.action)
        if mapping is None:
            continue
        kind, speaker = mapping
        if entry.actor.startswith("human:"):
            speaker = entry.actor.split(":", 1)[1]
        if entry.action in (AuditAction.REMINDER_SENT, AuditAction.REMINDER_FAILED):
            speaker = f"Vasooli — Tier {(entry.detail or {}).get('tier')}"

        conversation.append(
            ConversationEntry(
                at=entry.created_at,
                kind=kind,  # type: ignore[arg-type]
                speaker=speaker,
                headline=_summarise(entry),
                body=_conversation_body(entry, by_tier, inbound_by_id),
                meta=_conversation_meta(entry),
            )
        )
    return conversation


def _dispute_view(case: DisputeCase, *, payment_while_open: bool) -> DisputeView:
    """One dispute case, with the merchant's next step spelled out."""
    if case.is_open:
        next_action = (
            "Check the customer's claims against your delivery note or purchase order, "
            "then resolve the case. Recovery stays paused until you do."
        )
        if payment_while_open:
            next_action = (
                "Payment has arrived while this dispute is open. Confirm whether the "
                "objection still stands, then resolve the case."
            )
    elif case.recovery_resumed_at is not None:
        next_action = "Resolved. Recovery has resumed."
    else:
        next_action = "Resolved. Recovery stays stopped — resolve again with resume to restart it."

    return DisputeView(
        id=case.id,
        status=str(case.status),
        is_open=case.is_open,
        reason=case.reason,
        summary=case.summary,
        facts=[str(f) for f in (case.facts or [])],
        confidence=case.confidence,
        confidence_display=f"{round(case.confidence * 100)}%",
        source_excerpt=case.source_excerpt,
        detected_by=case.detected_by,
        ai_degraded=case.ai_degraded,
        opened_at=case.opened_at,
        resolved_at=case.resolved_at,
        resolved_by=case.resolved_by,
        resolution_note=case.resolution_note,
        recovery_resumed_at=case.recovery_resumed_at,
        next_action=next_action,
        payment_received_while_open=payment_while_open,
    )


def _next_action(invoice: Invoice) -> str:
    """One line telling a merchant what happens next, without reading the timeline."""
    if invoice.status == InvoiceStatus.RECOVERED:
        return "Recovered"
    if invoice.status == InvoiceStatus.HUMAN_REVIEW:
        if invoice.escalation_reason == "complaint_in_reply":
            return "Recovery paused — customer disputes this invoice"
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
    # One query for the whole page rather than one per row: the queue renders up to
    # 500 invoices and a per-row lookup here is the classic N+1 that only shows up
    # once there is real data.
    disputed = {
        c.invoice_id
        for c in session.exec(
            select(DisputeCase).where(DisputeCase.status == DisputeStatus.OPEN)
        ).all()
    }

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
            dispute_open=i.id in disputed,
            recovered_at=i.recovered_at,
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
    entries = list(
        session.exec(
            select(AuditLog).where(AuditLog.invoice_id == invoice.id).order_by(AuditLog.created_at)
        ).all()
    )
    inbound = list(
        session.exec(
            select(InboundMessage)
            .where(InboundMessage.invoice_id == invoice.id)
            .order_by(InboundMessage.received_at)
        ).all()
    )
    cases = cases_for(session, invoice.id)

    # Read once from the entries already loaded rather than issuing another query:
    # the audit trail is the record of whether money arrived during a dispute, and it
    # is already in memory.
    payment_while_open = any(e.action == AuditAction.PAYMENT_DURING_DISPUTE for e in entries)
    open_case = next((c for c in cases if c.is_open), None)

    reason = _explain_for(session, invoice)

    return InvoiceDetail(
        id=invoice.id,
        invoice_number=invoice.invoice_number,
        customer_name=customer.name if customer else "—",
        customer_email=customer.email if customer else "—",
        amount_display=format_inr(invoice.amount_paise),
        paid_display=format_inr(invoice.amount_paid_paise),
        outstanding_display=format_inr(invoice.outstanding_paise),
        link_paid_display=format_inr(invoice.link_paid_paise),
        external_paid_display=format_inr(invoice.external_paid_paise),
        external_payments=[payment_view(p) for p in payments_for(session, invoice.id)],
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
        simulated_replies_enabled=settings.allow_simulated_replies,
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
        dispute=(
            _dispute_view(open_case, payment_while_open=payment_while_open)
            if open_case is not None
            else None
        ),
        dispute_history=[
            _dispute_view(c, payment_while_open=payment_while_open) for c in cases if not c.is_open
        ],
        conversation=_build_conversation(entries, list(reminders), inbound),
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
def manual_escalate(invoice_id: uuid.UUID, session: SessionDep, operator: Operator) -> dict:
    """Hand an invoice to a human by hand."""
    invoice = session.get(Invoice, invoice_id)
    if invoice is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Invoice not found")
    escalate_to_human(session, invoice, "manual", actor=AuditActor.human(operator))
    session.commit()
    return {"invoice_number": invoice.invoice_number, "status": str(invoice.status)}


@router.post("/dashboard/invoices/{invoice_id}/write-off")
def write_off(invoice_id: uuid.UUID, session: SessionDep, operator: Operator) -> dict:
    """Close an invoice as unrecoverable, and shut the route money could still arrive by.

    Writing off is a decision to stop collecting. Leaving the Razorpay link live after
    it contradicts that decision in the one way that costs money: the customer opens
    the link from an old reminder, pays an invoice the merchant has already removed
    from the books, and the payment lands against a balance nobody is reconciling any
    more. Closure was previously reached only from reconciliation, so this state was
    permanent — the audit's "writing off leaves a payable link open".

    Ordering matches reconciliation, and for the same reason: the write-off commits
    first and is durable, and only then is Razorpay called. A closure failure is
    recorded on the link and retried by the sweep; it never rolls back the decision.
    """
    invoice = session.get(Invoice, invoice_id)
    if invoice is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Invoice not found")

    if invoice.status == InvoiceStatus.WRITTEN_OFF:
        # Two operators pressing the same button is ordinary. Re-attempt the closure
        # rather than the write-off, since an earlier failure is exactly why someone
        # would press it again.
        closed = _close_link_quietly(session, invoice)
        return {
            "invoice_number": invoice.invoice_number,
            "status": str(invoice.status),
            "payment_link_closed": closed,
            "note": "This invoice was already written off.",
        }

    if invoice.status == InvoiceStatus.RECOVERED:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "This invoice has been paid — it cannot be written off",
        )

    invoice.status = InvoiceStatus.WRITTEN_OFF
    session.add(invoice)
    session.add(
        AuditLog(
            invoice_id=invoice.id,
            actor=AuditActor.human(operator),
            action=AuditAction.INVOICE_WRITTEN_OFF,
            detail={"outstanding_paise": invoice.outstanding_paise},
        )
    )
    session.commit()

    closed = _close_link_quietly(session, invoice)
    return {
        "invoice_number": invoice.invoice_number,
        "status": str(invoice.status),
        "payment_link_closed": closed,
    }


def _close_link_quietly(session: Session, invoice: Invoice) -> bool:
    """Close this invoice's payment link without letting Razorpay break the write-off.

    A failure here is already recorded on the link and surfaces in the exceptions
    queue with a retry button, so raising would only turn a retryable operational task
    into a failed request against a decision that is already committed.
    """
    try:
        return close_link_for_invoice(session, invoice.id)
    except Exception:  # noqa: BLE001
        log.exception("write_off.closure_failed", invoice_number=invoice.invoice_number)
        return False


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

    #: Customer replies that were received and stored but could not be interpreted.
    #:
    #: Previously a dead end and invisible: the webhook answered 200 (which is correct —
    #: a 5xx would make the provider redeliver into the same bug), so nothing retried
    #: them and no screen showed them. A customer writing "we paid this on the 14th"
    #: could land in a column nobody queries while the reminders carried on.
    stuck_inbound = session.exec(
        select(InboundMessage)
        .where(
            InboundMessage.processed_at.is_(None),  # type: ignore[union-attr]
            InboundMessage.processing_error.is_not(None),  # type: ignore[union-attr]
        )
        .order_by(InboundMessage.received_at.desc())  # type: ignore[attr-defined]
    ).all()

    inbound = [
        {
            "id": str(message.id),
            "invoice_number": numbers.get(message.invoice_id, "—"),
            "sender": message.sender,
            "subject": message.subject,
            "excerpt": message.body_text[:200],
            "error": message.processing_error,
            "attempts": message.processing_attempts,
            "last_attempt_at": (
                message.last_attempt_at.isoformat() if message.last_attempt_at else None
            ),
            "next_retry_at": (message.next_retry_at.isoformat() if message.next_retry_at else None),
            "exhausted": message.is_exhausted,
            "received_at": message.received_at.isoformat(),
        }
        for message in stuck_inbound
    ]

    return {
        "reconciliation": reconciliation,
        "communication": communication,
        "unclosed_links": open_links,
        "inbound": inbound,
        "total": len(reconciliation) + len(communication) + len(open_links) + len(inbound),
    }


@router.post("/dashboard/exceptions/inbound/{message_id}/retry")
def retry_inbound(message_id: uuid.UUID, session: SessionDep, operator: Operator) -> dict:
    """Reprocess one stored customer reply now, ignoring its backoff.

    Works on exhausted messages too — that is the entire point of a manual retry. The
    message body is already durable evidence; this only re-runs the interpretation of
    it, and cannot mark an invoice paid.
    """
    message = session.get(InboundMessage, message_id)
    if message is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Inbound message not found")
    if message.processed_at is not None:
        return {"message_id": str(message_id), "recovered": True, "note": "already processed"}

    session.add(
        AuditLog(
            invoice_id=message.invoice_id,
            actor=AuditActor.human(operator),
            action=AuditAction.INBOUND_REPROCESSED,
            detail={
                "inbound_message_id": str(message_id),
                "attempts_before": message.processing_attempts,
            },
        )
    )
    session.commit()

    report = retry_failed_inbound(session, force_ids=[str(message_id)])
    session.refresh(message)
    return {
        "message_id": str(message_id),
        "recovered": message.processed_at is not None,
        "attempts": message.processing_attempts,
        "error": message.processing_error,
        "swept": report["attempted"],
    }


@router.post("/dashboard/exceptions/events/{provider_event_id}/retry")
def retry_event(provider_event_id: str, session: SessionDep, operator: Operator) -> dict:
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
            actor=AuditActor.human(operator),
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


@router.post("/dashboard/exceptions/links/{link_id}/retry-closure")
def retry_closure(link_id: uuid.UUID, session: SessionDep, operator: Operator) -> dict:
    """Re-attempt closing one payment link, ignoring its backoff.

    Deliberately narrow. This closes a link and nothing else — it cannot change an
    amount, reopen a link, or touch payment state. An operator button that could
    manipulate a payment link arbitrarily would be a far larger hole than the problem
    it solves.

    Safe to press repeatedly: closure is idempotent on `cancelled_at`, and Razorpay
    reporting the link as already cancelled counts as success rather than an error.
    """
    link = session.get(PaymentLink, link_id)
    if link is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Payment link not found")

    if link.cancelled_at is not None:
        return {
            "link_id": str(link_id),
            "closed": True,
            "note": "already closed",
            "status": link.status,
        }

    invoice = session.get(Invoice, link.invoice_id)
    if invoice is None or not invoice.link_should_be_closed:
        # Closing a link on an invoice still being collected would remove the
        # customer's way to pay. Settled and written-off invoices both qualify:
        # neither should accept another rupee.
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Invoice is still being collected — closing its link would remove the way to pay it",
        )

    session.add(
        AuditLog(
            invoice_id=invoice.id,
            actor=AuditActor.human(operator),
            action=AuditAction.PAYMENT_LINK_CLOSE_RETRIED,
            detail={
                "payment_link_id": link.razorpay_payment_link_id,
                "attempts_before": link.closure_attempts,
            },
        )
    )
    session.commit()

    closed = close_payment_link(session, link)
    session.refresh(link)
    return {
        "link_id": str(link_id),
        "closed": closed,
        "status": link.status,
        "error": link.closure_error,
        "attempts": link.closure_attempts,
    }


# ---------------------------------------------------------------------------
# Dispute review. Customer Conversation Safety.
# ---------------------------------------------------------------------------


class ResolveDispute(BaseModel):
    """What a merchant decided about a dispute."""

    note: str = Field(default="", max_length=1000)
    #: Resolving and resuming are separate decisions. A merchant who agrees the
    #: customer was right closes the case and leaves recovery stopped; one who checked
    #: the paperwork and found the invoice correct closes it and resumes. Defaulting
    #: to False keeps the safe choice the default one.
    resume_recovery: bool = False

    @model_validator(mode="after")
    def require_resume_reason(self) -> "ResolveDispute":
        if self.resume_recovery and not self.note.strip():
            raise ValueError("A decision note is required before recovery can resume")
        return self


@router.post("/dashboard/disputes/{case_id}/resolve")
def resolve_dispute_case(
    case_id: uuid.UUID,
    payload: ResolveDispute,
    session: SessionDep,
    operator: Operator,
) -> dict:
    """Close a dispute case, and optionally put the invoice back in the cadence.

    The only write path out of a dispute. There is no endpoint that resumes recovery
    without resolving, and none that resolves on the AI's behalf — a dispute is opened
    by policy acting on what the model read, and closed by a person.
    """
    case = session.get(DisputeCase, case_id)
    if case is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Dispute case not found")

    if not case.is_open:
        # Not an error. Two operators clicking resolve on the same case is ordinary,
        # and the second one should see the outcome rather than a failure.
        return {
            "case_id": str(case.id),
            "status": str(case.status),
            "resumed": case.recovery_resumed_at is not None,
            "note": "This case was already resolved.",
        }

    case, resumed = resolve_dispute(
        session,
        case,
        resolved_by=AuditActor.human(operator),
        note=payload.note,
        resume_recovery=payload.resume_recovery,
    )
    session.commit()

    invoice = session.get(Invoice, case.invoice_id)
    return {
        "case_id": str(case.id),
        "invoice_number": invoice.invoice_number if invoice else None,
        "status": str(case.status),
        "resumed": resumed,
        "invoice_status": str(invoice.status) if invoice else None,
        "note": (
            "Dispute resolved — recovery has resumed."
            if resumed
            else "Dispute resolved. Recovery stays stopped for this invoice."
        ),
    }


@router.get("/dashboard/disputes")
def open_disputes(session: SessionDep) -> list[dict]:
    """Every invoice currently paused for a dispute, newest first.

    A merchant's working list. Deliberately a flat summary rather than the full case:
    the detail lives on the invoice page, next to the conversation it came from.
    """
    cases = session.exec(
        select(DisputeCase)
        .where(DisputeCase.status == DisputeStatus.OPEN)
        .order_by(DisputeCase.opened_at.desc())  # type: ignore[attr-defined]
    ).all()

    rows = []
    for case in cases:
        invoice = session.get(Invoice, case.invoice_id)
        customer = session.get(Customer, invoice.customer_id) if invoice else None
        rows.append(
            {
                "case_id": str(case.id),
                "invoice_id": str(case.invoice_id),
                "invoice_number": invoice.invoice_number if invoice else "—",
                "customer_name": customer.name if customer else "—",
                "outstanding_display": format_inr(invoice.outstanding_paise) if invoice else "—",
                "reason": case.reason,
                "confidence_display": f"{round(case.confidence * 100)}%",
                "opened_at": case.opened_at,
                "detected_by": case.detected_by,
            }
        )
    return rows
