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

from sqlmodel import Session

from app.ai.drafting import Draft
from app.core.clock import utcnow
from app.core.config import settings
from app.core.constants import TONE_FOR_TIER
from app.core.logging import get_logger
from app.integrations.email.base import EmailProvider
from app.integrations.email.resend_client import ResendProvider
from app.models import AuditAction, AuditActor, AuditLog, Customer, Invoice, Reminder
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
    """Record and send one approved reminder, then advance the cadence counters.

    The counters are denormalized onto the invoice because app.policy is pure and
    cannot run a COUNT(*). They are updated here, in the same transaction as the
    reminder row, so a crash between the two is not possible.
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
        provider=result.provider,
        provider_message_id=result.message_id,
        policy_decision=decision.to_dict(),
        generated_by=draft.generated_by,
        llm_degraded=draft.degraded,
        sent_at=utcnow() if result.sent else None,
        send_error=result.error,
    )
    session.add(reminder)

    if result.sent:
        invoice.reminders_sent += 1
        invoice.current_tier = tier
        invoice.last_reminder_at = utcnow()
        session.add(invoice)

    session.add(
        AuditLog(
            invoice_id=invoice.id,
            actor=AuditActor.SYSTEM,
            action=AuditAction.REMINDER_SENT if result.sent else AuditAction.REMINDER_FAILED,
            detail={
                "tier": tier,
                "tone": TONE_FOR_TIER[tier].value,
                "subject": draft.subject,
                "provider": result.provider,
                "generated_by": draft.generated_by,
                "llm_degraded": draft.degraded,
                "to": customer.email,
                "error": result.error,
            },
        )
    )
    return reminder
