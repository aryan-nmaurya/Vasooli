"""Reminder delivery. Doc §3 Stage 3.

The service records reminders without sending them when `EMAIL_DRY_RUN` is enabled; the
real providers share the same seam. The seam matters more than the implementation — the
recovery cycle calls `deliver`, and swapping dry-run for Resend changes nothing above
this function.

Dry-run is not a stub. It renders the message, persists the reminder with the policy
decision that approved it, and updates the cadence counters. Everything except the
network call is exercised, so turning sending on later is a configuration change
rather than a new code path appearing on demo day.
"""

import re
import uuid
from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import or_
from sqlmodel import Session, select

from app.ai.drafting import Draft
from app.core.clock import utcnow
from app.core.config import settings
from app.core.constants import TONE_FOR_TIER, InvoiceStatus
from app.core.logging import get_logger
from app.core.runtime import effective_email_redirect
from app.integrations.email.base import EmailProvider
from app.integrations.email.resend_client import ResendProvider
from app.models import (
    AuditAction,
    AuditActor,
    AuditLog,
    Customer,
    DemoSettings,
    Invoice,
    Merchant,
    Reminder,
    SendingDomain,
)
from app.models.demo_settings import SINGLETON_ID
from app.models.reminder import MAX_DELIVERY_ATTEMPTS
from app.policy import PolicyDecision
from app.services.disputes import has_open_dispute
from app.services.outbound_controls import (
    OutboundBlockedError,
    assert_can_send,
    claim_send_slot,
    is_suppressed,
)

log = get_logger("messaging")

DRY_RUN_PROVIDER = "dry_run"
DELIVERY_LEASE_SECONDS = 120


@dataclass(frozen=True)
class DeliveryResult:
    sent: bool
    provider: str
    message_id: str | None = None
    error: str | None = None
    retryable: bool = False
    #: Where the message was actually addressed, when that is not the customer. The
    #: audit trail recorded `to: customer.email` unconditionally, which is false for
    #: every redirected send — the row claimed the customer had been written to when
    #: the message went to an operator inbox instead.
    redirected_to: str | None = None


def resolve_recipient(
    customer_email: str,
    *,
    is_demo: bool = True,
    redirect_override: str | None = None,
) -> tuple[str, str | None]:
    """Where this message actually goes, and who it was meant for.

    **Demo.** Always redirected. The seeded ledger has 52 invented domains, so
    unredirected mail would bounce off all of them, and if a real address ever lands in
    that ledger an unredirected send means a stranger receives a debt reminder. The
    redirect makes that impossible rather than unlikely, and no runtime setting can
    turn it off — `effective_email_redirect` can move the destination but never remove
    it.

    **Live.** Not redirected, once the deployment has explicitly opted in. This
    distinction did not exist: the redirect was applied globally, so a live merchant's
    reminder to their own overdue customer was delivered to the demo operator's inbox
    instead. The customer never received it, the reminder was recorded as sent, and the
    cadence advanced to the next tier — the product's central function, silently
    inoperative, in exactly the configuration the deployment runs.

    The opt-in is deliberate rather than a default. `ALLOW_DIRECT_CUSTOMER_EMAIL` is
    what turns a workspace that can be registered by anyone into one that can send mail
    to third parties, and that should be a decision someone makes, not a side effect of
    a merchant signing up. Until it is set, live mail is still redirected — but the
    reminder records `redirected_to` so the trail says the customer was not reached,
    rather than implying they were.
    """
    if not is_demo:
        if settings.allow_direct_customer_email:
            return customer_email, None
        fallback = effective_email_redirect()
        return (fallback, customer_email) if fallback else (customer_email, None)

    # Effective, not the raw setting: a reviewer can point mail at their own inbox at
    # runtime, and the inbound path below must agree about where it went or their
    # reply is refused as coming from a stranger.
    redirect = redirect_override or effective_email_redirect()
    if redirect:
        return redirect, customer_email
    return customer_email, None


def _subject_for(subject: str, intended_for: str | None) -> str:
    """Show the real recipient when mail has been redirected.

    Without this, a demo inbox fills with sixty identical-looking reminders and
    nobody can tell which customer each one was for.
    """
    return f"[→ {intended_for}] {subject}" if intended_for else subject


def reply_address_for(
    invoice_number: str,
    *,
    reply_token: uuid.UUID | None = None,
    is_demo: bool = True,
) -> str:
    """A stable address: legacy for demo, non-enumerable and tenant-safe for live."""
    if not is_demo and reply_token is not None:
        return f"reply-{reply_token.hex}@{settings.email_reply_to_domain}"
    local_invoice = re.sub(r"[^a-z0-9-]", "-", invoice_number.casefold()).strip("-")
    return f"invoice-{local_invoice}@{settings.email_reply_to_domain}"


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
    idempotency_key: str,
    provider: EmailProvider | None = None,
    reply_to: str | None = None,
    redirect_override: str | None = None,
    is_demo: bool = True,
    from_email: str | None = None,
) -> DeliveryResult:
    """Hand the message to a provider, or record it in dry-run."""
    recipient, intended_for = resolve_recipient(
        to, is_demo=is_demo, redirect_override=redirect_override
    )
    final_subject = _subject_for(subject, intended_for)

    if settings.email_dry_run:
        log.info(
            "messaging.dry_run",
            to=recipient,
            intended_for=intended_for,
            invoice_number=invoice_number,
            subject=final_subject,
        )
        return DeliveryResult(
            sent=True,
            provider=DRY_RUN_PROVIDER,
            message_id="dry-run",
            redirected_to=recipient if intended_for else None,
        )

    settings.assert_safe_to_send()
    provider = provider or ResendProvider(from_email=from_email)
    result = provider.send(
        to=recipient,
        subject=final_subject,
        html=render_html(body),
        text=body,
        headers={"X-Vasooli-Invoice": invoice_number},
        reply_to=reply_to or reply_address_for(invoice_number),
        idempotency_key=idempotency_key,
    )
    return DeliveryResult(
        sent=result.sent,
        provider=result.provider,
        message_id=result.message_id,
        error=result.error,
        retryable=result.retryable,
        redirected_to=recipient if intended_for else None,
    )


def sender_identity(session: Session, merchant: Merchant) -> str:
    """The verified From identity for one merchant, falling back only for demo."""
    if merchant.is_demo:
        return settings.email_from
    domain = session.exec(
        select(SendingDomain)
        .where(
            SendingDomain.merchant_id == merchant.id,
            SendingDomain.status == "verified",
        )
        .order_by(SendingDomain.verified_at.desc())  # type: ignore[attr-defined]
    ).first()
    if domain is None:
        raise OutboundBlockedError("A verified merchant sending domain is required")
    display = re.sub(r"[\r\n<>]", " ", merchant.legal_name or merchant.name).strip()
    return f"{display} Accounts <{domain.local_part}@{domain.domain}>"


def _backoff_seconds(attempt: int) -> int:
    """Bounded exponential backoff: 5m, 10m, 20m, 40m, capped at 2h.

    Bounded on purpose. An unbounded retry loop against a failing mail provider is a
    self-inflicted denial of service, and one that arrives with our own API key
    attached.
    """
    return min(300 * (2 ** max(0, attempt - 1)), 7200)


#: Escalation reason that does NOT block a retry.
#:
#: Tier 3 sends and then hands over — the HUMAN_REVIEW state is a consequence of that
#: send, not a reason to withhold it. Treating it like the others stranded the final
#: notice whenever its first delivery attempt failed.
_ESCALATION_CAUSED_BY_SENDING = "tier_3_reached"


def _no_longer_chased(session: Session, invoice: Invoice) -> bool:
    """Whether a drafted reminder must not be delivered any more.

    Every reason the cadence would stop, re-checked at delivery time. A queued retry
    carries copy approved before the invoice changed state, so the terminal statuses
    alone are not enough: a promise, a dispute, or an operator escalation all mean the
    customer should not hear from us, and each of those can land between drafting and
    sending.
    """
    if invoice.is_fully_paid:
        return True
    if invoice.status in (InvoiceStatus.RECOVERED, InvoiceStatus.WRITTEN_OFF):
        return True
    if invoice.status == InvoiceStatus.PROMISE_ACTIVE:
        return True
    if (
        invoice.status == InvoiceStatus.HUMAN_REVIEW
        and invoice.escalation_reason != _ESCALATION_CAUSED_BY_SENDING
    ):
        return True
    return has_open_dispute(session, invoice.id)


def _begin_attempt(reminder: Reminder, lease_token: str) -> None:
    """Durably claim an outbox row before the provider side effect begins."""
    reminder.attempt_count += 1
    reminder.last_attempt_at = utcnow()
    reminder.provider = "pending"
    reminder.delivery_state = "processing"
    reminder.lease_token = lease_token
    reminder.lease_expires_at = utcnow() + timedelta(seconds=DELIVERY_LEASE_SECONDS)
    reminder.next_retry_at = utcnow() + timedelta(seconds=_backoff_seconds(reminder.attempt_count))


def _record_attempt(reminder: Reminder, result: DeliveryResult) -> None:
    """Fold a provider result into an attempt already counted by `_begin_attempt`."""

    if result.sent:
        reminder.sent_at = utcnow()
        reminder.send_error = None
        reminder.next_retry_at = None
        reminder.delivery_state = "sent"
    else:
        reminder.sent_at = None
        reminder.send_error = (result.error or "unknown")[:500]
        reminder.delivery_state = (
            "failed"
            if result.retryable and reminder.attempt_count < MAX_DELIVERY_ATTEMPTS
            else "dead"
        )
        reminder.next_retry_at = (
            None
            if not result.retryable or reminder.attempt_count >= MAX_DELIVERY_ATTEMPTS
            else reminder.next_retry_at
        )
    reminder.provider = result.provider
    reminder.provider_message_id = result.message_id
    reminder.lease_token = None
    reminder.lease_expires_at = None


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
                # What actually happened, not what was intended. A redirected send did
                # not reach the customer, and a trail that does not say so is the
                # difference between "we contacted them" and "we believe we did".
                "delivered_to": result.redirected_to or customer.email,
                "redirected": result.redirected_to is not None,
                "attempt": reminder.attempt_count,
                "error": result.error,
                "retryable": result.retryable,
                "next_retry_at": (
                    reminder.next_retry_at.isoformat() if reminder.next_retry_at else None
                ),
                "exhausted": (not result.sent and reminder.attempt_count >= MAX_DELIVERY_ATTEMPTS),
            },
        )
    )


def _dispatch_reminder(
    session: Session,
    reminder_id: uuid.UUID,
    *,
    provider: EmailProvider | None = None,
) -> tuple[Reminder | None, DeliveryResult | None]:
    """Claim and deliver one durable outbox row without holding a DB lock on I/O."""
    now = utcnow()
    reminder = session.exec(
        select(Reminder).where(Reminder.id == reminder_id).with_for_update(skip_locked=True)
    ).first()
    if reminder is None or reminder.sent_at is not None:
        session.rollback()
        return None, None
    active_lease = (
        reminder.delivery_state == "processing"
        and reminder.lease_expires_at is not None
        and reminder.lease_expires_at > now
    )
    expired_lease = (
        reminder.delivery_state == "processing"
        and reminder.lease_expires_at is not None
        and reminder.lease_expires_at <= now
    )
    due = expired_lease or reminder.next_retry_at is None or reminder.next_retry_at <= now
    if active_lease or not due or reminder.attempt_count >= MAX_DELIVERY_ATTEMPTS:
        session.rollback()
        return None, None

    invoice = session.get(Invoice, reminder.invoice_id)
    if invoice is None:
        reminder.delivery_state = "dead"
        reminder.send_error = "abandoned: invoice missing"
        reminder.next_retry_at = None
        session.add(reminder)
        session.commit()
        return reminder, None

    # A queued retry carries a reminder drafted before the invoice changed state, so
    # every reason the cadence would stop has to be re-checked here — not just the
    # two terminal ones.
    #
    # This previously abandoned only RECOVERED, WRITTEN_OFF, and the single
    # HUMAN_REVIEW/complaint_in_reply combination, which meant a Tier 1 reminder that
    # failed delivery would still go out minutes after the customer promised to pay,
    # or after an operator escalated the invoice by hand. "A promise pauses
    # escalation" has to hold on the retry path too, or it does not hold at all.
    if _no_longer_chased(session, invoice):
        reminder.delivery_state = "dead"
        reminder.send_error = "abandoned: invoice no longer being chased"
        reminder.next_retry_at = None
        session.add(reminder)
        session.commit()
        return reminder, None

    customer = session.get(Customer, invoice.customer_id)
    if customer is None:
        reminder.delivery_state = "dead"
        reminder.send_error = "abandoned: customer missing"
        reminder.next_retry_at = None
        session.add(reminder)
        session.commit()
        return reminder, None

    lease_token = uuid.uuid4().hex
    _begin_attempt(reminder, lease_token)
    session.add(reminder)
    session.commit()

    # Re-read the invoice after the lease commit, immediately before handing the
    # message to the provider.
    #
    # The checks above ran against a snapshot taken before that commit, and committing
    # released the transaction that made them meaningful. Reconciliation, a reply, or
    # an operator escalation can all land in the window between the two — the cycle's
    # advisory lock does not cover webhook or reply processing. `populate_existing`
    # is required: without it SQLAlchemy's identity map hands back the same stale
    # object and this re-read silently confirms whatever it already believed.
    fresh = session.exec(
        select(Invoice).where(Invoice.id == invoice.id).execution_options(populate_existing=True)
    ).one()
    if _no_longer_chased(session, fresh):
        # Release the lease rather than burning an attempt: nothing was sent, and the
        # invoice is no longer one we chase.
        claimed = session.exec(
            select(Reminder).where(Reminder.id == reminder_id).with_for_update()
        ).one()
        claimed.delivery_state = "dead"
        claimed.send_error = "abandoned: invoice state changed before send"
        claimed.next_retry_at = None
        claimed.lease_token = None
        claimed.lease_expires_at = None
        session.add(claimed)
        session.commit()
        log.info(
            "messaging.aborted_before_send",
            invoice_number=fresh.invoice_number,
            status=str(fresh.status),
        )
        return claimed, None

    merchant = session.get(Merchant, fresh.merchant_id)
    demo_settings = (
        session.get(DemoSettings, SINGLETON_ID) if merchant and merchant.is_demo else None
    )
    redirect_override = demo_settings.email_redirect_override if demo_settings else None
    try:
        if merchant is None:
            result = DeliveryResult(
                sent=False,
                provider=getattr(provider, "name", "unknown"),
                error="invoice merchant missing",
                retryable=False,
            )
        elif not merchant.is_demo and is_suppressed(
            session, merchant.id, customer=customer, email=customer.email
        ):
            result = DeliveryResult(
                sent=False,
                provider=getattr(provider, "name", "unknown"),
                error="customer is suppressed",
                retryable=False,
            )
        else:
            if not merchant.is_demo:
                # Domain first, then quota: a merchant who cannot legitimately send at
                # all should not have the attempt counted against their daily budget.
                assert_can_send(session, merchant.id, is_demo=merchant.is_demo)
                claim_send_slot(session, merchant.id)
            result = _send_email(
                to=customer.email,
                subject=reminder.subject,
                body=reminder.body,
                invoice_number=fresh.invoice_number,
                idempotency_key=f"vasooli-reminder-{reminder.id}",
                provider=provider,
                reply_to=reply_address_for(
                    fresh.invoice_number,
                    reply_token=fresh.reply_token,
                    is_demo=merchant.is_demo,
                ),
                redirect_override=redirect_override,
                is_demo=merchant.is_demo,
                from_email=sender_identity(session, merchant),
            )
    except OutboundBlockedError as exc:
        result = DeliveryResult(
            sent=False,
            provider=getattr(provider, "name", "unknown"),
            error=str(exc),
            retryable=False,
        )
    except Exception as exc:  # provider adapters should return failures, but fail safe
        result = DeliveryResult(
            sent=False,
            provider=getattr(provider, "name", "unknown"),
            error=f"{type(exc).__name__}: {exc}",
            retryable=True,
        )

    claimed = session.exec(
        select(Reminder).where(Reminder.id == reminder_id).with_for_update()
    ).one()
    if claimed.lease_token != lease_token:
        # The lease is deliberately much longer than a provider timeout, so this can
        # happen only after severe scheduler/database delay. Never let a stale worker
        # overwrite the newer owner's outcome.
        session.rollback()
        log.warning("messaging.stale_lease_result", reminder_id=str(reminder_id))
        return claimed, None

    invoice = session.get(Invoice, claimed.invoice_id)
    customer = session.get(Customer, invoice.customer_id) if invoice else None
    _record_attempt(claimed, result)
    session.add(claimed)
    if result.sent and invoice is not None:
        invoice.reminders_sent += 1
        invoice.current_tier = max(invoice.current_tier, claimed.tier)
        invoice.last_reminder_at = utcnow()
        session.add(invoice)
    if invoice is not None and customer is not None:
        _audit_attempt(session, invoice, claimed, customer, result)
    session.commit()
    session.refresh(claimed)
    return claimed, result


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
        delivery_state="pending",
        next_retry_at=utcnow(),
    )
    session.add(reminder)
    # This commit is the transactional outbox boundary: a crash from this point on
    # leaves a reclaimable pending row; no provider call can exist without its intent.
    session.commit()
    session.refresh(reminder)
    delivered, _ = _dispatch_reminder(session, reminder.id, provider=provider)
    return delivered or reminder


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
    due_ids = session.exec(
        select(Reminder)
        .where(
            Reminder.sent_at.is_(None),  # type: ignore[union-attr]
            Reminder.attempt_count < MAX_DELIVERY_ATTEMPTS,
            or_(
                (
                    Reminder.delivery_state.in_(["pending", "failed"])  # type: ignore[union-attr]
                    & (Reminder.next_retry_at.is_not(None))  # type: ignore[union-attr]
                    & (Reminder.next_retry_at <= now)  # type: ignore[operator]
                ),
                (
                    (Reminder.delivery_state == "processing")
                    & (Reminder.lease_expires_at.is_not(None))  # type: ignore[union-attr]
                    & (Reminder.lease_expires_at <= now)  # type: ignore[operator]
                ),
            ),
        )
        .order_by(Reminder.next_retry_at)
        .limit(limit)
    ).all()

    recovered = 0
    still_failing = 0
    attempted = 0

    for due in due_ids:
        _, result = _dispatch_reminder(session, due.id, provider=provider)
        if result is None:
            continue
        attempted += 1
        if result.sent:
            recovered += 1
        else:
            still_failing += 1

    if attempted:
        log.info(
            "messaging.retry_complete",
            attempted=attempted,
            recovered=recovered,
            still_failing=still_failing,
        )
    return {"attempted": attempted, "recovered": recovered, "still_failing": still_failing}
