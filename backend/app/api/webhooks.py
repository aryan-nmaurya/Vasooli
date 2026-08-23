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

from fastapi import APIRouter, HTTPException, Request, status
from sqlalchemy.exc import IntegrityError

from app.core.db import SessionDep
from app.core.logging import get_logger
from app.integrations.razorpay_signature import verify_signature
from app.models import AuditAction, AuditActor, AuditLog, ReconciliationEvent
from app.services.reconciliation import begin_attempt, mark_event_failed, process_event

router = APIRouter(prefix="/api/webhooks", tags=["webhooks"])
log = get_logger("webhooks")


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

    return {"status": "processed", "event_id": event_id}
