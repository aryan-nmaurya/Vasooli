"""Force a reconciliation failure, for the demo's failure-and-recovery beat.

    uv run python -m scripts.demo_failure --invoice INV-3006

Stores a signature-verified webhook whose payload cannot be matched to any invoice,
which is a genuine unmatched-payment exception — the same state a real mismatched
payment would produce. Nothing about the failure is faked: the event is real, the
signature is real, and the repair path is the production one.

DEMO SIMULATION: the webhook itself is generated locally rather than sent by Razorpay.
"""

import argparse
import json
import time

import httpx
from sqlmodel import Session, select

from app.core.config import settings
from app.core.db import engine
from app.integrations.razorpay_signature import compute_signature
from app.models import Invoice


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--invoice", default="INV-3006")
    ap.add_argument("--url", default="http://localhost:8000/api/webhooks/razorpay")
    args = ap.parse_args()

    with Session(engine) as session:
        invoice = session.exec(
            select(Invoice).where(Invoice.invoice_number == args.invoice)
        ).first()
        amount = invoice.amount_paise if invoice else 2_200_000

    event_id = f"evt_broken_{int(time.time())}"
    payload = {
        "entity": "event",
        "event": "payment_link.paid",
        "payload": {
            "payment_link": {
                "entity": {
                    # Deliberately unknown everywhere: no link, no notes, no reference.
                    "id": "plink_UNKNOWN_TO_VASOOLI",
                    "reference_id": "unknown-reference",
                    "amount": amount,
                    "amount_paid": amount,
                    "status": "paid",
                    "notes": {},
                }
            }
        },
    }

    raw = json.dumps(payload).encode()
    resp = httpx.post(
        args.url,
        content=raw,
        headers={
            "X-Razorpay-Signature": compute_signature(raw, settings.razorpay_webhook_secret),
            "X-Razorpay-Event-Id": event_id,
            "Content-Type": "application/json",
        },
        timeout=20,
    )

    print(f"  posted {event_id} -> {resp.status_code} {resp.text.strip()[:120]}")
    print("\n  The payment is signature-verified and stored, but matches no invoice.")
    print("  Open the dashboard -> Reconciliation Exceptions to see and retry it.")


if __name__ == "__main__":
    main()
