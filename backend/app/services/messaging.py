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


def _send_email(*, to: str, subject: str, body: str, invoice_number: str) -> DeliveryResult:
    """Hand the message to an email provider.

    Phase 7 replaces this with Resend, falling back to SendGrid. Until then every
    message is recorded and nothing leaves the building — which is the correct default
    while the customer list is synthetic.
    """
    if settings.email_dry_run:
        log.info("messaging.dry_run", to=to, invoice_number=invoice_number, subject=subject)
        return DeliveryResult(sent=True, provider=DRY_RUN_PROVIDER, message_id="dry-run")

    raise NotImplementedError(
        "Live email delivery lands in Phase 7. Set EMAIL_DRY_RUN=true until then."
    )


def deliver_reminder(
    session: Session,
    *,
    invoice: Invoice,
    customer: Customer,
    tier: int,
    draft: Draft,
    decision: PolicyDecision,
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
