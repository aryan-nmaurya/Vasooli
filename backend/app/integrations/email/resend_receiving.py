"""Native Resend Receiving adapter.

Resend's ``email.received`` webhook contains routing metadata but intentionally omits
the body.  This adapter verifies the raw Svix-signed event first and then retrieves
the complete message from the authenticated Receiving API with a bounded timeout.
"""

from typing import Any

import httpx
import resend

from app.core.config import settings

RECEIVING_API_URL = "https://api.resend.com/emails/receiving"
MAX_RECEIVED_EMAIL_BYTES = 5_000_000


def verify_webhook(
    raw_body: bytes,
    *,
    svix_id: str | None,
    svix_timestamp: str | None,
    svix_signature: str | None,
) -> dict[str, Any]:
    """Verify and parse a Resend event, raising ``ValueError`` on any failure."""
    if not settings.resend_inbound_webhook_secret:
        raise ValueError("Resend inbound webhook secret is not configured")
    event = resend.Webhooks.verify(
        {
            "payload": raw_body.decode("utf-8"),
            "headers": {
                "id": svix_id or "",
                "timestamp": svix_timestamp or "",
                "signature": svix_signature or "",
            },
            "webhook_secret": settings.resend_inbound_webhook_secret,
        }
    )
    return dict(event)


async def fetch_received_email(email_id: str) -> dict[str, Any]:
    """Retrieve the body and headers omitted from the webhook notification."""
    timeout = httpx.Timeout(settings.email_provider_timeout_seconds)
    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.get(
            f"{RECEIVING_API_URL}/{email_id}",
            headers={"Authorization": f"Bearer {settings.resend_api_key}"},
        )
    response.raise_for_status()
    if len(response.content) > MAX_RECEIVED_EMAIL_BYTES:
        raise ValueError("Resend Receiving API response exceeded 5 MB")
    payload = response.json()
    if not isinstance(payload, dict):
        raise ValueError("Resend Receiving API returned a non-object response")
    return payload
