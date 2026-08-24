"""Produce a reconciliation exception for the demo's failure-and-recovery beat.

    uv run python -m scripts.demo_failure --invoice INV-3006     # break it
    uv run python -m scripts.demo_failure --repair               # then fix it

Two honest scenarios, because they are genuinely different problems:

**recoverable** (default) — a payment arrives referencing a link our database does not
know about, so it cannot be matched. The operator identifies it, the mapping is
restored, and a retry reconciles it. This is the FAILED → EXCEPTION QUEUE → RETRY →
RECOVERED path.

**unmatched** (`--kind unmatched`) — a payment that matches nothing at all. Retrying
cannot help; a human has to work out what it was for. Vasooli marks it terminal rather
than retrying forever, which is the correct behaviour and worth showing too.

DEMO SIMULATION: the webhook is generated and signed locally rather than sent by
Razorpay. Everything after it arrives is the production path — signature verification,
persistence, deduplication, matching, and reconciliation.
"""

import argparse
import json
import sys
import time

import httpx
from sqlmodel import Session, select

from app.core.config import settings
from app.core.db import engine
from app.integrations.razorpay_signature import compute_signature
from app.models import Invoice, PaymentLink

#: What the link id is renamed to while it is "unknown" to us.
SHADOW_PREFIX = "shadow_"


def post_webhook(url: str, payload: dict, event_id: str) -> httpx.Response:
    raw = json.dumps(payload).encode()
    return httpx.post(
        url,
        content=raw,
        headers={
            "X-Razorpay-Signature": compute_signature(raw, settings.razorpay_webhook_secret),
            "X-Razorpay-Event-Id": event_id,
            "Content-Type": "application/json",
        },
        timeout=20,
    )


def repair() -> None:
    """Restore any shadowed link mapping, so a retry can now match."""
    with Session(engine) as session:
        shadowed = [
            link
            for link in session.exec(select(PaymentLink)).all()
            if link.razorpay_payment_link_id.startswith(SHADOW_PREFIX)
        ]
        if not shadowed:
            print("  Nothing to repair.")
            return
        for link in shadowed:
            original = link.razorpay_payment_link_id[len(SHADOW_PREFIX) :]
            link.razorpay_payment_link_id = original
            session.add(link)
            print(f"  Restored mapping for {original}")
        session.commit()

    print("\n  Now press Retry in the dashboard's Reconciliation Exceptions panel.")
    print("  The same event reconciles, and the invoice moves to recovered.")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--invoice", default="INV-3006")
    ap.add_argument("--kind", choices=["recoverable", "unmatched"], default="recoverable")
    ap.add_argument("--repair", action="store_true", help="restore the mapping, then retry")
    ap.add_argument("--url", default="http://localhost:8000/api/webhooks/razorpay")
    args = ap.parse_args()

    if args.repair:
        repair()
        return

    event_id = f"evt_demo_{int(time.time())}"

    with Session(engine) as session:
        invoice = session.exec(
            select(Invoice).where(Invoice.invoice_number == args.invoice)
        ).first()
        if invoice is None:
            sys.exit(f"✗ no invoice {args.invoice}. Run: uv run python -m scripts.demo_reset")

        link = session.exec(select(PaymentLink).where(PaymentLink.invoice_id == invoice.id)).first()
        if link is None:
            sys.exit(f"✗ {args.invoice} has no payment link.")

        real_link_id = link.razorpay_payment_link_id

        if args.kind == "recoverable":
            # Hide our mapping, so the incoming webhook matches nothing. This is what a
            # genuine ordering problem looks like: the payment arrives before we have
            # recorded the link it belongs to.
            link.razorpay_payment_link_id = f"{SHADOW_PREFIX}{real_link_id}"
            session.add(link)
            session.commit()

        entity = {
            "id": real_link_id if args.kind == "recoverable" else "plink_UNKNOWN_TO_VASOOLI",
            "reference_id": "" if args.kind == "recoverable" else "unknown-reference",
            "amount": invoice.amount_paise,
            "amount_paid": invoice.amount_paise,
            "status": "paid",
            # Deliberately empty: notes would match the invoice directly and there
            # would be no failure to demonstrate.
            "notes": {},
        }
        amount_display = invoice.amount_display

    payload = {
        "entity": "event",
        "event": "payment_link.paid",
        "payload": {"payment_link": {"entity": entity}},
    }
    response = post_webhook(args.url, payload, event_id)

    print(f"  Posted {event_id} -> {response.status_code} {response.text.strip()[:100]}")
    print(f"\n  A {amount_display} payment was received, verified, and stored — but could")
    print("  not be matched to an invoice, so it is sitting in the exceptions queue.")

    if args.kind == "recoverable":
        print("\n  Open the dashboard -> Operational exceptions to show it.")
        print("  Then repair the mapping and retry:")
        print("    uv run python -m scripts.demo_failure --repair")
    else:
        print("\n  This one cannot be fixed by retrying — a human has to identify it.")
        print("  Vasooli marks it terminal rather than retrying forever.")


if __name__ == "__main__":
    main()
