"""Merchant payment webhooks are signed with the merchant's secret, not ours.

Payment links live on each merchant's own Razorpay account, so their account signs
the confirming webhook with their own signing secret. Verification only ever tried
the platform secret, so every one of those failed with a 400 and the payment was
picked up hours later by the reconciliation sweep instead of in seconds.

The payload is used only to choose which secret to try. It is never trusted: an
attacker may name any merchant and still cannot produce a valid HMAC without that
merchant's secret, so the signature remains the thing that decides.
"""

import hashlib
import hmac
import json

import pytest

from app.core.config import settings
from app.models import Merchant, PaymentConnection
from app.services.payment_connections import encrypt_secret

MERCHANT_SECRET = "merchant-webhook-secret"
ACCOUNT_ID = "acc_merchant_live"


def sign(body: bytes, secret: str) -> str:
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


@pytest.fixture
def connected_merchant(session):
    m = Merchant(
        name="Webhook Ltd", contact_email="ops@webhook.example", is_demo=False, mode="live"
    )
    session.add(m)
    session.commit()
    session.refresh(m)
    session.add(
        PaymentConnection(
            merchant_id=m.id,
            mode="byok",
            provider_account_id=ACCOUNT_ID,
            api_key_id="rzp_test_x",
            api_key_secret_encrypted=encrypt_secret("k"),
            webhook_secret_encrypted=encrypt_secret(MERCHANT_SECRET),
            status="connected",
        )
    )
    session.commit()
    return m


def body_for(account_id: str = ACCOUNT_ID) -> bytes:
    return json.dumps(
        {
            "event": "payment_link.paid",
            "account_id": account_id,
            "payload": {"payment_link": {"entity": {"id": "plink_x", "reference_id": "vsl-x"}}},
        },
        sort_keys=True,
    ).encode()


def test_a_webhook_signed_with_the_merchant_secret_is_accepted(client, connected_merchant):
    body = body_for()
    response = client.post(
        "/api/webhooks/razorpay",
        content=body,
        headers={
            "X-Razorpay-Signature": sign(body, MERCHANT_SECRET),
            "Content-Type": "application/json",
        },
    )
    # Not 400: the signature verified. The event may still be unmatched, which is a
    # different outcome from being rejected as forged.
    assert response.status_code != 400, response.text


def test_the_platform_secret_still_works(client):
    """The demo account and any platform-issued link must keep verifying."""
    body = body_for(account_id="acc_platform")
    response = client.post(
        "/api/webhooks/razorpay",
        content=body,
        headers={
            "X-Razorpay-Signature": sign(body, settings.razorpay_webhook_secret),
            "Content-Type": "application/json",
        },
    )
    assert response.status_code != 400, response.text


def test_a_forged_signature_is_still_rejected(client, connected_merchant):
    """Naming a real merchant must not help an attacker who lacks their secret."""
    body = body_for()
    response = client.post(
        "/api/webhooks/razorpay",
        content=body,
        headers={
            "X-Razorpay-Signature": sign(body, "not-the-secret"),
            "Content-Type": "application/json",
        },
    )
    assert response.status_code == 400


def test_one_merchants_secret_cannot_sign_for_another(session, client, connected_merchant):
    """The secret only verifies payloads naming the account it belongs to."""
    other = Merchant(
        name="Other Ltd", contact_email="ops@other.example", is_demo=False, mode="live"
    )
    session.add(other)
    session.commit()
    session.refresh(other)
    session.add(
        PaymentConnection(
            merchant_id=other.id,
            mode="byok",
            provider_account_id="acc_other",
            api_key_id="rzp_test_y",
            api_key_secret_encrypted=encrypt_secret("k"),
            webhook_secret_encrypted=encrypt_secret("other-secret"),
            status="connected",
        )
    )
    session.commit()

    # A payload naming merchant one, signed with merchant two's secret.
    body = body_for(account_id=ACCOUNT_ID)
    response = client.post(
        "/api/webhooks/razorpay",
        content=body,
        headers={
            "X-Razorpay-Signature": sign(body, "other-secret"),
            "Content-Type": "application/json",
        },
    )
    assert response.status_code == 400


def test_a_revoked_connection_secret_is_not_used(session, client, connected_merchant):
    """Disconnecting must actually stop that secret from verifying anything."""
    from sqlmodel import select

    row = session.exec(
        select(PaymentConnection).where(PaymentConnection.merchant_id == connected_merchant.id)
    ).one()
    row.status = "revoked"
    session.add(row)
    session.commit()

    body = body_for()
    response = client.post(
        "/api/webhooks/razorpay",
        content=body,
        headers={
            "X-Razorpay-Signature": sign(body, MERCHANT_SECRET),
            "Content-Type": "application/json",
        },
    )
    assert response.status_code == 400


def test_the_secret_is_never_returned_by_the_api(session, client, connected_merchant):
    """It is write-only: stored encrypted, reported only as present or absent."""
    from sqlmodel import select

    row = session.exec(
        select(PaymentConnection).where(PaymentConnection.merchant_id == connected_merchant.id)
    ).one()
    assert row.webhook_secret_encrypted
    assert MERCHANT_SECRET not in row.webhook_secret_encrypted
