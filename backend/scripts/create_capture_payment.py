"""Create one ₹1 Razorpay test link for recording the real checkout fallback.

Unlike ``check_razorpay``, this deliberately leaves the link open so a browser can
complete it. It refuses live keys and enables no Razorpay email/SMS notifications.
"""

import time

from app.core.config import settings
from app.integrations.razorpay_client import RazorpayClient


def main() -> None:
    if not settings.razorpay_key_id.startswith("rzp_test_"):
        raise SystemExit("capture links require an rzp_test_ key")
    reference = f"fallback-capture-{int(time.time())}"
    link = RazorpayClient().create_payment_link(
        amount_paise=100,
        reference_id=reference,
        description="Vasooli fallback recording — Razorpay test mode",
        customer_name="Vasooli Test Payer",
        customer_email="payment-test@example.com",
        customer_phone="+919845012345",
        notes={"purpose": "fallback_capture", "reference": reference},
        accept_partial=False,
    )
    print(f"link_id={link.id}")
    print(f"short_url={link.short_url}")
    print(f"reference_id={reference}")


if __name__ == "__main__":
    main()
