"""Apply what the mail provider says happened to a reminder after it accepted it.

Same shape as Razorpay reconciliation, for the same reasons: the provider delivers
at-least-once, so deduplication is a unique index on their event id rather than
anything held in memory; the raw payload is kept verbatim; and the event is recorded
before it is acted on.

The one decision this makes beyond bookkeeping is a hard bounce. A permanently refused
address is not a transient problem and not something a person can fix by waiting, so the
invoice is taken out of automation and handed to a human. Continuing to send would
achieve nothing except damage to the sending domain's reputation — and, worse, the
invoice would keep advancing through the tiers and eventually be escalated as an
unresponsive customer who never received a single message.
"""

from datetime import datetime
from typing import Any

from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from app.core.clock import utcnow
from app.core.logging import get_logger
from app.models import AuditAction, AuditActor, AuditLog, Invoice, Reminder
from app.models.email_event import PROVIDER_EVENT_STATES, DeliveryState, EmailEvent

log = get_logger("delivery_events")


def _parse_time(value: Any) -> datetime:
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return utcnow()


def _find_reminder(session: Session, message_id: str | None) -> Reminder | None:
    """Correlate on the provider's own message id, which we stored when we sent.

    Never on the recipient address: one customer can hold several open invoices, and
    matching a bounce to the wrong reminder would escalate the wrong invoice.
    """
    if not message_id:
        return None
    return session.exec(select(Reminder).where(Reminder.provider_message_id == message_id)).first()


def record_delivery_event(
    session: Session,
    *,
    provider_event_id: str,
    event_type: str,
    payload: dict[str, Any],
) -> dict[str, str]:
    """Store one provider event and apply it. Idempotent on `provider_event_id`."""
    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    message_id = data.get("email_id") or data.get("message_id")
    recipient = ""
    raw_to = data.get("to")
    if isinstance(raw_to, list) and raw_to:
        recipient = str(raw_to[0])
    elif isinstance(raw_to, str):
        recipient = raw_to

    state = PROVIDER_EVENT_STATES.get(event_type)
    reminder = _find_reminder(session, str(message_id) if message_id else None)

    bounce = data.get("bounce") if isinstance(data.get("bounce"), dict) else {}
    detail = bounce.get("message") or bounce.get("subType") or data.get("reason")

    event = EmailEvent(
        reminder_id=reminder.id if reminder else None,
        invoice_id=reminder.invoice_id if reminder else None,
        provider_event_id=provider_event_id,
        provider_message_id=str(message_id) if message_id else None,
        event_type=event_type,
        state=state,
        recipient=recipient,
        detail=str(detail)[:500] if detail else None,
        raw_payload=payload,
        occurred_at=_parse_time(payload.get("created_at") or data.get("created_at")),
    )
    session.add(event)
    try:
        session.commit()
    except IntegrityError:
        # The unique index rejected it: already seen. Answer 200 so the provider stops
        # redelivering, and change nothing.
        session.rollback()
        return {"status": "duplicate_ignored", "event_id": provider_event_id}

    if state is None:
        # Stored for the trail; deliberately changes nothing. An `email.opened` is not
        # evidence about delivery, and acting on one would be tracking, not operations.
        return {"status": "recorded", "event_id": provider_event_id}

    if reminder is None:
        # A real event about a message we cannot tie to a reminder. Kept rather than
        # dropped — a bounce we cannot attribute is still a bounce.
        log.warning("delivery_events.unmatched", event_type=event_type, message_id=str(message_id))
        return {"status": "unmatched", "event_id": provider_event_id}

    _apply(session, reminder, state, detail=event.detail, occurred_at=event.occurred_at)
    return {"status": "applied", "event_id": provider_event_id}


def _apply(
    session: Session,
    reminder: Reminder,
    state: str,
    *,
    detail: str | None,
    occurred_at: datetime,
) -> None:
    # Out-of-order delivery is ordinary — `email.delivered` can arrive after a later
    # event. An older event never overwrites a newer one.
    if reminder.last_delivery_event_at and occurred_at < reminder.last_delivery_event_at:
        log.info("delivery_events.stale_ignored", reminder_id=str(reminder.id))
        return

    reminder.delivery_status = state
    reminder.delivery_detail = detail
    reminder.last_delivery_event_at = occurred_at

    if state == DeliveryState.DELIVERED:
        reminder.delivered_at = occurred_at
    elif state == DeliveryState.BOUNCED:
        reminder.bounced_at = occurred_at

    session.add(reminder)

    invoice = session.get(Invoice, reminder.invoice_id)
    session.add(
        AuditLog(
            invoice_id=reminder.invoice_id,
            actor=AuditActor.SYSTEM,
            action=AuditAction.REMINDER_DELIVERY_UPDATED,
            detail={
                "reminder_id": str(reminder.id),
                "tier": reminder.tier,
                "state": state,
                "provider_detail": detail,
                "occurred_at": occurred_at.isoformat(),
            },
        )
    )

    if state in DeliveryState.SUPPRESSING and invoice is not None:
        _stop_automation(session, invoice, reminder, state)

    session.commit()
    log.info(
        "delivery_events.applied",
        reminder_id=str(reminder.id),
        state=state,
        invoice_number=invoice.invoice_number if invoice else None,
    )


def _stop_automation(session: Session, invoice: Invoice, reminder: Reminder, state: str) -> None:
    """A permanently undeliverable address ends the automated cadence.

    Imported here rather than at module scope: app.services.recovery imports the
    messaging stack, and a top-level import would make the two modules circular.
    """
    from app.services.recovery import escalate_to_human

    if not invoice.is_in_automation:
        return

    reason = "email_bounced" if state == DeliveryState.BOUNCED else "recipient_marked_as_spam"
    escalate_to_human(session, invoice, reason, actor=AuditActor.SYSTEM)
    session.add(
        AuditLog(
            invoice_id=invoice.id,
            actor=AuditActor.SYSTEM,
            action=AuditAction.CONTACT_SUPPRESSED,
            detail={
                "reminder_id": str(reminder.id),
                "tier": reminder.tier,
                "state": state,
                "note": (
                    "Automated email stopped for this invoice. Continuing to send to a "
                    "permanently refused address reaches nobody and damages the sending "
                    "domain — and the invoice would otherwise keep advancing through the "
                    "tiers and be escalated as an unresponsive customer who never "
                    "received a message."
                ),
            },
        )
    )
    log.warning(
        "delivery_events.contact_suppressed",
        invoice_number=invoice.invoice_number,
        state=state,
    )
