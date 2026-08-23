"""The recovery cycle. Doc §3, Phase 8.

One function walks the entire loop: resolve promises, pick a tier, diagnose, draft,
evaluate policy, then send or escalate. The scheduled run and the dashboard's manual
trigger both call it — a demo path that diverges from the production path proves
nothing.

The ordering inside the cycle is deliberate:

1. **Promises first.** An expired promise has to become a broken promise before tier
   selection runs, or the invoice stays paused for another whole day.
2. **Diagnose before drafting**, because a dispute-likely invoice must never reach a
   drafting call at all.
3. **Policy evaluates the actual drafted text**, not a plan to draft something. The
   banned-language check has nothing to inspect otherwise.
4. **Send last**, and only on approval.
"""

import uuid
from dataclasses import dataclass, field
from datetime import timedelta

from sqlalchemy import text
from sqlmodel import Session, select

from app.ai import DiagnosisInputs, DraftInputs, diagnose, draft_reminder
from app.core.clock import days_overdue as days_overdue_for
from app.core.clock import today_ist, utcnow
from app.core.constants import (
    PROMISE_GRACE_DAYS,
    InvoiceStatus,
    PromiseStatus,
    ReasonCategory,
)
from app.core.db import engine
from app.core.logging import get_logger
from app.models import (
    AuditAction,
    AuditActor,
    AuditLog,
    Customer,
    Invoice,
    Merchant,
    PaymentLink,
    Promise,
    Reminder,
)
from app.policy import RequiredAction, evaluate_reminder, next_tier_for
from app.policy.banned_language import find_banned_phrases
from app.services.messaging import deliver_reminder

log = get_logger("recovery")

#: Postgres advisory lock id. Two cycles running at once would double-send, and
#: APScheduler's max_instances only guards a single process — a second Railway worker
#: would not know about it.
CYCLE_LOCK_ID = 0x7A50_0111


@dataclass
class CycleReport:
    considered: int = 0
    sent: int = 0
    held: int = 0
    escalated: int = 0
    promises_broken: int = 0
    diagnosed: int = 0
    skipped_no_tier: int = 0
    errors: list[dict[str, str]] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "considered": self.considered,
            "sent": self.sent,
            "held": self.held,
            "escalated": self.escalated,
            "promises_broken": self.promises_broken,
            "diagnosed": self.diagnosed,
            "skipped_no_tier": self.skipped_no_tier,
            "errors": self.errors,
        }


def escalate_to_human(session: Session, invoice: Invoice, reason: str) -> None:
    """Take an invoice out of automation. Doc §3 Stage 3."""
    if invoice.escalated_to_human_at is None:
        invoice.escalated_to_human_at = utcnow()
    invoice.escalation_reason = reason
    invoice.status = InvoiceStatus.HUMAN_REVIEW
    session.add(invoice)
    session.add(
        AuditLog(
            invoice_id=invoice.id,
            actor=AuditActor.POLICY,
            action=AuditAction.ESCALATED_TO_HUMAN,
            detail={"reason": reason, "tier_reached": invoice.current_tier},
        )
    )


def sweep_promises(session: Session, report: CycleReport) -> None:
    """Resolve promises whose date has passed. Doc §3 Stage 4.

    A broken promise resumes escalation at the tier it was paused at, never reset to
    polite. Someone who promised at Tier 2 and did not pay should not receive a
    friendly first nudge as though nothing happened.
    """
    today = today_ist()
    active = session.exec(select(Promise).where(Promise.status == PromiseStatus.ACTIVE)).all()

    for promise in active:
        invoice = session.get(Invoice, promise.invoice_id)
        if invoice is None:
            continue

        # Paid in the meantime: reconciliation already marked it kept.
        if invoice.is_fully_paid:
            continue

        if today <= promise.promised_date + timedelta(days=PROMISE_GRACE_DAYS):
            continue

        promise.status = PromiseStatus.BROKEN
        promise.resolved_at = utcnow()
        session.add(promise)

        invoice.status = InvoiceStatus.CHASING
        invoice.current_tier = promise.tier_at_pause
        session.add(invoice)

        customer = session.get(Customer, invoice.customer_id)
        if customer is not None:
            # Feeds back into future diagnoses for this customer.
            customer.broken_promises += 1
            session.add(customer)

        session.add(
            AuditLog(
                invoice_id=invoice.id,
                actor=AuditActor.SYSTEM,
                action=AuditAction.PROMISE_BROKEN,
                detail={
                    "promised_date": str(promise.promised_date),
                    "resumed_at_tier": promise.tier_at_pause,
                },
            )
        )
        report.promises_broken += 1
        log.info(
            "recovery.promise_broken",
            invoice_number=invoice.invoice_number,
            resumed_at_tier=promise.tier_at_pause,
        )


def _active_promise_date(session: Session, invoice_id: uuid.UUID):
    promise = session.exec(
        select(Promise).where(
            Promise.invoice_id == invoice_id, Promise.status == PromiseStatus.ACTIVE
        )
    ).first()
    return promise.promised_date if promise else None


def _ensure_diagnosis(
    session: Session, invoice: Invoice, customer: Customer, report: CycleReport, *, use_llm: bool
) -> None:
    """Diagnose, but only when a reminder is actually due.

    Re-run as tiers advance rather than cached forever: "unresponsive" is defined as
    no reply after Tier 2, so the same invoice can legitimately change category as it
    ages. Not run on every tick — only when the cycle is about to act.
    """
    has_reply = False  # Phase 7 wires inbound replies in.
    inputs = DiagnosisInputs(
        total_invoices=customer.total_invoices,
        invoices_paid_late=customer.invoices_paid_late,
        invoices_defaulted=customer.invoices_defaulted,
        broken_promises=customer.broken_promises,
        avg_invoice_paise=customer.avg_invoice_paise,
        amount_paise=invoice.amount_paise,
        days_overdue=days_overdue_for(invoice.due_at),
        has_prior_dispute_note=invoice.has_prior_dispute_note,
        has_reply=has_reply,
        reply_has_complaint=False,
        current_tier=invoice.current_tier,
    )
    result = diagnose(inputs, invoice_number=invoice.invoice_number, use_llm=use_llm)

    invoice.reason_category = result.category
    invoice.reason_explanation = result.explanation
    invoice.reason_confidence = result.confidence
    invoice.reason_diagnosed_at = utcnow()
    invoice.reason_llm_disagreed = result.llm_disagreed
    session.add(invoice)

    session.add(
        AuditLog(
            invoice_id=invoice.id,
            actor=AuditActor.AI,
            action=AuditAction.DIAGNOSED,
            detail={
                "category": result.category.value,
                "explanation": result.explanation,
                "confidence": result.confidence,
                "source": result.source,
                "llm_disagreed": result.llm_disagreed,
                "signals": list(result.signals_used),
            },
        )
    )
    report.diagnosed += 1


def _eligible_invoices(session: Session, invoice_ids: list[uuid.UUID] | None) -> list[Invoice]:
    query = select(Invoice).where(
        Invoice.status.in_(  # type: ignore[attr-defined]
            [
                InvoiceStatus.PENDING,
                InvoiceStatus.CHASING,
                InvoiceStatus.PROMISE_ACTIVE,
                InvoiceStatus.PARTIALLY_PAID,
            ]
        )
    )
    if invoice_ids:
        query = query.where(Invoice.id.in_(invoice_ids))  # type: ignore[attr-defined]
    return list(session.exec(query.order_by(Invoice.due_at)).all())


def run_recovery_cycle(
    session: Session,
    *,
    dry_run: bool = False,
    invoice_ids: list[uuid.UUID] | None = None,
    use_llm: bool = True,
    limit: int | None = None,
) -> CycleReport:
    """Walk the recovery loop once over every eligible invoice.

    `dry_run` evaluates everything and sends nothing, so the manual trigger can be
    used to inspect what *would* happen before committing to it.
    """
    report = CycleReport()

    # One cycle at a time across the whole deployment. APScheduler's max_instances
    # only covers a single process; a second worker would happily run a parallel cycle
    # and both would pass the same policy checks before either recorded a send.
    # `.one()` yields a Row, not a bool — and `not (False,)` is False, because a
    # non-empty tuple is truthy. Unpacking is what makes this lock do anything at all;
    # without it the guard silently passed and two workers could both run a cycle.
    #
    # The lock is held on its OWN connection, not the ORM session's. A Postgres
    # advisory lock belongs to the connection that took it, and `session.commit()`
    # hands the session's connection back to the pool mid-cycle — so unlocking through
    # the session releases a lock on a different connection than the one still holding
    # it. That leak makes every later cycle in the process report "already running"
    # and quietly do nothing.
    lock_conn = engine.connect()
    got_lock = bool(
        lock_conn.execute(
            text("SELECT pg_try_advisory_lock(:key)"), {"key": CYCLE_LOCK_ID}
        ).scalar()
    )
    if not got_lock:
        lock_conn.close()
        log.warning("recovery.cycle_already_running")
        report.errors.append({"invoice_number": "-", "error": "cycle already running"})
        return report

    try:
        sweep_promises(session, report)
        session.commit()

        merchant = session.exec(select(Merchant)).first()
        merchant_name = merchant.name if merchant else "Vasooli"

        invoices = _eligible_invoices(session, invoice_ids)
        if limit:
            invoices = invoices[:limit]

        for invoice in invoices:
            report.considered += 1
            try:
                _process_invoice(
                    session,
                    invoice,
                    merchant_name=merchant_name,
                    report=report,
                    dry_run=dry_run,
                    use_llm=use_llm,
                )
                session.commit()
            except Exception as exc:  # noqa: BLE001 - one invoice must not stop the cycle
                session.rollback()
                report.errors.append({"invoice_number": invoice.invoice_number, "error": str(exc)})
                log.exception("recovery.invoice_failed", invoice_number=invoice.invoice_number)
    finally:
        lock_conn.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": CYCLE_LOCK_ID})
        lock_conn.commit()
        lock_conn.close()

    log.info("recovery.cycle_complete", **report.as_dict())
    return report


def _process_invoice(
    session: Session,
    invoice: Invoice,
    *,
    merchant_name: str,
    report: CycleReport,
    dry_run: bool,
    use_llm: bool,
) -> None:
    """Decide and act on one invoice. Committed by the caller."""
    days_overdue = days_overdue_for(invoice.due_at)
    sent_tiers = frozenset(
        r.tier
        for r in session.exec(select(Reminder).where(Reminder.invoice_id == invoice.id)).all()
    )

    tier = next_tier_for(days_overdue=days_overdue, sent_tiers=sent_tiers)
    if tier is None:
        report.skipped_no_tier += 1
        return

    customer = session.get(Customer, invoice.customer_id)
    if customer is None:
        raise ValueError("invoice has no customer")

    _ensure_diagnosis(session, invoice, customer, report, use_llm=use_llm)

    # Dispute-likely never reaches a drafting call. Doc §3 Stage 2 routes it straight
    # to a human, and generating copy we will not send is wasted tokens and a
    # confusing audit trail.
    # `==`, not `is`. SQLModel disables validation on table models, so a category
    # loaded from Postgres is a plain str while one just assigned in memory is the
    # enum. StrEnum compares equal to both; identity would silently miss half the
    # cases and quietly send an automated chase on a disputed invoice.
    if invoice.reason_category == ReasonCategory.DISPUTE_LIKELY:
        if not dry_run:
            escalate_to_human(session, invoice, "dispute_likely")
        report.escalated += 1
        return

    link = session.exec(select(PaymentLink).where(PaymentLink.invoice_id == invoice.id)).first()

    draft_inputs = DraftInputs(
        merchant_name=merchant_name,
        customer_name=customer.name,
        invoice_number=invoice.invoice_number,
        outstanding_paise=invoice.outstanding_paise,
        due_date=invoice.due_at.strftime("%d %B %Y"),
        days_overdue=days_overdue,
        payment_url=link.short_url if link else "",
        reason_explanation=invoice.reason_explanation or "",
        tier=tier,
    )
    draft = draft_reminder(draft_inputs, use_llm=use_llm)

    decision = evaluate_reminder(
        invoice_number=invoice.invoice_number,
        status=invoice.status,
        reason_category=invoice.reason_category,
        has_prior_dispute_note=invoice.has_prior_dispute_note,
        outstanding_paise=invoice.outstanding_paise,
        days_overdue=days_overdue,
        reminders_sent=invoice.reminders_sent,
        sent_tiers=sent_tiers,
        last_reminder_at=invoice.last_reminder_at,
        active_promise_date=_active_promise_date(session, invoice.id),
        proposed_tier=tier,
        drafted_subject=draft.subject,
        drafted_body=draft.body,
        now=utcnow(),
    )

    # One regeneration attempt when the only objection is the drafted wording. Doc §5
    # allows exactly one; a third attempt would be trusting the model to eventually
    # behave, which is what the rules layer exists to avoid.
    banned_check = next(c for c in decision.checks if c.name == "no_banned_language")
    only_language_failed = decision.failed_checks == [banned_check]
    if only_language_failed and use_llm:
        draft = draft_reminder(
            draft_inputs,
            banned_phrases=find_banned_phrases(f"{draft.subject}\n{draft.body}"),
        )
        decision = evaluate_reminder(
            invoice_number=invoice.invoice_number,
            status=invoice.status,
            reason_category=invoice.reason_category,
            has_prior_dispute_note=invoice.has_prior_dispute_note,
            outstanding_paise=invoice.outstanding_paise,
            days_overdue=days_overdue,
            reminders_sent=invoice.reminders_sent,
            sent_tiers=sent_tiers,
            last_reminder_at=invoice.last_reminder_at,
            active_promise_date=_active_promise_date(session, invoice.id),
            proposed_tier=tier,
            drafted_subject=draft.subject,
            drafted_body=draft.body,
            now=utcnow(),
        )

    session.add(
        AuditLog(
            invoice_id=invoice.id,
            actor=AuditActor.POLICY,
            action=AuditAction.POLICY_EVALUATED
            if decision.approved
            else AuditAction.POLICY_REJECTED,
            detail=decision.to_dict(),
        )
    )

    if decision.required_action is RequiredAction.ESCALATE_TO_HUMAN:
        if not dry_run:
            escalate_to_human(session, invoice, decision.reason)
        report.escalated += 1
        return

    if not decision.approved:
        report.held += 1
        return

    if dry_run:
        report.sent += 1
        return

    deliver_reminder(
        session,
        invoice=invoice,
        customer=customer,
        tier=tier,
        draft=draft,
        decision=decision,
    )
    report.sent += 1

    # Tier 3 both sends AND hands over. Doc §3: Vasooli does not escalate beyond this
    # on its own, so the final notice goes out and a human takes it from there.
    if tier == 3:
        escalate_to_human(session, invoice, "tier_3_reached")
        report.escalated += 1
