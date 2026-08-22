"""Send a signed webhook to the local app, without ngrok or the Razorpay dashboard.

    uv run python -m scripts.replay_webhook --invoice INV-2011
    uv run python -m scripts.replay_webhook --invoice INV-2011 --partial 50
    uv run python -m scripts.replay_webhook --invoice INV-2011 --times 5   # dedup check

Builds a payload in Razorpay's shape, signs it with the real webhook secret, and posts
it to the running app. That makes the whole reconciliation path — signature check,
deduplication, matching, balance update — testable in a second, instead of requiring a
tunnel and a manual test payment for every change.

This is a development tool. It proves our handling is correct; it does not prove
Razorpay sends what we think. Use a genuine test payment for that at least once.
"""

import argparse
import json
import sys
import time

import httpx
from sqlmodel import Session, select

from app.core.config import settings
from app.core.db import engine
from app.core.money import format_inr
from app.integrations.razorpay_signature import compute_signature
from app.models import Invoice, PaymentLink


def build_event(link: PaymentLink, invoice: Invoice, amount_paid: int) -> dict:
    fully_paid = amount_paid >= invoice.amount_paise
    return {
        "entity": "event",
        "event": "payment_link.paid" if fully_paid else "payment_link.partially_paid",
        "contains": ["payment_link", "payment"],
        "created_at": int(time.time()),
        "payload": {
            "payment_link": {
                "entity": {
                    "id": link.razorpay_payment_link_id,
                    "reference_id": link.reference_id,
                    "amount": invoice.amount_paise,
                    "amount_paid": amount_paid,
                    "status": "paid" if fully_paid else "partially_paid",
                    "notes": {
                        "invoice_id": str(invoice.id),
                        "invoice_number": invoice.invoice_number,
                    },
                }
            },
            "payment": {"entity": {"id": f"pay_REPLAY{int(time.time())}", "amount": amount_paid}},
        },
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--invoice", required=True, help="invoice number, e.g. INV-2011")
    ap.add_argument("--url", default="http://localhost:8000/api/webhooks/razorpay")
    ap.add_argument("--partial", type=int, help="pay this percent instead of the full amount")
    ap.add_argument("--times", type=int, default=1, help="deliver the same event N times")
    ap.add_argument("--bad-signature", action="store_true", help="sign with the wrong secret")
    args = ap.parse_args()

    with Session(engine) as session:
        invoice = session.exec(
            select(Invoice).where(Invoice.invoice_number == args.invoice)
        ).first()
        if invoice is None:
            sys.exit(f"✗ no invoice {args.invoice}")
        link = session.exec(select(PaymentLink).where(PaymentLink.invoice_id == invoice.id)).first()
        if link is None:
            sys.exit(f"✗ {args.invoice} has no payment link — run provisioning first")

        amount_paid = (
            invoice.amount_paise * args.partial // 100 if args.partial else invoice.amount_paise
        )
        payload = build_event(link, invoice, amount_paid)

    raw = json.dumps(payload).encode()
    secret = "wrong-secret" if args.bad_signature else settings.razorpay_webhook_secret
    # A fixed event id across --times, so repeated deliveries look like Razorpay's
    # at-least-once redelivery rather than distinct payments.
    event_id = f"evt_replay_{invoice.invoice_number}_{amount_paid}"

    print(f"{args.invoice}: paying {format_inr(amount_paid)} of {invoice.amount_display}")
    for i in range(args.times):
        resp = httpx.post(
            args.url,
            content=raw,
            headers={
                "X-Razorpay-Signature": compute_signature(raw, secret),
                "X-Razorpay-Event-Id": event_id,
                "Content-Type": "application/json",
            },
            timeout=20,
        )
        ctype = resp.headers.get("content-type", "")
        body = resp.json() if ctype.startswith("application/json") else resp.text
        print(f"  delivery {i + 1}: {resp.status_code}  {body}")

    with Session(engine) as session:
        fresh = session.exec(select(Invoice).where(Invoice.invoice_number == args.invoice)).one()
        print(
            f"\n  {fresh.invoice_number}: {fresh.status}"
            f"  paid {format_inr(fresh.amount_paid_paise)}"
            f"  outstanding {fresh.outstanding_display}"
        )


if __name__ == "__main__":
    main()
