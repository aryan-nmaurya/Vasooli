"""Signed inbound email ingestion, identity correlation, and durable evidence."""

import json

import pytest
from fastapi.testclient import TestClient
from sqlmodel import select

from app.core.config import settings
from app.integrations.razorpay_signature import compute_signature
from app.main import create_app
from app.models import InboundMessage
from app.services.messaging import reply_address_for


@pytest.fixture
def api(session):
    with TestClient(create_app()) as client:
        yield client


def payload(invoice, customer, *, sender: str | None = None) -> dict:
    return {
        "invoice_number": invoice.invoice_number,
        "from_email": sender or customer.email,
        "to_email": reply_address_for(invoice.invoice_number),
        "subject": f"Re: {invoice.invoice_number}",
        "text": "We received only nine of the twelve units. Please investigate.",
        "message_id": "<customer-message-1@example.com>",
        "in_reply_to": "<vasooli-reminder-1@example.com>",
    }


def post(api, body: dict, *, event_id: str = "inbound-event-1"):
    raw = json.dumps(body).encode()
    return api.post(
        "/api/webhooks/inbound-email",
        content=raw,
        headers={
            "Content-Type": "application/json",
            "X-Vasooli-Event-Id": event_id,
            "X-Vasooli-Signature": compute_signature(raw, settings.inbound_email_webhook_secret),
        },
    )


def test_signed_message_is_stored_and_processed(api, session, invoice, customer, monkeypatch):
    monkeypatch.setattr(settings, "inbound_email_webhook_secret", "test-inbound-secret")
    seen: list[str] = []

    def handle(_session, _invoice, body, **_kwargs):
        seen.append(body)

    monkeypatch.setattr("app.api.webhooks.handle_reply", handle)
    response = post(api, payload(invoice, customer))

    assert response.json()["status"] == "processed"
    message = session.exec(select(InboundMessage)).one()
    assert message.signature_verified is True
    assert message.body_text == payload(invoice, customer)["text"]
    assert message.processed_at is not None
    assert seen == [message.body_text]


def test_duplicate_provider_delivery_is_ignored(api, session, invoice, customer, monkeypatch):
    monkeypatch.setattr(settings, "inbound_email_webhook_secret", "test-inbound-secret")
    monkeypatch.setattr("app.api.webhooks.handle_reply", lambda *_a, **_k: None)

    assert post(api, payload(invoice, customer)).status_code == 200
    duplicate = post(api, payload(invoice, customer))
    assert duplicate.json()["status"] == "duplicate_ignored"
    assert len(session.exec(select(InboundMessage)).all()) == 1


def test_sender_must_match_the_invoice_customer(api, session, invoice, customer, monkeypatch):
    monkeypatch.setattr(settings, "inbound_email_webhook_secret", "test-inbound-secret")
    response = post(api, payload(invoice, customer, sender="attacker@example.com"))
    assert response.status_code == 403
    assert session.exec(select(InboundMessage)).all() == []


def test_unsigned_inbound_email_is_rejected(api, session, invoice, customer, monkeypatch):
    monkeypatch.setattr(settings, "inbound_email_webhook_secret", "test-inbound-secret")
    response = api.post("/api/webhooks/inbound-email", json=payload(invoice, customer))
    assert response.status_code == 400
    assert session.exec(select(InboundMessage)).all() == []


def _resend_event(invoice, customer) -> dict:
    return {
        "type": "email.received",
        "created_at": "2026-08-28T05:30:00Z",
        "data": {
            "email_id": "received-email-1",
            "created_at": "2026-08-28T05:30:00Z",
            "from": f"Customer <{customer.email}>",
            "to": [reply_address_for(invoice.invoice_number)],
            "message_id": "<native-message@example.com>",
            "subject": f"Re: {invoice.invoice_number}",
            "attachments": [],
        },
    }


def _resend_message(invoice, customer) -> dict:
    return {
        "id": "received-email-1",
        "from": f"Customer <{customer.email}>",
        "to": [reply_address_for(invoice.invoice_number)],
        "received_for": [reply_address_for(invoice.invoice_number)],
        "message_id": "<native-message@example.com>",
        "created_at": "2026-08-28T05:30:00Z",
        "subject": f"Re: {invoice.invoice_number}",
        "text": "The quantity is short by three units. Please investigate.",
        "html": "<p>The quantity is short by three units.</p>",
        "headers": {"in-reply-to": "<provider-reminder@example.com>"},
        "attachments": [],
    }


def test_native_resend_event_fetches_and_persists_full_message(
    api, session, invoice, customer, monkeypatch
):
    event = _resend_event(invoice, customer)
    message = _resend_message(invoice, customer)
    monkeypatch.setattr("app.api.webhooks.verify_webhook", lambda *_a, **_k: event)

    async def fetch(_email_id):
        return message

    monkeypatch.setattr("app.api.webhooks.fetch_received_email", fetch)
    monkeypatch.setattr("app.api.webhooks.handle_reply", lambda *_a, **_k: None)

    response = api.post(
        "/api/webhooks/resend/inbound",
        content=b'{"signed":"raw-body"}',
        headers={"svix-id": "svix-event-1"},
    )
    assert response.status_code == 200
    assert response.json()["status"] == "processed"
    stored = session.exec(select(InboundMessage)).one()
    assert stored.provider_event_id == "svix-event-1"
    assert stored.body_text == message["text"]
    assert stored.sender == customer.email
    assert stored.in_reply_to == "<provider-reminder@example.com>"
    assert "html" not in stored.raw_payload["message"]


def test_native_resend_duplicate_is_suppressed_before_refetch(
    api, session, invoice, customer, monkeypatch
):
    event = _resend_event(invoice, customer)
    calls = 0
    monkeypatch.setattr("app.api.webhooks.verify_webhook", lambda *_a, **_k: event)

    async def fetch(_email_id):
        nonlocal calls
        calls += 1
        return _resend_message(invoice, customer)

    monkeypatch.setattr("app.api.webhooks.fetch_received_email", fetch)
    monkeypatch.setattr("app.api.webhooks.handle_reply", lambda *_a, **_k: None)
    headers = {"svix-id": "svix-event-duplicate"}
    first = api.post("/api/webhooks/resend/inbound", content=b"{}", headers=headers)
    assert first.status_code == 200
    duplicate = api.post("/api/webhooks/resend/inbound", content=b"{}", headers=headers)
    assert duplicate.json()["status"] == "duplicate_ignored"
    assert calls == 1


def test_native_resend_rejects_invalid_signature(api, monkeypatch):
    def invalid(*_args, **_kwargs):
        raise ValueError("bad signature")

    monkeypatch.setattr("app.api.webhooks.verify_webhook", invalid)
    response = api.post(
        "/api/webhooks/resend/inbound",
        content=b"{}",
        headers={"svix-id": "bad"},
    )
    assert response.status_code == 400


# ===========================================================================
# Redirect mode must round-trip.
# ===========================================================================


def test_the_operator_inbox_can_reply_when_redirect_is_on(
    api, session, invoice, customer, monkeypatch
):
    """The demo path, and previously a dead end.

    With EMAIL_REDIRECT_TO set, Vasooli sends the reminder to the operator rather than
    the customer. A reply therefore arrives From: the operator — which failed sender
    correlation and returned 403, so redirect mode could send mail you were able to
    answer and then silently discard the answer.
    """
    monkeypatch.setattr(settings, "inbound_email_webhook_secret", "test-inbound-secret")
    monkeypatch.setattr(settings, "email_redirect_to", "ops@vasooli.test")
    monkeypatch.setattr("app.api.webhooks.handle_reply", lambda *a, **k: None)

    response = post(api, payload(invoice, customer, sender="ops@vasooli.test"))
    assert response.status_code == 200

    stored = session.exec(select(InboundMessage)).one()
    # The trail records who actually wrote it, not who it was about.
    assert stored.sender == "ops@vasooli.test"
    assert stored.invoice_id == invoice.id


def test_the_operator_exception_still_requires_the_invoice_alias(
    api, session, invoice, customer, monkeypatch
):
    """The alias is the only correlation left for an operator reply, so it must hold.

    Otherwise the exception would let one authenticated address post a reply onto any
    invoice in the ledger.
    """
    monkeypatch.setattr(settings, "inbound_email_webhook_secret", "test-inbound-secret")
    monkeypatch.setattr(settings, "email_redirect_to", "ops@vasooli.test")

    body = payload(invoice, customer, sender="ops@vasooli.test")
    body["to_email"] = "invoice-INV-DOES-NOT-EXIST@vasooli.test"
    assert post(api, body).status_code == 403


def test_a_stranger_is_still_refused_when_redirect_is_on(
    api, session, invoice, customer, monkeypatch
):
    """The exception is for the configured operator address and nobody else."""
    monkeypatch.setattr(settings, "inbound_email_webhook_secret", "test-inbound-secret")
    monkeypatch.setattr(settings, "email_redirect_to", "ops@vasooli.test")

    assert (
        post(api, payload(invoice, customer, sender="stranger@elsewhere.test")).status_code == 403
    )


def test_no_operator_exception_when_redirect_is_unset(api, session, invoice, customer, monkeypatch):
    """With redirect off there is no reason for the operator's inbox to be special."""
    monkeypatch.setattr(settings, "inbound_email_webhook_secret", "test-inbound-secret")
    monkeypatch.setattr(settings, "email_redirect_to", None)

    assert post(api, payload(invoice, customer, sender="ops@vasooli.test")).status_code == 403


def test_the_customer_path_is_unchanged(api, session, invoice, customer, monkeypatch):
    """The normal case must not have been altered by the exception."""
    monkeypatch.setattr(settings, "inbound_email_webhook_secret", "test-inbound-secret")
    monkeypatch.setattr(settings, "email_redirect_to", "ops@vasooli.test")
    monkeypatch.setattr("app.api.webhooks.handle_reply", lambda *a, **k: None)

    assert post(api, payload(invoice, customer)).status_code == 200
