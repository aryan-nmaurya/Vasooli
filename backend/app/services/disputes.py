"""Open, pause on, and close a dispute. Customer Conversation Safety.

The chain this module completes, and the order it must stay in:

    app.ai.dispute_analysis   understands the customer's message
    app.policy.disputes       decides whether that pauses recovery
    this module               writes the decision down
    Razorpay                  remains the only source of payment truth

Nothing here writes `amount_paid_paise`, sets an invoice RECOVERED, or touches a
payment link. A dispute changes whether Vasooli speaks to a customer; it never
changes what they owe. If a payment arrives mid-dispute the reconciler handles it on
its own path and this module only records that it happened.
"""

import hashlib
import uuid

from sqlmodel import Session, select

from app.ai.dispute_analysis import DisputeAnalysis
from app.core.clock import utcnow
from app.core.constants import DisputeStatus, InvoiceStatus, PromiseStatus, ReasonCategory
from app.core.logging import get_logger
from app.models import AuditAction, AuditActor, AuditLog, DisputeCase, Invoice, Promise
from app.policy import DisputeAction, decide_dispute_action, decide_resume

log = get_logger("disputes")

#: Written as the invoice's escalation_reason when a dispute pauses recovery. Kept
#: identical to the value the pre-existing complaint branch already used, so
#: app.services.explain, the dashboard copy and every existing test keep reading the
#: same string. A new feature is not a reason to rename an established one.
DISPUTE_ESCALATION_REASON = "complaint_in_reply"


def fingerprint(reply_body: str) -> str:
    """Stable identity for a reply's text.

    Whitespace-normalised and lowercased before hashing: the same message pasted
    twice into the simulate-reply box, or redelivered by a mail provider, differs by
    a trailing newline often enough that a raw hash would miss the duplicate.
    """
    normalised = " ".join(reply_body.split()).lower()
    return hashlib.sha256(normalised.encode()).hexdigest()[:32]


def open_case_for(session: Session, invoice_id: uuid.UUID) -> DisputeCase | None:
    """The open dispute case for an invoice, if there is one."""
    return session.exec(
        select(DisputeCase).where(
            DisputeCase.invoice_id == invoice_id,
            DisputeCase.status == DisputeStatus.OPEN,
        )
    ).first()


def has_open_dispute(session: Session, invoice_id: uuid.UUID) -> bool:
    return open_case_for(session, invoice_id) is not None


def cases_for(session: Session, invoice_id: uuid.UUID) -> list[DisputeCase]:
    return list(
        session.exec(
            select(DisputeCase)
            .where(DisputeCase.invoice_id == invoice_id)
            .order_by(DisputeCase.opened_at)  # type: ignore[arg-type]
        ).all()
    )


def record_dispute(
    session: Session,
    invoice: Invoice,
    analysis: DisputeAnalysis,
    *,
    reply_body: str,
) -> DisputeCase | None:
    """Act on a dispute signal. Returns the open case, or None if nothing was opened.

    Does NOT commit — the caller owns the transaction, so the reply, the audit trail
    and the pause land together or not at all.

    Idempotent in the way that matters. Replaying the same message finds the case its
    first delivery opened and writes one `dispute_already_open` row instead of a
    second case, a second pause and a second escalation. The partial unique index on
    `dispute_cases` backs this up at the database level, so two workers racing on the
    same reply cannot both win.
    """
    decision = decide_dispute_action(
        is_dispute=analysis.is_dispute,
        status=invoice.status,
        case_already_open=has_open_dispute(session, invoice.id),
    )

    # The AI's reading is recorded whenever it found a dispute, even where policy
    # then declines to act. An observation that led nowhere is still evidence of what
    # the system understood, and its absence would make the trail look like the
    # message was never read.
    if analysis.is_dispute:
        session.add(
            AuditLog(
                invoice_id=invoice.id,
                actor=AuditActor.AI,
                action=AuditAction.DISPUTE_DETECTED,
                detail={
                    "reason": analysis.reason,
                    "summary": analysis.summary,
                    "facts": list(analysis.facts),
                    "confidence": analysis.confidence,
                    "model": analysis.source,
                    "degraded": analysis.degraded,
                    "deterministic_fallback": analysis.used_fallback,
                    "policy_action": str(decision.action),
                },
            )
        )

    if decision.action is DisputeAction.ALREADY_PAUSED:
        existing = open_case_for(session, invoice.id)
        session.add(
            AuditLog(
                invoice_id=invoice.id,
                actor=AuditActor.POLICY,
                action=AuditAction.DISPUTE_ALREADY_OPEN,
                detail={
                    "case_id": str(existing.id) if existing else None,
                    "repeat_of_same_message": bool(
                        existing and existing.source_fingerprint == fingerprint(reply_body)
                    ),
                    "reason": decision.reason,
                },
            )
        )
        return existing

    if not decision.pauses_recovery:
        log.info(
            "disputes.no_pause",
            invoice_number=invoice.invoice_number,
            action=str(decision.action),
        )
        return None

    case = DisputeCase(
        invoice_id=invoice.id,
        status=DisputeStatus.OPEN,
        reason=analysis.reason[:120] or "Customer raised an objection",
        summary=analysis.summary[:400],
        facts=list(analysis.facts),
        confidence=analysis.confidence,
        source_excerpt=reply_body[:300],
        source_fingerprint=fingerprint(reply_body),
        source_reply_number=invoice.reply_count,
        detected_by=analysis.source,
        ai_degraded=analysis.degraded,
    )
    session.add(case)
    session.flush()

    # --- The pause itself ----------------------------------------------------
    # Attributed to POLICY, not AI. The model said what the message meant; this
    # status change is the policy engine's decision, and the audit trail has to
    # show it that way or the architecture claim is only a comment in a file.
    invoice.reason_category = ReasonCategory.DISPUTE_LIKELY
    invoice.reason_explanation = "The customer disputes this invoice in their reply."
    invoice.reason_diagnosed_at = utcnow()
    invoice.status = InvoiceStatus.HUMAN_REVIEW
    invoice.escalated_to_human_at = invoice.escalated_to_human_at or utcnow()
    invoice.escalation_reason = DISPUTE_ESCALATION_REASON
    session.add(invoice)

    session.add(
        AuditLog(
            invoice_id=invoice.id,
            actor=AuditActor.POLICY,
            action=AuditAction.RECOVERY_PAUSED,
            detail={
                "reason": decision.reason,
                "case_id": str(case.id),
                "tier_at_pause": invoice.current_tier,
                "confidence": analysis.confidence,
            },
        )
    )
    session.add(
        AuditLog(
            invoice_id=invoice.id,
            actor=AuditActor.POLICY,
            action=AuditAction.DISPUTE_CASE_OPENED,
            detail={
                "case_id": str(case.id),
                "reason": case.reason,
                "fact_count": len(case.facts),
                "detected_by": case.detected_by,
            },
        )
    )
    # Kept for continuity: every previous complaint wrote this action, the dashboard
    # summarises it, and app.services.explain reads escalation_reason from it.
    session.add(
        AuditLog(
            invoice_id=invoice.id,
            actor=AuditActor.POLICY,
            action=AuditAction.ESCALATED_TO_HUMAN,
            detail={
                "reason": DISPUTE_ESCALATION_REASON,
                "case_id": str(case.id),
                "excerpt": reply_body[:300],
            },
        )
    )

    log.info(
        "disputes.paused",
        invoice_number=invoice.invoice_number,
        case_id=str(case.id),
        detected_by=case.detected_by,
        confidence=case.confidence,
    )
    return case


def resolve_dispute(
    session: Session,
    case: DisputeCase,
    *,
    resolved_by: str,
    note: str = "",
    resume_recovery: bool = False,
) -> tuple[DisputeCase, bool]:
    """Close a dispute case. Returns the case and whether recovery restarted.

    Resolving and resuming are two decisions, not one. A merchant who agrees the
    customer was right closes the case and does NOT want the cadence to restart; one
    who checked the delivery note and found the invoice correct closes it and does.
    Collapsing them into a single button would make the safe choice the harder one.
    """
    if not case.is_open:
        return case, False

    invoice = session.get(Invoice, case.invoice_id)
    if invoice is None:  # pragma: no cover - foreign key makes this unreachable
        raise ValueError(f"dispute case {case.id} has no invoice")

    case.status = DisputeStatus.RESOLVED
    case.resolved_at = utcnow()
    case.resolved_by = resolved_by
    case.resolution_note = note[:1000]
    session.add(case)

    session.add(
        AuditLog(
            invoice_id=invoice.id,
            actor=resolved_by,
            action=AuditAction.DISPUTE_RESOLVED,
            detail={
                "case_id": str(case.id),
                "note": note[:500],
                "resume_requested": resume_recovery,
                "days_open": (case.resolved_at - case.opened_at).days,
            },
        )
    )

    resumed = False
    if resume_recovery:
        decision = decide_resume(case_is_open=False, status=invoice.status)
        if decision.action is DisputeAction.NO_ACTION:
            # Clear the dispute diagnosis rather than keep it and special-case it.
            # `not_dispute_likely` reads reason_category, so an invoice resumed with
            # DISPUTE_LIKELY still set would be re-escalated by the very next cycle
            # and the resume button would appear to do nothing. A null category means
            # the next cycle diagnoses it afresh, which is the honest state: what this
            # customer's problem is has just changed.
            invoice.reason_category = None
            invoice.reason_explanation = None
            invoice.reason_confidence = None
            invoice.escalation_reason = None
            invoice.escalated_to_human_at = None
            invoice.status = _status_after_resume(session, invoice)
            case.recovery_resumed_at = utcnow()
            session.add(invoice)
            session.add(case)
            resumed = True

        session.add(
            AuditLog(
                invoice_id=invoice.id,
                actor=resolved_by,
                action=AuditAction.RECOVERY_RESUMED,
                detail={
                    "case_id": str(case.id),
                    "resumed": resumed,
                    "reason": decision.reason,
                    "resumed_at_status": str(invoice.status),
                    "tier": invoice.current_tier,
                },
            )
        )

    log.info(
        "disputes.resolved",
        invoice_number=invoice.invoice_number,
        case_id=str(case.id),
        resumed=resumed,
    )
    return case, resumed


def _status_after_resume(session: Session, invoice: Invoice) -> InvoiceStatus:
    """Where an invoice lands when a dispute is lifted.

    Not always CHASING. A customer who disputed an invoice and later promised a date
    has both on record; lifting the dispute should hand the invoice back to the
    promise that is still in force, not restart chasing on top of it.
    """
    if invoice.amount_paid_paise > 0:
        return InvoiceStatus.PARTIALLY_PAID

    promise = session.exec(
        select(Promise).where(
            Promise.invoice_id == invoice.id,
            Promise.status == PromiseStatus.ACTIVE,
        )
    ).first()
    if promise is not None:
        return InvoiceStatus.PROMISE_ACTIVE

    return InvoiceStatus.CHASING
