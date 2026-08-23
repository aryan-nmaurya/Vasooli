"""Pre-flight check: can this Razorpay account create Payment Links?

    uv run python -m scripts.check_razorpay

Creates one ₹1 Payment Link in test mode, reports what came back, then cancels it.

NOTE ON SMART COLLECT: an earlier version of this script probed Smart Collect
(Virtual Accounts). Razorpay confirmed that product is **not available for this
merchant's business type**, so Vasooli collects through Payment Links instead. This
script no longer checks for it, because a check that always fails teaches nothing.

Refuses to run against live keys — a live Payment Link takes real money.
"""

import sys
import time

from app.core.config import settings
from app.integrations.razorpay_client import (
    RazorpayClient,
    RazorpayPermanentError,
    RazorpayTransientError,
)

OK = "\033[32m✓\033[0m"
NO = "\033[31m✗\033[0m"
WARN = "\033[33m!\033[0m"


def fail(message: str, *, hint: str = "") -> None:
    print(f"\n{NO} {message}")
    if hint:
        print(f"\n  {hint}")
    sys.exit(1)


def main() -> None:
    print("Razorpay pre-flight check (Payment Links)\n" + "-" * 44)

    key_id = settings.razorpay_key_id

    if key_id.startswith("rzp_live_"):
        fail(
            "RAZORPAY_KEY_ID is a LIVE key.",
            hint=(
                "A live Payment Link accepts real money. Switch the dashboard to Test\n"
                "  Mode, generate test keys, and put the rzp_test_... key in backend/.env."
            ),
        )
    if not key_id.startswith("rzp_test_"):
        fail(
            f"RAZORPAY_KEY_ID does not look like a test key (got {key_id[:12]}...).",
            hint="Expected it to start with rzp_test_. Check backend/.env.",
        )
    if "PLACEHOLDER" in settings.razorpay_key_secret:
        fail("RAZORPAY_KEY_SECRET is still the placeholder.")
    print(f"{OK} Test-mode key detected ({key_id[:16]}...)")

    client = RazorpayClient()
    reference = f"preflight-{int(time.time())}"

    try:
        link = client.create_payment_link(
            amount_paise=100,  # ₹1
            reference_id=reference,
            description="Vasooli preflight — safe to cancel",
            customer_name="Vasooli Preflight",
            customer_email="preflight@example.com",
            # Razorpay rejects contact numbers with long runs of one digit.
            customer_phone="+919845012345",
            notes={"preflight": reference},
            accept_partial=True,
        )
    except RazorpayPermanentError as exc:
        fail(
            f"Payment Link creation refused: {exc}",
            hint=(
                "Check that Payment Links are enabled on the account, and that the\n"
                "  amount is within the account's per-link limit (₹50,000 on this one)."
            ),
        )
    except RazorpayTransientError as exc:
        fail(f"Razorpay error: {exc}", hint="Transient? Retry before concluding.")

    print(f"{OK} Payment Link created ({link.id})")
    print(f"{OK} Payable URL: {link.short_url}")
    print(f"{OK} reference_id echoed back: {link.reference_id}")
    print(f"{OK} notes echoed back: {link.raw.get('notes')}")
    print(f"{OK} accept_partial: {link.raw.get('accept_partial')}")

    try:
        client.cancel_payment_link(link.id)
        print(f"{OK} Payment Link cancelled")
    except (RazorpayPermanentError, RazorpayTransientError) as exc:
        print(f"{WARN} Could not cancel {link.id}: {exc} — cancel it from the dashboard.")

    print("\n" + "-" * 44)
    print(f"{OK} Payment Links work. Collection is unblocked.")
    print("\nNext: Dashboard > Settings > Webhooks — subscribe to")
    print("  payment_link.paid and payment_link.partially_paid,")
    print("  and put the signing secret in RAZORPAY_WEBHOOK_SECRET.")


if __name__ == "__main__":
    main()
