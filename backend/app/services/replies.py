"""Handle a customer's reply. Doc §3 Stage 4.

Turns an inbound message into one of three outcomes: a promise that pauses escalation,
a dispute that pauses recovery and opens a human-review case, or nothing. The reading
itself is done by app.ai; everything here is the deterministic part — validating what
came back and deciding what it means for the invoice.

A reply is untrusted input. Nothing in this module lets it set an invoice's status
directly: a promise pauses the chase, and a dispute goes to app.policy.disputes, which
decides whether recovery stops. Neither can mark an invoice paid, because only a
verified Razorpay payment does that.
"""

from dataclasses import dataclass, replace
from datetime import timedelta

from sqlmodel import Session, select

from app.ai.dispute_analysis import analyse_dispute
from app.ai.promise_extraction import extract_promise
from app.core.clock import today_ist, utcnow
from app.core.constants import InvoiceStatus, PromiseStatus
from app.core.logging import get_logger
from app.models import (
    AuditAction,
    AuditActor,
    AuditLog,
    Customer,
    InboundMessage,
    Invoice,
    Promise,
)
from app.models.inbound_message import MAX_INBOUND_ATTEMPTS
from app.services.ai_audit import AITask, record_ai_outcome
from app.services.disputes import record_dispute

log = get_logger("replies")

#: Lines that begin quoted history in the replies mail clients actually produce.
_QUOTE_MARKERS = (
    "-----original message-----",
    "on wrote:",
    "from:",
    "sent from my",
    "________________________________",
)


def strip_quoted_text(body: str) -> str:
    """Remove the quoted copy of our own reminder from a reply.

    Without this, our Tier 2 message is re-read as though the customer wrote it — and
    since our own copy says things like "confirm a pay-by date", the extractor happily
    finds a promise the customer never made.
    """
    lines = body.splitlines()
    kept: list[str] = []
    for line in lines:
        stripped = line.strip().lower()
        if stripped.startswith(">"):
            break
        if any(stripped.startswith(m) for m in _QUOTE_MARKERS):
            break
        if stripped.startswith("on ") and stripped.endswith("wrote:"):
            break
        kept.append(line)
    return "\n".join(kept).strip() or body.strip()


@dataclass(frozen=True)
class ReplyOutcome:
    invoice_number: str
    promise_created: bool = False
    escalated: bool = False
    is_complaint: bool = False
    promised_date: str | None = None
    confidence: float = 0.0
    note: str = ""
    #: Set when this reply opened or matched a dispute case, so the caller can link
    #: straight to it. None on every non-dispute reply, which is most of them.
    dispute_case_id: str | None = None


def handle_reply(
    session: Session,
    invoice: Invoice,
    raw_body: str,
    *,
    use_llm: bool = True,
    inbound_message_id: str | None = None,
    count_as_new_reply: bool = True,
) -> ReplyOutcome:
    """Record a reply and act on what it says.

    `count_as_new_reply` is False only on a reprocess where the reply history was
    already written by an earlier attempt that failed further down. Without it, a
    message that failed after its mid-way commit would add a second reply to the
    invoice's history every time it was retried, and `reply_count` drives whether a
    customer is classified "unresponsive".
    """
    body = strip_quoted_text(raw_body)

    # Record the reply on the invoice BEFORE deciding what it means.
    #
    # Diagnosis reads this on every later cycle. "Unresponsive" is defined as no reply
    # after Tier 2, so a customer who answered — even vaguely — must never be
    # classified that way. Before this existed, `has_reply` was hardcoded False and
    # every replying customer eventually became "unresponsive".
    if count_as_new_reply:
        invoice.reply_count += 1
        invoice.last_reply_at = utcnow()
        invoice.last_reply_excerpt = body[:400]
        session.add(invoice)

    session.add(
        AuditLog(
            invoice_id=invoice.id,
            actor=AuditActor.SYSTEM,
            action=AuditAction.REPLY_RECEIVED,
            detail={
                "excerpt": body[:400],
                "raw_length": len(raw_body),
                "reply_number": invoice.reply_count,
                "inbound_message_id": inbound_message_id,
            },
        )
    )

    extraction = extract_promise(
        body,
        today=today_ist(),
        invoice_number=invoice.invoice_number,
        outstanding_paise=invoice.outstanding_paise,
        use_llm=use_llm,
    )

    if use_llm:
        fell_back = extraction.source == "rule_based"
        record_ai_outcome(
            session,
            invoice_id=invoice.id,
            task=AITask.EXTRACT_PROMISE,
            model=None if fell_back else extraction.source,
            models_attempted=() if fell_back else (extraction.source,),
            accepted=not fell_back,
            used_fallback=fell_back,
            reason=(
                "no model answered; regex extraction used"
                if fell_back
                # A promise the model found but policy will not act on. Worth
                # recording separately from one it never found.
                else (
                    "promise found but below the confidence or horizon threshold"
                    if extraction.has_promise and extraction.below_threshold
                    else None
                )
            ),
        )

    # A complaint is not a payment negotiation. Doc §3 Stage 2 routes it to a human,
    # and an automated nudge on a disputed invoice escalates a disagreement rather
    # than resolving it.
    if extraction.is_complaint:
        # A second structured question, not a second system. Same client, same model
        # list, same failover, same timeout — asked only on the branch that already
        # concluded this is a complaint, so a normal reply and a promise reply still
        # cost exactly one model call and behave exactly as they did before.
        analysis = analyse_dispute(
            body,
            invoice_number=invoice.invoice_number,
            outstanding_paise=invoice.outstanding_paise,
            use_llm=use_llm,
        )

        if use_llm:
            record_ai_outcome(
                session,
                invoice_id=invoice.id,
                task=AITask.ANALYSE_DISPUTE,
                model=None if analysis.used_fallback else analysis.source,
                models_attempted=analysis.models_attempted,
                accepted=not analysis.used_fallback,
                used_fallback=analysis.used_fallback,
                reason=(
                    "no model answered; the customer's message is shown unedited"
                    if analysis.used_fallback
                    else None
                ),
                error=analysis.error,
            )

        # The extractor and the analyser disagree only rarely, and when they do the
        # extractor wins: it is the detector this system has always used, its
        # behaviour is what the existing tests pin, and a complaint that stops being
        # a complaint because a second call was less sure is a regression in safety.
        # The analyser's job is to describe, and a description is still owed here.
        if not analysis.is_dispute:
            analysis = replace(
                analysis,
                is_dispute=True,
                reason=analysis.reason or "Customer raised an objection in their reply",
                summary=analysis.summary
                or (
                    "The reply was read as a complaint, though the follow-up analysis "
                    "was not sure what is being disputed. The message is shown unedited "
                    "below."
                ),
            )

        case = record_dispute(session, invoice, analysis, reply_body=body)
        session.commit()

        log.info("replies.complaint", invoice_number=invoice.invoice_number)
        return ReplyOutcome(
            invoice_number=invoice.invoice_number,
            escalated=True,
            is_complaint=True,
            confidence=analysis.confidence,
            note="Dispute detected — recovery paused and a review case opened.",
            dispute_case_id=str(case.id) if case else None,
        )

    if not extraction.should_pause_escalation:
        session.commit()
        note = (
            "Possible promise, but too weak or too far out to pause on."
            if extraction.has_promise
            else "No commitment found in the reply."
        )
        return ReplyOutcome(
            invoice_number=invoice.invoice_number,
            confidence=extraction.confidence,
            note=note,
        )

    # Replace any existing active promise rather than adding a second one: the partial
    # unique index permits only one, and the newest commitment is the one in force.
    existing = session.exec(
        select(Promise).where(
            Promise.invoice_id == invoice.id, Promise.status == PromiseStatus.ACTIVE
        )
    ).first()
    if existing is not None:
        existing.status = PromiseStatus.BROKEN
        existing.resolved_at = utcnow()
        session.add(existing)
        session.flush()

    promise = Promise(
        invoice_id=invoice.id,
        promised_date=extraction.promised_date,  # type: ignore[arg-type]
        promised_amount_paise=extraction.promised_amount_paise,
        source_message_excerpt=extraction.excerpt[:300] or body[:300],
        extraction_confidence=extraction.confidence,
        status=PromiseStatus.ACTIVE,
        # Resume point if this promise is broken. Doc §3 Stage 4: escalation returns
        # to the tone it left off at, never reset to polite.
        tier_at_pause=invoice.current_tier,
    )
    session.add(promise)

    # A promise does NOT pull an invoice back out of human review.
    #
    # Contradictory replies happen: a customer disputes an invoice and then, later,
    # offers to pay. Both are recorded — the promise is real information for whoever
    # is handling the dispute — but only a person decides the dispute is settled. A
    # keyword match on a later message must not quietly restart automated chasing on a
    # bill the customer has contested.
    if invoice.status != InvoiceStatus.HUMAN_REVIEW:
        invoice.status = InvoiceStatus.PROMISE_ACTIVE
    session.add(invoice)

    session.add(
        AuditLog(
            invoice_id=invoice.id,
            actor=AuditActor.AI,
            action=AuditAction.PROMISE_LOGGED,
            detail={
                "promised_date": str(extraction.promised_date),
                "confidence": extraction.confidence,
                "tier_at_pause": invoice.current_tier,
                "invoice_status": str(invoice.status),
                "excerpt": extraction.excerpt[:300],
                "source": extraction.source,
            },
        )
    )
    session.commit()

    log.info(
        "replies.promise_logged",
        invoice_number=invoice.invoice_number,
        promised_date=str(extraction.promised_date),
        tier_at_pause=invoice.current_tier,
    )
    return ReplyOutcome(
        invoice_number=invoice.invoice_number,
        promise_created=True,
        promised_date=str(extraction.promised_date),
        confidence=extraction.confidence,
        note="Promise logged — escalation paused.",
    )


def find_invoice_for_reply(session: Session, *, invoice_number: str) -> Invoice | None:
    return session.exec(select(Invoice).where(Invoice.invoice_number == invoice_number)).first()


def find_customer(session: Session, invoice: Invoice) -> Customer | None:
    return session.get(Customer, invoice.customer_id)


# ---------------------------------------------------------------------------
# Reprocessing failed inbound messages.
#
# The webhook answers 200 the moment a message is stored — that is deliberate and it
# is what stops the provider redelivering. The consequence is that if `handle_reply`
# raises afterwards, the provider considers the message delivered and nothing on their
# side will ever send it again. Before this, that was the end of the road: the failure
# existed only as a string in a column nobody queried, and a customer's "we paid this
# on the 14th" was silently dropped while the reminders carried on.
#
# So the retry has to be ours. Same shape as the delivery, closure, and webhook
# retries, swept by the same job, with the same bounded backoff and the same escape
# hatch of an operator button once the automatic attempts are spent.
# ---------------------------------------------------------------------------


def _inbound_backoff(attempt: int) -> timedelta:
    """Bounded: 1m, 2m, 4m, 8m, capped at 15m."""
    return timedelta(seconds=min(60 * (2 ** max(0, attempt - 1)), 900))


def mark_inbound_failed(session: Session, message: InboundMessage, error: str) -> None:
    """Record a processing failure and schedule the next attempt."""
    message.processing_attempts += 1
    message.last_attempt_at = utcnow()
    message.processing_error = f"{error}"[:500]
    message.next_retry_at = (
        utcnow() + _inbound_backoff(message.processing_attempts)
        if message.processing_attempts < MAX_INBOUND_ATTEMPTS
        else None  # exhausted: an operator has to read it
    )
    session.add(message)
    session.add(
        AuditLog(
            invoice_id=message.invoice_id,
            actor=AuditActor.SYSTEM,
            action=AuditAction.INBOUND_PROCESSING_FAILED,
            detail={
                "inbound_message_id": str(message.id),
                "sender": message.sender,
                "error": message.processing_error,
                "attempts": message.processing_attempts,
                "next_retry_at": (
                    message.next_retry_at.isoformat() if message.next_retry_at else None
                ),
                "exhausted": message.processing_attempts >= MAX_INBOUND_ATTEMPTS,
            },
        )
    )
    session.commit()
    log.warning(
        "replies.processing_failed",
        inbound_message_id=str(message.id),
        attempts=message.processing_attempts,
        error=message.processing_error,
    )


def reprocess_inbound(session: Session, message: InboundMessage) -> bool:
    """Re-run reply handling for one stored message. Returns True on success.

    Safe to call repeatedly. `handle_reply` is idempotent on the paths that matter: a
    dispute already open is recorded as a replay rather than opened twice, and a
    promise is written against the invoice's current state rather than accumulated.
    """
    if message.processed_at is not None:
        return True

    invoice = session.get(Invoice, message.invoice_id)
    if invoice is None:
        # Nothing to reprocess against. Terminal rather than retried forever.
        message.processing_attempts = MAX_INBOUND_ATTEMPTS
        message.processing_error = "invoice missing"
        message.next_retry_at = None
        session.add(message)
        session.commit()
        return False

    # An earlier attempt may have committed the reply history before failing further
    # down — handle_reply commits mid-way on the dispute branch. Counting it again
    # would inflate reply_count, which decides whether a customer is "unresponsive".
    already_recorded = bool(
        session.exec(
            select(AuditLog).where(
                AuditLog.invoice_id == invoice.id,
                AuditLog.action == AuditAction.REPLY_RECEIVED,
                AuditLog.detail["inbound_message_id"].astext == str(message.id),  # type: ignore[index]
            )
        ).first()
    )

    try:
        handle_reply(
            session,
            invoice,
            message.body_text[:20_000],
            inbound_message_id=str(message.id),
            count_as_new_reply=not already_recorded,
        )
    except Exception as exc:  # noqa: BLE001
        session.rollback()
        session.refresh(message)
        mark_inbound_failed(session, message, f"{type(exc).__name__}: {exc}")
        return False

    message.processed_at = utcnow()
    message.processing_error = None
    message.next_retry_at = None
    message.last_attempt_at = utcnow()
    message.processing_attempts += 1
    session.add(message)
    session.commit()
    log.info("replies.reprocessed", inbound_message_id=str(message.id))
    return True


def retry_failed_inbound(
    session: Session, *, limit: int = 50, force_ids: list[str] | None = None
) -> dict[str, int]:
    """Reprocess inbound messages whose backoff has elapsed.

    `force_ids` bypasses the backoff for an operator pressing "reprocess" on a specific
    message — including exhausted ones, which is the entire point of a manual retry.
    """
    if force_ids:
        due = list(
            session.exec(
                select(InboundMessage).where(
                    InboundMessage.id.in_(force_ids),  # type: ignore[attr-defined]
                    InboundMessage.processed_at.is_(None),  # type: ignore[union-attr]
                )
            ).all()
        )
    else:
        now = utcnow()
        due = list(
            session.exec(
                select(InboundMessage)
                .where(
                    InboundMessage.processed_at.is_(None),  # type: ignore[union-attr]
                    InboundMessage.processing_attempts < MAX_INBOUND_ATTEMPTS,
                    InboundMessage.next_retry_at.is_not(None),  # type: ignore[union-attr]
                    InboundMessage.next_retry_at <= now,  # type: ignore[operator]
                )
                .limit(limit)
            ).all()
        )

    recovered = sum(1 for message in due if reprocess_inbound(session, message))
    if due:
        log.info("replies.retry_complete", attempted=len(due), recovered=recovered)
    return {"attempted": len(due), "recovered": recovered}
