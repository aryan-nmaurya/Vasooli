"""Reminder delivery. Doc §3 Stage 3.

Phase 8 records reminders without sending them: `EMAIL_DRY_RUN` is the default and the
real providers arrive in Phase 7. The seam matters more than the implementation — the
recovery cycle calls `deliver`, and swapping dry-run for Resend changes nothing above
this function.

Dry-run is not a stub. It renders the message, persists the reminder with the policy
decision that approved it, and updates the cadence counters. Everything except the
network call is exercised, so turning sending on later is a configuration change
rather than a new code path appearing on demo day.
"""

from dataclasses import dataclass
from datetime import timedelta

from sqlmodel import Session, select

from app.ai.drafting import Draft
from app.core.clock import utcnow
from app.core.config import settings
from app.core.constants import TONE_FOR_TIER, InvoiceStatus
from app.core.logging import get_logger
from app.integrations.email.base import EmailProvider
from app.integrations.email.resend_client import ResendProvider
from app.models import AuditAction, AuditActor, AuditLog, Customer, Invoice, Reminder
from app.models.reminder import MAX_DELIVERY_ATTEMPTS
from app.policy import PolicyDecision

log = get_logger("messaging")

DRY_RUN_PROVIDER = "dry_run"


@dataclass(frozen=True)
class DeliveryResult:
    sent: bool
    provider: str
    message_id: str | None = None
    error: str | None = None


def resolve_recipient(customer_email: str) -> tuple[str, str | None]:
    """Where this message actually goes, and who it was meant for.

    The synthetic ledger has 52 invented domains, so unredirected live mail would
    bounce off all of them. More importantly, if a real address ever lands in the
    ledger, an unredirected send means a stranger receives a debt reminder. The
    redirect makes that impossible rather than unlikely.
    """
    if settings.email_redirect_to:
        return settings.email_redirect_to, customer_email
    return customer_email, None


def _subject_for(subject: str, intended_for: str | None) -> str:
    """Show the real recipient when mail has been redirected.

    Without this, a demo inbox fills with sixty identical-looking reminders and
    nobody can tell which customer each one was for.
    """
    return f"[→ {intended_for}] {subject}" if intended_for else subject


def render_html(body: str) -> str:
    """Plain text to a minimal HTML body.

    Deliberately unstyled. A collections reminder that looks like marketing gets
    filed as marketing, and the content is what matters here.
    """
    escaped = body.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    paragraphs = "".join(
        f"<p style='margin:0 0 14px'>{para.replace(chr(10), '<br>')}</p>"
        for para in escaped.split("\n\n")
        if para.strip()
    )
    return (
        '<div style="font-family:-apple-system,Segoe UI,Roboto,sans-serif;'
        'font-size:15px;line-height:1.55;color:#1a1a1a;max-width:560px">'
        f"{paragraphs}</div>"
    )


def _send_email(
    *,
    to: str,
    subject: str,
    body: str,
    invoice_number: str,
    provider: EmailProvider | None = None,
) -> DeliveryResult:
    """Hand the message to a provider, or record it in dry-run."""
    recipient, intended_for = resolve_recipient(to)
    final_subject = _subject_for(subject, intended_for)

    if settings.email_dry_run:
        log.info(
            "messaging.dry_run",
            to=recipient,
            intended_for=intended_for,
            invoice_number=invoice_number,
            subject=final_subject,
        )
        return DeliveryResult(sent=True, provider=DRY_RUN_PROVIDER, message_id="dry-run")

    settings.assert_safe_to_send()
    provider = provider or ResendProvider()
    result = provider.send(
        to=recipient,
        subject=final_subject,
        html=render_html(body),
        text=body,
        headers={"X-Vasooli-Invoice": invoice_number},
    )
    return DeliveryResult(
        sent=result.sent,
        provider=result.provider,
        message_id=result.message_id,
        error=result.error,
    )


def _backoff_seconds(attempt: int) -> int:
    """Bounded exponential backoff: 5m, 10m, 20m, 40m, capped at 2h.

    Bounded on purpose. An unbounded retry loop against a failing mail provider is a
    self-inflicted denial of service, and one that arrives with our own API key
    attached.
    """
    return min(300 * (2 ** max(0, attempt - 1)), 7200)


def _record_attempt(reminder: Reminder, result: DeliveryResult) -> None:
    """Fold one delivery outcome into the reminder row."""
    reminder.attempt_count += 1
    reminder.last_attempt_at = utcnow()

    if result.sent:
        reminder.sent_at = utcnow()
        reminder.send_error = None
        reminder.next_retry_at = None
    else:
        reminder.sent_at = None
        reminder.send_error = (result.error or "unknown")[:500]
        reminder.next_retry_at = (
            utcnow() + timedelta(seconds=_backoff_seconds(reminder.attempt_count))
            if reminder.attempt_count < MAX_DELIVERY_ATTEMPTS
            else None  # exhausted; a human decides what happens next
        )
    reminder.provider = result.provider
    reminder.provider_message_id = result.message_id


def _audit_attempt(
    session: Session,
    invoice: Invoice,
    reminder: Reminder,
    customer: Customer,
    result: DeliveryResult,
) -> None:
    session.add(
        AuditLog(
            invoice_id=invoice.id,
            actor=AuditActor.SYSTEM,
            action=AuditAction.REMINDER_SENT if result.sent else AuditAction.REMINDER_FAILED,
            detail={
                "tier": reminder.tier,
                "tone": str(reminder.tone),
                "subject": reminder.subject,
                "provider": result.provider,
                "generated_by": reminder.generated_by,
                "llm_degraded": reminder.llm_degraded,
                "to": customer.email,
                "attempt": reminder.attempt_count,
                "error": result.error,
                "next_retry_at": (
                    reminder.next_retry_at.isoformat() if reminder.next_retry_at else None
                ),
                "exhausted": (not result.sent and reminder.attempt_count >= MAX_DELIVERY_ATTEMPTS),
            },
        )
    )


def deliver_reminder(
    session: Session,
    *,
    invoice: Invoice,
    customer: Customer,
    tier: int,
    draft: Draft,
    decision: PolicyDecision,
    provider: EmailProvider | None = None,
) -> Reminder:
    """Attempt one reminder, recording the outcome either way.

    The row is written whether or not delivery succeeds, because an attempt that
    failed is information a human needs. What the row does NOT do on failure is count
    as a sent reminder: `sent_at` stays NULL, the invoice's cadence counters are
    untouched, and the tier remains owed. Before that distinction existed, a bounced
    email silently consumed the tier and the invoice was never chased again.
    """
    result = _send_email(
        to=customer.email,
        subject=draft.subject,
        body=draft.body,
        invoice_number=invoice.invoice_number,
        provider=provider,
    )

    reminder = Reminder(
        invoice_id=invoice.id,
        tier=tier,
        tone=TONE_FOR_TIER[tier],
        subject=draft.subject,
        body=draft.body,
        channel="email",
        policy_decision=decision.to_dict(),
        generated_by=draft.generated_by,
        llm_degraded=draft.degraded,
    )
    _record_attempt(reminder, result)
    session.add(reminder)

    if result.sent:
        # Cadence counters advance only on a delivery a provider accepted. They are
        # denormalized onto the invoice because app.policy is pure and cannot COUNT(*).
        invoice.reminders_sent += 1
        invoice.current_tier = tier
        invoice.last_reminder_at = utcnow()
        session.add(invoice)

    _audit_attempt(session, invoice, reminder, customer, result)
    return reminder


def retry_failed_deliveries(
    session: Session,
    *,
    provider: EmailProvider | None = None,
    limit: int = 50,
) -> dict[str, int]:
    """Re-attempt reminders whose delivery failed and whose backoff has elapsed.

    Retries the SAME row rather than drafting a new message: the customer is owed the
    message that was already approved by policy, and re-drafting would re-run the LLM
    and produce different copy for the same tier.

    Cooldown is not re-checked here, and does not need to be. `last_reminder_at`
    advances only on a successful send, so a retry of a never-delivered message cannot
    contact anyone twice inside the cooldown window.
    """
    now = utcnow()
    due = session.exec(
        select(Reminder)
        .where(
            Reminder.sent_at.is_(None),  # type: ignore[union-attr]
            Reminder.attempt_count < MAX_DELIVERY_ATTEMPTS,
            Reminder.next_retry_at.is_not(None),  # type: ignore[union-attr]
            Reminder.next_retry_at <= now,  # type: ignore[operator]
        )
        .limit(limit)
    ).all()

    recovered = 0
    still_failing = 0

    for reminder in due:
        invoice = session.get(Invoice, reminder.invoice_id)
        if invoice is None:
            continue

        # An invoice settled or handed over since the attempt no longer needs chasing.
        if invoice.status in (InvoiceStatus.RECOVERED, InvoiceStatus.WRITTEN_OFF):
            reminder.next_retry_at = None
            reminder.send_error = "abandoned: invoice no longer being chased"
            session.add(reminder)
            continue

        customer = session.get(Customer, invoice.customer_id)
        if customer is None:
            continue

        result = _send_email(
            to=customer.email,
            subject=reminder.subject,
            body=reminder.body,
            invoice_number=invoice.invoice_number,
            provider=provider,
        )
        _record_attempt(reminder, result)
        session.add(reminder)

        if result.sent:
            invoice.reminders_sent += 1
            invoice.current_tier = max(invoice.current_tier, reminder.tier)
            invoice.last_reminder_at = utcnow()
            session.add(invoice)
            recovered += 1
        else:
            still_failing += 1

        _audit_attempt(session, invoice, reminder, customer, result)

    session.commit()
    if due:
        log.info(
            "messaging.retry_complete",
            attempted=len(due),
            recovered=recovered,
            still_failing=still_failing,
        )
    return {"attempted": len(due), "recovered": recovered, "still_failing": still_failing}
