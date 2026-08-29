"""Resend's real Svix signature format is verified over the untouched body."""

import base64
import hashlib
import hmac
import json
import time

import pytest

from app.core.config import settings
from app.integrations.email.resend_receiving import verify_webhook


def _signed_headers(raw: bytes, secret_bytes: bytes) -> dict[str, str]:
    event_id = "msg_test_native_resend"
    timestamp = str(int(time.time()))
    signed = b".".join((event_id.encode(), timestamp.encode(), raw))
    digest = base64.b64encode(hmac.new(secret_bytes, signed, hashlib.sha256).digest()).decode()
    return {
        "svix_id": event_id,
        "svix_timestamp": timestamp,
        "svix_signature": f"v1,{digest}",
    }


def test_real_resend_signature_contract(monkeypatch):
    secret_bytes = b"native-resend-test-secret"
    monkeypatch.setattr(
        settings,
        "resend_inbound_webhook_secret",
        "whsec_" + base64.b64encode(secret_bytes).decode(),
    )
    raw = json.dumps(
        {"type": "email.received", "data": {"email_id": "received-1"}},
        separators=(",", ":"),
    ).encode()
    event = verify_webhook(raw, **_signed_headers(raw, secret_bytes))
    assert event["type"] == "email.received"


def test_altered_body_is_rejected(monkeypatch):
    secret_bytes = b"native-resend-test-secret"
    monkeypatch.setattr(
        settings,
        "resend_inbound_webhook_secret",
        "whsec_" + base64.b64encode(secret_bytes).decode(),
    )
    original = b'{"type":"email.received"}'
    with pytest.raises(ValueError, match="matching signature"):
        verify_webhook(b'{"type":"email.sent"}', **_signed_headers(original, secret_bytes))
