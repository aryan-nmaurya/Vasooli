"""Exercise configured provider contracts without touching customer records.

Read-only checks run by default. ``--send-test-email`` is an explicit external side
effect and sends only to EMAIL_REDIRECT_TO. Razorpay's create/cancel probe remains in
``scripts.check_razorpay`` because its lifecycle and failure cleanup are different.
"""

import argparse
import sys
import time

import httpx
from pydantic import BaseModel

from app.ai.client import LLMClient
from app.core.config import settings
from app.integrations.email.resend_client import ResendProvider


class ProbeResponse(BaseModel):
    ok: bool


def _resend_get(path: str) -> dict:
    response = httpx.get(
        f"https://api.resend.com{path}",
        headers={"Authorization": f"Bearer {settings.resend_api_key}"},
        timeout=settings.email_provider_timeout_seconds,
    )
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict):
        raise RuntimeError(f"Resend {path} returned a non-object response")
    return payload


def verify_resend(expected_webhook_url: str | None, send_test_email: bool) -> bool:
    ok = True
    domains = _resend_get("/domains").get("data") or []
    domain = next(
        (
            item
            for item in domains
            if str(item.get("name", "")).casefold() == settings.email_reply_to_domain.casefold()
        ),
        None,
    )
    domain_ok = bool(
        domain
        and domain.get("status") == "verified"
        and (domain.get("capabilities") or {}).get("receiving") == "enabled"
    )
    print(
        "Resend receiving domain:",
        "PASS" if domain_ok else "FAIL",
        f"({settings.email_reply_to_domain})",
    )
    ok &= domain_ok

    webhooks = _resend_get("/webhooks").get("data") or []
    if expected_webhook_url:
        webhook_ok = any(
            item.get("endpoint") == expected_webhook_url
            and item.get("status") == "enabled"
            and "email.received" in (item.get("events") or [])
            for item in webhooks
        )
        print("Resend inbound webhook:", "PASS" if webhook_ok else "FAIL")
        ok &= webhook_ok
    else:
        print("Resend inbound webhook: SKIP (pass --expected-webhook-url)")

    secret_ok = settings.resend_inbound_webhook_secret.startswith("whsec_")
    print("Resend signing secret configured:", "PASS" if secret_ok else "FAIL")
    ok &= secret_ok

    if send_test_email:
        if not settings.email_redirect_to:
            raise RuntimeError("--send-test-email requires EMAIL_REDIRECT_TO")
        settings.assert_safe_to_send()
        result = ResendProvider().send(
            to=settings.email_redirect_to,
            subject="Vasooli provider verification",
            html="<p>Resend delivery verification. No customer action is required.</p>",
            text="Resend delivery verification. No customer action is required.",
            reply_to=f"verification@{settings.email_reply_to_domain}",
            headers={"X-Vasooli-Verification": "true"},
            idempotency_key=f"vasooli-provider-verification-{int(time.time()) // 3600}",
        )
        print("Resend outbound delivery:", "PASS" if result.sent else f"FAIL ({result.error})")
        ok &= result.sent
    else:
        print("Resend outbound delivery: SKIP (pass --send-test-email)")
    return ok


def verify_gemini() -> bool:
    result = LLMClient().generate_structured(
        prompt='Return exactly this JSON object: {"ok": true}',
        response_model=ProbeResponse,
        task="provider_verification",
    )
    passed = result.ok and result.value is not None and result.value.ok is True
    print(
        "Gemini structured generation:",
        "PASS" if passed else f"FAIL ({result.error or 'unexpected response'})",
        f"models={','.join(result.attempts) or 'none'}",
    )
    return passed


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expected-webhook-url")
    parser.add_argument("--send-test-email", action="store_true")
    parser.add_argument("--skip-resend", action="store_true")
    parser.add_argument("--skip-gemini", action="store_true")
    args = parser.parse_args()

    checks: list[bool] = []
    if not args.skip_resend:
        checks.append(verify_resend(args.expected_webhook_url, args.send_test_email))
    if not args.skip_gemini:
        checks.append(verify_gemini())
    if not checks:
        raise SystemExit("No checks selected")
    raise SystemExit(0 if all(checks) else 1)


if __name__ == "__main__":
    try:
        main()
    except (httpx.HTTPError, RuntimeError) as exc:
        print(f"provider verification failed: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
