"""The recovery cycle. Doc §3.

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
    ReminderPolicyVersion,
)
from app.policy import RequiredAction, evaluate_reminder, next_tier_for
from app.policy.banned_language import find_banned_phrases
from app.services.ai_audit import AITask, record_ai_outcome
from app.services.authorization import merchant_scope, service_scope, set_merchant_context
from app.services.billing import subscription_is_active
from app.services.closure import retry_pending_closures
from app.services.disputes import has_open_dispute
from app.services.messaging import deliver_reminder, retry_failed_deliveries
from app.services.reconciliation import retry_failed_events

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
    deliveries_retried: int = 0
    deliveries_recovered: int = 0
    closures_retried: int = 0
    closures_completed: int = 0
    events_retried: int = 0
    events_recovered: int = 0
    ai_disabled_after_failure: bool = False
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
            "deliveries_retried": self.deliveries_retried,
            "deliveries_recovered": self.deliveries_recovered,
            "closures_retried": self.closures_retried,
            "closures_completed": self.closures_completed,
            "events_retried": self.events_retried,
            "events_recovered": self.events_recovered,
            "ai_disabled_after_failure": self.ai_disabled_after_failure,
            "errors": self.errors,
        }


def escalate_to_human(
    session: Session, invoice: Invoice, reason: str, *, actor: str = AuditActor.POLICY
) -> None:
    """Take an invoice out of automation. Doc §3 Stage 3.

    `actor` defaults to the policy engine because that is who escalates on the cycle.
    An operator escalating by hand passes their own identity: "escalated by policy"
    and "escalated by Priya at 4pm" are different facts, and recording the second as
    the first is a hole in the audit trail rather than a cosmetic detail.
    """
    if invoice.escalated_to_human_at is None:
        invoice.escalated_to_human_at = utcnow()
    invoice.escalation_reason = reason
    invoice.status = InvoiceStatus.HUMAN_REVIEW
    session.add(invoice)
    session.add(
        AuditLog(
            invoice_id=invoice.id,
            actor=actor,
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

        # `promises` carries no merchant_id and so has no policy of its own; the
        # invoice and customer rows this touches do.
        set_merchant_context(session, invoice.merchant_id)

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
    # Real reply history, not a placeholder.
    #
    # This was hardcoded False, which meant every customer who wrote back was still
    # classified "unresponsive" after Tier 2 — the category reserved for people who
    # ignore you. It changed the tone of the next reminder and could hand a
    # cooperative customer to a human as a defaulter.
    has_reply = invoice.has_replied
    # A complaint sets DISPUTE_LIKELY and escalates at the time it arrives; carrying
    # that forward keeps the classification stable on later cycles.
    reply_has_complaint = (
        invoice.reason_category == ReasonCategory.DISPUTE_LIKELY
        or invoice.escalation_reason == "complaint_in_reply"
    )

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
        reply_has_complaint=reply_has_complaint,
        current_tier=invoice.current_tier,
    )
    result = diagnose(inputs, invoice_number=invoice.invoice_number, use_llm=use_llm)

    invoice.reason_category = result.category
    invoice.reason_explanation = result.explanation
    invoice.reason_confidence = result.confidence
    invoice.reason_diagnosed_at = utcnow()
    invoice.reason_llm_disagreed = result.llm_disagreed
    session.add(invoice)

    if use_llm:
        if result.source == "rule_based":
            report.ai_disabled_after_failure = True
        record_ai_outcome(
            session,
            invoice_id=invoice.id,
            task=AITask.DIAGNOSE,
            model=None if result.source == "rule_based" else result.source,
            models_attempted=() if result.source == "rule_based" else (result.source,),
            accepted=result.source != "rule_based",
            used_fallback=result.source == "rule_based",
            reason=(
                "no model answered; rule-based explanation used"
                if result.source == "rule_based"
                else None
            ),
        )

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
    # Do not set idle_session_timeout here. This connection is intentionally idle while
    # the cycle works; expiring it would release the lock while the original cycle was
    # still sending. A real process or connection crash closes the socket and Postgres
    # releases the session-scoped lock automatically.
    got_lock = bool(
        lock_conn.execute(
            text("SELECT pg_try_advisory_lock(:key)"), {"key": CYCLE_LOCK_ID}
        ).scalar()
    )
    # Commit immediately. The advisory lock is session-scoped and survives this, but
    # leaving the transaction open parks the connection in "idle in transaction",
    # where the idle-session timeout does not apply and the lock cannot self-heal.
    lock_conn.commit()

    if not got_lock:
        lock_conn.close()
        log.warning("recovery.cycle_already_running")
        report.errors.append({"invoice_number": "-", "error": "cycle already running"})
        return report

    try:
        # The cycle is cross-tenant by definition: it walks every merchant's ledger and
        # has no request to take a tenant from. Held here rather than in the scheduler
        # wrapper so the manual trigger, the worker and the tests are covered too —
        # without it, a connection that does not bypass row-level security sees no
        # invoices at all and the cycle reports a clean run having done nothing.
        with service_scope(session):
            sweep_promises(session, report)
            session.commit()

            # Re-attempt failed deliveries before choosing new tiers. A customer owed a
            # Tier 1 reminder that bounced should get that one, not skip to Tier 2.
            if not dry_run:
                retry = retry_failed_deliveries(session)
                report.deliveries_retried = retry["attempted"]
                report.deliveries_recovered = retry["recovered"]

                # A recovered invoice whose link is still live is a customer who can pay
                # twice. Transient Razorpay failures during reconciliation land here.
                closures = retry_pending_closures(session)
                report.closures_retried = closures["attempted"]
                report.closures_completed = closures["closed"]

                # Webhooks that failed reconciliation. Razorpay has stopped redelivering
                # them (we returned 200), so this sweep is the only thing that will.
                events = retry_failed_events(session)
                report.events_retried = events["attempted"]
                report.events_recovered = events["recovered"]

            invoices = _eligible_invoices(session, invoice_ids)
            if limit:
                invoices = invoices[:limit]

            for invoice in invoices:
                report.considered += 1
                try:
                    # Selecting the work is cross-tenant; acting on it is not. Pinning
                    # the tenant for the whole invoice — not just one statement — is
                    # what lets the diagnosis, the reminder row and the delivery
                    # updates all commit, since each commit would otherwise drop the
                    # tenant and the next write would be refused by WITH CHECK.
                    with merchant_scope(session, invoice.merchant_id):
                        merchant = session.get(Merchant, invoice.merchant_id)
                        _process_invoice(
                            session,
                            invoice,
                            merchant_name=merchant.name if merchant else "Vasooli",
                            report=report,
                            dry_run=dry_run,
                            use_llm=use_llm and not report.ai_disabled_after_failure,
                        )
                        session.commit()
                except Exception as exc:  # noqa: BLE001 - one invoice must not stop the cycle
                    session.rollback()
                    report.errors.append(
                        {"invoice_number": invoice.invoice_number, "error": str(exc)}
                    )
                    log.exception("recovery.invoice_failed", invoice_number=invoice.invoice_number)
    finally:
        try:
            lock_conn.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": CYCLE_LOCK_ID})
            lock_conn.commit()
        finally:
            # Closing is what actually guarantees release, even if the unlock
            # statement itself failed on a broken connection.
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
    merchant = session.get(Merchant, invoice.merchant_id)
    if merchant is None:
        report.held += 1
        return
    if merchant.mode == "live" and not subscription_is_active(session, merchant.id):
        log.info("recovery.held_billing_inactive", merchant_id=str(merchant.id))
        report.held += 1
        return
    policy_version = session.exec(
        select(ReminderPolicyVersion)
        .where(
            ReminderPolicyVersion.merchant_id == merchant.id,
            ReminderPolicyVersion.is_active.is_(True),  # type: ignore[union-attr]
        )
        .order_by(ReminderPolicyVersion.version.desc())
    ).first()
    tier_offsets = policy_version.tier_offsets if policy_version else None
    cooldown_days = policy_version.cooldown_days if policy_version else None
    max_attempts = policy_version.max_attempts if policy_version else None
    days_overdue = days_overdue_for(invoice.due_at)
    # Successfully DELIVERED tiers only. A reminder row whose send failed has
    # `sent_at` NULL and must not count: counting it made the cycle believe the tier
    # was done, so the customer never received that reminder and the invoice was
    # never chased again — a silent strand with no error anywhere.
    sent_tiers = frozenset(
        r.tier
        for r in session.exec(
            select(Reminder).where(
                Reminder.invoice_id == invoice.id,
                Reminder.sent_at.is_not(None),  # type: ignore[union-attr]
            )
        ).all()
    )

    # A tier already attempted and awaiting retry must not be re-drafted as a new
    # send: the customer is owed the message policy already approved, and the unique
    # constraint on (invoice_id, tier) would reject the duplicate anyway.
    pending_tiers = frozenset(
        r.tier
        for r in session.exec(
            select(Reminder).where(
                Reminder.invoice_id == invoice.id,
                Reminder.sent_at.is_(None),  # type: ignore[union-attr]
            )
        ).all()
    )

    tier = next_tier_for(
        days_overdue=days_overdue, sent_tiers=sent_tiers, tier_offsets=tier_offsets
    )
    if tier is None or tier in pending_tiers:
        report.skipped_no_tier += 1
        return

    customer = session.get(Customer, invoice.customer_id)
    if customer is None:
        raise ValueError("invoice has no customer")

    ai_requested = use_llm
    _ensure_diagnosis(session, invoice, customer, report, use_llm=use_llm)
    use_llm = use_llm and not report.ai_disabled_after_failure

    # Commit the diagnosis before drafting.
    #
    # Writing to the invoice takes a row lock that Postgres holds until commit. Left
    # open, it would span the drafting call — an external HTTP request to the model —
    # and a payment webhook arriving in that window would block on the invoice row
    # until the model replied. A slow model would stall reconciliation of real money.
    #
    # Same rule as the Razorpay closure: never hold a database lock across a network
    # call to a third party.
    session.commit()

    # An open dispute case ends the cycle for this invoice before a single token is
    # spent. `no_open_dispute` in the policy engine is the authoritative check and
    # still runs below for anything that reaches it, but a customer who has told us
    # the bill is wrong should not have reminder copy drafted about it at all.
    dispute_open = has_open_dispute(session, invoice.id)
    if dispute_open:
        log.info(
            "recovery.dispute_open",
            invoice_number=invoice.invoice_number,
            status=str(invoice.status),
        )
        report.held += 1
        return

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

    if ai_requested:
        fell_back = draft.generated_by == "template_fallback"
        if fell_back:
            # Treat the first full-model failure as a circuit-breaker signal for the
            # remainder of this cycle. Deterministic rules/templates are immediately
            # available and do not multiply one outage by the ledger size.
            report.ai_disabled_after_failure = True
        record_ai_outcome(
            session,
            invoice_id=invoice.id,
            task=AITask.DRAFT_REMINDER,
            model=None if fell_back else draft.generated_by,
            models_attempted=() if fell_back else (draft.generated_by,),
            accepted=not fell_back,
            used_fallback=fell_back,
            reason=(
                # Either no model answered, or one did and its figures did not match
                # the invoice. Both land here, and the distinction matters: a model
                # inventing an amount is a different problem from a model being down.
                (
                    "AI circuit breaker was open; deterministic template used"
                    if not use_llm
                    else "no model answered, or its figures did not match the invoice"
                )
                if fell_back
                else None
            ),
        )

    decision = evaluate_reminder(
        invoice_number=invoice.invoice_number,
        status=invoice.status,
        reason_category=invoice.reason_category,
        has_prior_dispute_note=invoice.has_prior_dispute_note,
        has_open_dispute=dispute_open,
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
        tier_offsets=tier_offsets,
        cooldown_days=cooldown_days,
        max_attempts=max_attempts,
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
            has_open_dispute=dispute_open,
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
            tier_offsets=tier_offsets,
            cooldown_days=cooldown_days,
            max_attempts=max_attempts,
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

    # Final check under a row lock, immediately before sending.
    #
    # Everything between tier selection and here involved external calls — diagnosis
    # and drafting both hit the model — and a payment or a promise can land in that
    # window. Chasing a customer who has already paid is the worst false positive this
    # system can produce, and it is the one a merchant hears about.
    #
    # The lock is taken here rather than around the whole function deliberately: held
    # across the LLM calls it would pin the invoice row for the length of a network
    # request, and a slow model would block reconciliation of a real payment.
    # populate_existing is what makes this a real check.
    #
    # Without it SQLAlchemy hands back the object already in the identity map, carrying
    # the attribute values it was loaded with — so the row gets locked, the SQL runs,
    # and the guard still reads a stale `status` and `amount_paid_paise` from before
    # the payment. The lock would be real and the check would be theatre.
    fresh = session.exec(
        select(Invoice)
        .where(Invoice.id == invoice.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).one()
    if fresh.is_fully_paid or fresh.status in (
        InvoiceStatus.RECOVERED,
        InvoiceStatus.WRITTEN_OFF,
        InvoiceStatus.HUMAN_REVIEW,
        InvoiceStatus.PROMISE_ACTIVE,
    ):
        log.info(
            "recovery.aborted_before_send",
            invoice_number=fresh.invoice_number,
            status=str(fresh.status),
            paid=fresh.is_fully_paid,
        )
        report.held += 1
        return

    deliver_reminder(
        session,
        invoice=fresh,
        customer=customer,
        tier=tier,
        draft=draft,
        decision=decision,
    )
    report.sent += 1

    # Tier 3 both sends AND hands over. Doc §3: Vasooli does not escalate beyond this
    # on its own, so the final notice goes out and a human takes it from there.
    if tier == 3:
        escalate_to_human(session, fresh, "tier_3_reached")
        report.escalated += 1
