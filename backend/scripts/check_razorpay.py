"""Pre-flight check for Phase 3: is Smart Collect usable on this account?

    uv run python -m scripts.check_razorpay

Creates one throwaway customer and a ₹1 virtual account in test mode, reports what
happened, then closes the account again. Run this BEFORE building provisioning —
Smart Collect is not enabled on every Razorpay account, and finding that out during
Phase 3 costs a day.

Refuses to run against live keys. Nothing here should ever touch real money.
"""

import sys
import uuid

import razorpay
from razorpay.errors import BadRequestError, GatewayError, ServerError

from app.core.config import settings

OK = "\033[32m✓\033[0m"
NO = "\033[31m✗\033[0m"
WARN = "\033[33m!\033[0m"


def fail(message: str, *, hint: str = "") -> None:
    print(f"\n{NO} {message}")
    if hint:
        print(f"\n  {hint}")
    sys.exit(1)


def main() -> None:
    print("Razorpay pre-flight check\n" + "-" * 40)

    key_id = settings.razorpay_key_id

    # --- Guard: never run this against a live account -----------------------------
    if key_id.startswith("rzp_live_"):
        fail(
            "RAZORPAY_KEY_ID is a LIVE key.",
            hint=(
                "Live virtual accounts are backed by real bank accounts and can receive\n"
                "  real money. Switch the dashboard to Test Mode, generate test keys, and\n"
                "  put the rzp_test_... key in backend/.env instead."
            ),
        )
    if not key_id.startswith("rzp_test_"):
        fail(
            f"RAZORPAY_KEY_ID does not look like a test key (got {key_id[:12]}...).",
            hint="Expected it to start with rzp_test_. Check backend/.env.",
        )
    if "PLACEHOLDER" in settings.razorpay_key_secret:
        fail(
            "RAZORPAY_KEY_SECRET is still the placeholder.",
            hint="Paste your test-mode key secret into backend/.env.",
        )
    print(f"{OK} Test-mode key detected ({key_id[:16]}...)")

    client = razorpay.Client(auth=(key_id, settings.razorpay_key_secret))

    # --- 1. Do the credentials authenticate? --------------------------------------
    try:
        client.payment.all({"count": 1})
        print(f"{OK} Credentials authenticate")
    except BadRequestError as exc:
        fail(f"Authentication failed: {exc}", hint="Key id and secret must be from the same mode.")

    # --- 2. Can we create a customer? ---------------------------------------------
    suffix = uuid.uuid4().hex[:8]
    try:
        customer = client.customer.create(
            {
                "name": "Vasooli Preflight",
                "email": f"preflight+{suffix}@example.com",
                "contact": "+919999999999",
                "fail_existing": "0",
            }
        )
        print(f"{OK} Customer created ({customer['id']})")
    except (BadRequestError, GatewayError, ServerError) as exc:
        fail(f"Customer creation failed: {exc}")

    # --- 3. The real question: Smart Collect / Virtual Accounts -------------------
    try:
        va = client.virtual_account.create(
            {
                "receivers": {"types": ["bank_account"]},
                "description": "Vasooli preflight — safe to close",
                "customer_id": customer["id"],
                "amount_expected": 100,  # ₹1 in paise
                "notes": {"preflight": suffix},
            }
        )
        print(f"{OK} Virtual account created ({va['id']})")
    except BadRequestError as exc:
        fail(
            f"Virtual account creation refused: {exc}",
            hint=(
                "This usually means Smart Collect is not enabled on the account.\n"
                "  Dashboard > Products, or contact Razorpay support to request it.\n"
                "  Phase 3 cannot proceed until this works — see the plan's fallback note."
            ),
        )
    except (GatewayError, ServerError) as exc:
        fail(f"Razorpay error: {exc}", hint="Transient? Retry once before concluding.")

    # --- 4. Inspect what a customer would actually pay into -----------------------
    receivers = va.get("receivers") or []
    if receivers:
        r = receivers[0]
        print(f"{OK} Payable account: {r.get('account_number')} / {r.get('ifsc')}")
    else:
        print(f"{WARN} VA created but no receiver returned — inspect the payload manually.")

    print(f"{OK} amount_expected echoed back: {va.get('amount_expected')} paise")
    print(f"{OK} notes echoed back: {va.get('notes')}")

    # --- 5. Clean up ---------------------------------------------------------------
    try:
        client.virtual_account.close(va["id"])
        print(f"{OK} Virtual account closed")
    except Exception as exc:  # noqa: BLE001 - cleanup failure is not a check failure
        print(f"{WARN} Could not close {va['id']}: {exc} — close it from the dashboard.")

    print("\n" + "-" * 40)
    print(f"{OK} Smart Collect is available. Phase 3 is unblocked.")
    print("\nNext: Dashboard > Settings > Webhooks — add a webhook for")
    print("  virtual_account.credited and virtual_account.created,")
    print("  and put its secret in RAZORPAY_WEBHOOK_SECRET.")


if __name__ == "__main__":
    main()
