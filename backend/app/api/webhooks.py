"""Razorpay webhook receiver. Doc §6.

The endpoint the technical panel will probe hardest, so the ordering below is
deliberate and worth reading top to bottom:

    verify signature  ->  record the event  ->  process it

Signature first, because an unverified payload is untrusted input and should not reach
the database at all. Recording second, because the insert IS the deduplication — a
unique index on the provider's event id is atomic, survives a restart, and is shared
across workers, none of which is true of an in-memory set. Processing last, and in a
separate transaction, so a bug in reconciliation leaves the event stored with an error
rather than losing a payment.
"""

import json
import secrets
from datetime import datetime
from email.utils import parseaddr
from html.parser import HTMLParser
from typing import Any

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from app.core.clock import utcnow
from app.core.config import settings
from app.core.db import SessionDep
from app.core.logging import get_logger
from app.core.runtime import effective_email_redirect
from app.integrations.email.resend_receiving import fetch_received_email, verify_webhook
from app.integrations.razorpay_signature import compute_signature, verify_signature
from app.models import (
    AuditAction,
    AuditActor,
    AuditLog,
    Customer,
    InboundMessage,
    Invoice,
    ReconciliationEvent,
)
from app.services.messaging import reply_address_for
from app.services.reconciliation import begin_attempt, mark_event_failed, process_event
from app.services.replies import handle_reply

router = APIRouter(prefix="/api/webhooks", tags=["webhooks"])
log = get_logger("webhooks")


class InboundEmail(BaseModel):
    model_config = ConfigDict(extra="forbid")

    invoice_number: str = Field(min_length=1, max_length=120)
    from_email: str = Field(min_length=3, max_length=320)
    to_email: str = Field(min_length=3, max_length=320)
    subject: str = Field(default="", max_length=500)
    text: str = Field(min_length=1, max_length=20_000)
    message_id: str = Field(min_length=1, max_length=500)
    in_reply_to: str | None = Field(default=None, max_length=500)
    received_at: datetime | None = None


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        if data.strip():
            self.parts.append(data.strip())


def _plain_body(message: dict[str, Any]) -> str:
    text = message.get("text")
    if isinstance(text, str) and text.strip():
        body = text.strip()
    else:
        parser = _TextExtractor()
        parser.feed(str(message.get("html") or ""))
        body = "\n".join(parser.parts).strip()
    if not body:
        return "[The received email contained no plain-text or HTML body.]"
    if len(body) > 100_000:
        return body[:100_000] + "\n\n[Body truncated at 100,000 characters.]"
    return body


def _addresses(value: Any) -> list[str]:
    """Normalize Resend's recipient field without iterating a single string by char."""
    if isinstance(value, str):
        return [value]
    if isinstance(value, (list, tuple)):
        return [str(item) for item in value if item]
    return []


def _find_inbound_invoice(
    session: Session,
    *,
    sender: str,
    recipients: list[str],
) -> Invoice | None:
    """Correlate both the authenticated sender and invoice-specific reply address.

    Two independent facts have to line up: the message was sent by the person this
    invoice belongs to, and it was addressed to the reply alias minted for that
    specific invoice. A valid provider signature proves neither — it proves only that
    the provider really delivered this, which is why authorization is checked here
    rather than assumed from the signature.

    The operator's own inbox is the one deliberate exception, and only when
    EMAIL_REDIRECT_TO is set. In redirect mode Vasooli *itself* rerouted the reminder
    there instead of to the customer, so a reply from that inbox is the intended
    round-trip rather than a stranger writing in. Without this, redirect mode is a
    trap: it sends mail you can reply to and then silently refuses the reply, which
    reads as inbound email being broken. The narrowing matters — the message must
    still carry a valid signature AND be addressed to this invoice's unique alias, and
    the real sender is recorded verbatim on the InboundMessage, so the trail never
    claims the customer wrote something the operator did.
    """
    sender_address = parseaddr(sender)[1].casefold()
    recipient_addresses = {parseaddr(value)[1].casefold() for value in recipients}

    # The same effective address messaging.resolve_recipient sent to. Reading the raw
    # setting here would mean a reviewer who redirected mail to their own inbox could
    # receive a reminder and then have their reply rejected as an unknown sender.
    redirect_to = (effective_email_redirect() or "").casefold()
    sender_is_operator = bool(redirect_to) and sender_address == parseaddr(redirect_to)[1]

    if sender_is_operator:
        # The alias is the only correlation available, and it is invoice-specific.
        candidates = session.exec(select(Invoice)).all()
    else:
        candidates = session.exec(
            select(Invoice).join(Customer).where(func.lower(Customer.email) == sender_address)
        ).all()

    return next(
        (
            invoice
            for invoice in candidates
            if reply_address_for(invoice.invoice_number).casefold() in recipient_addresses
        ),
        None,
    )


def _record_inbound(
    session: Session,
    *,
    invoice: Invoice,
    event_id: str,
    message_id: str,
    sender: str,
    recipient: str,
    subject: str,
    body: str,
    in_reply_to: str | None,
    raw_payload: dict[str, Any],
    received_at: datetime,
) -> dict[str, str]:
    message = InboundMessage(
        invoice_id=invoice.id,
        provider_event_id=event_id,
        message_id=message_id,
        sender=sender,
        recipient=recipient,
        subject=subject,
        body_text=body,
        in_reply_to=in_reply_to,
        raw_payload=raw_payload,
        signature_verified=True,
        received_at=received_at,
    )
    session.add(message)
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        return {"status": "duplicate_ignored", "event_id": event_id}

    try:
        # The complete body remains evidence; AI/policy gets a bounded working copy.
        handle_reply(session, invoice, body[:20_000], inbound_message_id=str(message.id))
        message.processed_at = utcnow()
        session.add(message)
        session.commit()
    except Exception as exc:
        session.rollback()
        session.refresh(message)
        message.processing_error = f"{type(exc).__name__}: {exc}"[:500]
        session.add(message)
        session.commit()
        log.exception("inbound_email.processing_failed", event_id=event_id)
        return {"status": "recorded_for_review", "event_id": event_id}
    return {"status": "processed", "event_id": event_id}


@router.post("/resend/inbound")
async def resend_inbound_webhook(request: Request, session: SessionDep) -> dict[str, str]:
    """Verify a native Resend event and retrieve the complete received email."""
    raw_body = await request.body()
    event_id = request.headers.get("svix-id")
    try:
        event = verify_webhook(
            raw_body,
            svix_id=event_id,
            svix_timestamp=request.headers.get("svix-timestamp"),
            svix_signature=request.headers.get("svix-signature"),
        )
    except (UnicodeDecodeError, ValueError) as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "invalid webhook signature") from exc

    if event.get("type") != "email.received" or not isinstance(event.get("data"), dict):
        return {"status": "event_ignored", "event_id": event_id or "unknown"}
    if not event_id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "missing svix-id")
    if session.exec(
        select(InboundMessage).where(InboundMessage.provider_event_id == event_id)
    ).first():
        return {"status": "duplicate_ignored", "event_id": event_id}

    data = event["data"]
    email_id = data.get("email_id")
    if not isinstance(email_id, str) or not email_id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "missing email id")
    try:
        message = await fetch_received_email(email_id)
    except Exception as exc:  # Resend retries non-2xx webhook responses.
        log.warning("inbound_email.fetch_failed", event_id=event_id, error=type(exc).__name__)
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "email body unavailable") from exc

    sender = str(message.get("from") or data.get("from") or "")
    recipients = _addresses(message.get("received_for") or message.get("to") or data.get("to"))
    invoice = _find_inbound_invoice(session, sender=sender, recipients=recipients)
    if invoice is None:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "message does not match invoice thread")

    expected_recipient = reply_address_for(invoice.invoice_number)
    headers = message.get("headers") if isinstance(message.get("headers"), dict) else {}
    lowered_headers = {str(key).casefold(): str(value) for key, value in headers.items()}
    received_raw = message.get("created_at") or data.get("created_at")
    try:
        received_at = datetime.fromisoformat(str(received_raw).replace("Z", "+00:00"))
    except ValueError:
        received_at = utcnow()

    # Exclude HTML from the JSON copy: the complete normalized text is stored in its
    # own column and attachments remain metadata-only, keeping the evidence durable
    # without duplicating an arbitrarily large body.
    retained_message = {key: value for key, value in message.items() if key != "html"}
    return _record_inbound(
        session,
        invoice=invoice,
        event_id=event_id,
        message_id=str(message.get("message_id") or data.get("message_id") or email_id),
        sender=parseaddr(sender)[1],
        recipient=expected_recipient,
        subject=str(message.get("subject") or data.get("subject") or "")[:500],
        body=_plain_body(message),
        in_reply_to=lowered_headers.get("in-reply-to"),
        raw_payload={"event": event, "message": retained_message},
        received_at=received_at,
    )


@router.post("/inbound-email")
async def inbound_email_webhook(request: Request, session: SessionDep) -> dict[str, str]:
    """Accept a provider-normalised email only after HMAC and sender correlation."""
    raw_body = await request.body()
    signature = request.headers.get("X-Vasooli-Signature")
    expected = compute_signature(raw_body, settings.inbound_email_webhook_secret)
    if (
        not settings.inbound_email_webhook_secret
        or not signature
        or not secrets.compare_digest(expected, signature)
    ):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "invalid signature")

    try:
        payload = InboundEmail.model_validate_json(raw_body)
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "malformed inbound email") from exc

    event_id = request.headers.get("X-Vasooli-Event-Id") or payload.message_id
    invoice = _find_inbound_invoice(
        session,
        sender=payload.from_email,
        recipients=[payload.to_email],
    )
    if invoice is None:
        # A valid provider signature proves delivery, not that an arbitrary From
        # address owns this invoice. Correlation is a separate authorization check.
        raise HTTPException(status.HTTP_403_FORBIDDEN, "message does not match invoice thread")
    if invoice.invoice_number != payload.invoice_number:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "message does not match invoice thread")
    return _record_inbound(
        session,
        invoice=invoice,
        event_id=event_id,
        message_id=payload.message_id,
        sender=payload.from_email,
        recipient=payload.to_email,
        subject=payload.subject,
        body=payload.text,
        in_reply_to=payload.in_reply_to,
        raw_payload=payload.model_dump(mode="json"),
        received_at=payload.received_at or utcnow(),
    )


def _event_id(request: Request, payload: dict) -> str:
    """The idempotency key.

    Razorpay sends `X-Razorpay-Event-Id`. The fallbacks exist so that a delivery
    missing the header still deduplicates: a payload id if present, otherwise a hash
    of the body itself, which is stable across redeliveries of the same event.
    """
    header = request.headers.get("X-Razorpay-Event-Id")
    if header:
        return header
    if payload.get("id"):
        return str(payload["id"])
    import hashlib

    return "sha256:" + hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


@router.post("/razorpay")
async def razorpay_webhook(request: Request, session: SessionDep) -> dict[str, str]:
    # Raw bytes, not the parsed body. Re-serializing changes key order and whitespace,
    # which changes the digest and rejects every genuine webhook.
    raw_body = await request.body()
    signature = request.headers.get("X-Razorpay-Signature")

    if not verify_signature(raw_body, signature):
        log.warning("webhook.signature_invalid", body_bytes=len(raw_body))
        session.add(
            AuditLog(
                invoice_id=None,
                actor=AuditActor.RAZORPAY,
                action=AuditAction.WEBHOOK_SIGNATURE_INVALID,
                detail={"body_bytes": len(raw_body), "had_signature": bool(signature)},
            )
        )
        session.commit()
        # 400, and the payload is not stored. An unverified body is untrusted.
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "invalid signature")

    try:
        payload = json.loads(raw_body)
    except json.JSONDecodeError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "malformed JSON") from exc

    event_id = _event_id(request, payload)

    event = ReconciliationEvent(
        provider_event_id=event_id,
        event_type=payload.get("event", "unknown"),
        raw_payload=payload,
        signature_verified=True,
    )
    session.add(event)
    try:
        session.commit()
    except IntegrityError:
        # The unique index rejected it: we have already seen this event. Answer 200 so
        # Razorpay stops redelivering, and change nothing.
        session.rollback()
        log.info("webhook.duplicate_ignored", event_id=event_id)
        return {"status": "duplicate_ignored", "event_id": event_id}

    session.refresh(event)

    begin_attempt(session, event)
    attempts = event.attempts
    session.commit()

    try:
        process_event(session, event)
    except Exception as exc:  # noqa: BLE001
        # Record the failure rather than returning 5xx. A 5xx makes Razorpay retry,
        # and retrying into an unhandled bug amplifies it.
        #
        # But a 200 also means Razorpay stops trying, so the failure has to become OUR
        # responsibility: mark_event_failed stores the error, counts the attempt, and
        # schedules a retry. The event then appears in the reconciliation exceptions
        # queue rather than existing only as a log line nobody reads.
        session.rollback()
        session.refresh(event)
        # The rollback undid the attempt counter too; restore it so the backoff and
        # the retry limit still advance.
        event.attempts = attempts
        mark_event_failed(session, event, f"{type(exc).__name__}: {exc}")
        log.exception("webhook.processing_failed", event_id=event_id)
        return {"status": "recorded_for_retry", "event_id": event_id}

    # Report what actually happened, not merely that we did not crash. An unmatched
    # payment leaves the event FAILED; answering "processed" would tell Razorpay — and
    # anyone reading the logs — that money was reconciled when it was not.
    session.refresh(event)
    return {"status": event.status, "event_id": event_id}
