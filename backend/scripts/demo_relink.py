"""Give every demo invoice a fresh, unpaid payment link.

    uv run python -m scripts.demo_relink

Run this after `demo_reset --no-provision`, and again between recording takes.

Why it exists
-------------
`reference_id_for` gives a demo invoice the stable reference `vsl-<invoice number>`,
and `_create_or_adopt` treats Razorpay's duplicate-reference rejection as a signal to
ADOPT the link that already exists. Both are right for production: a customer must
never be handed two places to pay one invoice.

For a demo they combine badly. `demo_reset` truncates the ledger but cannot untake a
payment, so re-provisioning adopts links that were paid in an earlier take. The next
`payment_link_sync` reads them as paid and the invoices flip straight back to
`recovered` — before a single reminder has been sent. Observed as
`{"checked": 8, "recovered": 4}`.

A per-run nonce sidesteps it. The references are unique to this run, so Razorpay has
nothing to adopt and every link comes back `created`. Nothing about how real merchants
are provisioned changes; this is demo tooling only.
"""

import uuid

from sqlmodel import Session, select

from app.core.db import engine
from app.integrations.razorpay_client import RazorpayClient
from app.models import Customer, Invoice, PaymentLink


def main() -> int:
    nonce = uuid.uuid4().hex[:6]
    client = RazorpayClient()
    # Razorpay rate-limits link creation; the reset script uses the same spacing.
    client._min_interval = 11.0
    print(f"Fresh links under nonce {nonce}\n")

    with Session(engine) as session:
        invoices = sorted(session.exec(select(Invoice)).all(), key=lambda i: i.invoice_number)
        for invoice in invoices:
            existing = session.exec(
                select(PaymentLink).where(PaymentLink.invoice_id == invoice.id)
            ).first()
            if existing is not None:
                print(f"  {invoice.invoice_number}  already linked, skipped")
                continue

            customer = session.get(Customer, invoice.customer_id)
            reference = f"vsl-{invoice.invoice_number}-{nonce}"
            result = client.create_payment_link(
                amount_paise=invoice.amount_paise,
                reference_id=reference,
                description=f"Invoice {invoice.invoice_number}",
                customer_name=customer.name,
                customer_email=customer.email,
                customer_phone=getattr(customer, "phone", None),
                notes={
                    "invoice_number": invoice.invoice_number,
                    "invoice_id": str(invoice.id),
                },
            )
            session.add(
                PaymentLink(
                    invoice_id=invoice.id,
                    razorpay_payment_link_id=result.id,
                    reference_id=reference,
                    short_url=result.short_url,
                    status=result.status,
                    amount_expected_paise=invoice.amount_paise,
                    amount_paid_paise=result.amount_paid_paise,
                    accept_partial=True,
                    raw_response=result.raw,
                )
            )
            # Committed per invoice: a rate-limit failure halfway through should keep
            # the links already created rather than orphaning them at the provider.
            session.commit()
            print(f"  {invoice.invoice_number}  {result.status:<8} {result.short_url}")

    print("\nEvery invoice has an unpaid link. Ledger is ready to record.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
