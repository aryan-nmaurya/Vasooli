"""Handle a customer's reply. Doc §3 Stage 4.

Turns an inbound message into one of three outcomes: a promise that pauses escalation,
a complaint that routes to a human, or nothing. The extraction itself is done by
app.ai; everything here is the deterministic part — validating what came back and
deciding what it means for the invoice.

A reply is untrusted input. Nothing in this module lets it set an invoice's status
directly: a promise pauses the chase, and a complaint escalates to a person. Neither
can mark an invoice paid, because only a signed Razorpay webhook does that.
"""

from dataclasses import dataclass

from sqlmodel import Session, select

from app.ai.promise_extraction import extract_promise
from app.core.clock import today_ist, utcnow
from app.core.constants import InvoiceStatus, PromiseStatus, ReasonCategory
from app.core.logging import get_logger
from app.models import (
    AuditAction,
    AuditActor,
    AuditLog,
    Customer,
    Invoice,
    Promise,
)

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


def handle_reply(
    session: Session,
    invoice: Invoice,
    raw_body: str,
    *,
    use_llm: bool = True,
) -> ReplyOutcome:
    """Record a reply and act on what it says."""
    body = strip_quoted_text(raw_body)

    # Record the reply on the invoice BEFORE deciding what it means.
    #
    # Diagnosis reads this on every later cycle. "Unresponsive" is defined as no reply
    # after Tier 2, so a customer who answered — even vaguely — must never be
    # classified that way. Before this existed, `has_reply` was hardcoded False and
    # every replying customer eventually became "unresponsive".
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

    # A complaint is not a payment negotiation. Doc §3 Stage 2 routes it to a human,
    # and an automated nudge on a disputed invoice escalates a disagreement rather
    # than resolving it.
    if extraction.is_complaint:
        invoice.reason_category = ReasonCategory.DISPUTE_LIKELY
        invoice.reason_explanation = "The customer disputes this invoice in their reply."
        invoice.reason_diagnosed_at = utcnow()
        invoice.status = InvoiceStatus.HUMAN_REVIEW
        invoice.escalated_to_human_at = invoice.escalated_to_human_at or utcnow()
        invoice.escalation_reason = "complaint_in_reply"
        session.add(invoice)
        session.add(
            AuditLog(
                invoice_id=invoice.id,
                actor=AuditActor.AI,
                action=AuditAction.ESCALATED_TO_HUMAN,
                detail={"reason": "complaint_in_reply", "excerpt": extraction.excerpt[:300]},
            )
        )
        session.commit()
        log.info("replies.complaint", invoice_number=invoice.invoice_number)
        return ReplyOutcome(
            invoice_number=invoice.invoice_number,
            escalated=True,
            is_complaint=True,
            note="Complaint detected — routed to human review.",
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
