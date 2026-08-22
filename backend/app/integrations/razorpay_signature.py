"""Webhook signature verification. Doc §6.

Razorpay signs each webhook with an HMAC-SHA256 of the request body, keyed on the
webhook secret. Without this check, anyone who learns the endpoint URL can post a
"payment received" event and mark invoices paid.
"""

import hashlib
import hmac

from app.core.config import settings


def compute_signature(raw_body: bytes, secret: str) -> str:
    return hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()


def verify_signature(raw_body: bytes, signature: str | None, secret: str | None = None) -> bool:
    """Check a webhook signature against the raw request body.

    Two details that are easy to get wrong and silently break this:

    * The body must be the RAW bytes as received. Parsing the JSON and re-serializing
      it changes key order and whitespace, producing a different digest and rejecting
      every genuine webhook.
    * The comparison uses `compare_digest`, not `==`. A plain string comparison exits
      at the first differing byte, and the timing difference leaks the expected
      signature one byte at a time.
    """
    if not signature:
        return False
    expected = compute_signature(raw_body, secret or settings.razorpay_webhook_secret)
    return hmac.compare_digest(expected, signature)
