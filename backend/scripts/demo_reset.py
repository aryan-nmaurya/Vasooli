"""Reset the ledger to a curated demo state.

    uv run python -m scripts.demo_reset
    uv run python -m scripts.demo_reset --no-provision   # skip Razorpay calls

Eight invoices, each chosen to make one point on stage. Sixty is impressive in a
screenshot and unusable in a walkthrough — you cannot talk about a queue you have to
scroll, and provisioning sixty payment links takes ten minutes of Razorpay rate limit
for fifty-two links nobody will open.

Every invoice here gets a real payment link, so any of them can be paid live.
"""

import argparse
import sys
import time
from dataclasses import dataclass

from sqlmodel import Session, select

from app.core.clock import today_ist
from app.core.money import format_inr
from app.integrations.razorpay_client import RazorpayClient
from app.models import Invoice, PaymentLink
from app.schemas.invoice import InvoiceIngestRow
from app.services.ingestion import ingest_batch
from app.services.provisioning import provision_batch

TABLES = (
    "audit_logs",
    "reconciliation_events",
    "promises",
    "reminders",
    "payment_links",
    "invoices",
    "customers",
    "merchants",
)


@dataclass(frozen=True)
class DemoInvoice:
    number: str
    customer: str
    email: str
    phone: str
    amount_inr: int
    days_overdue: int
    #: Customer history, which is what the classifier actually reads.
    total_invoices: int
    paid_late: int
    defaulted: int
    broken_promises: int
    avg_invoice_inr: int
    dispute_note: bool
    #: What this invoice demonstrates. Printed after seeding as a crib sheet.
    demonstrates: str


DEMO_SET = [
    DemoInvoice(
        "INV-3001",
        "Nova Retail",
        "accounts@nova-retail.example.com",
        "+919845012345",
        12_500,
        2,
        total_invoices=14,
        paid_late=0,
        defaulted=0,
        broken_promises=0,
        avg_invoice_inr=11_000,
        dispute_note=False,
        demonstrates="Not yet due — 2 days overdue, below the Tier 1 threshold of 3.",
    ),
    DemoInvoice(
        "INV-3002",
        "Sunrise Textiles",
        "finance@sunrise-textiles.example.com",
        "+919812345678",
        18_000,
        3,
        total_invoices=9,
        paid_late=0,
        defaulted=0,
        broken_promises=0,
        avg_invoice_inr=17_000,
        dispute_note=False,
        demonstrates="Oversight — clean payer, Tier 1 polite reminder fires today.",
    ),
    DemoInvoice(
        "INV-3003",
        "ABC Traders",
        "rahul@abc-traders.example.com",
        "+919823456789",
        34_000,
        10,
        total_invoices=22,
        paid_late=6,
        defaulted=0,
        broken_promises=1,
        avg_invoice_inr=13_000,
        dispute_note=False,
        demonstrates="Cash-constrained — Tier 2 firm reminder. Best invoice for the promise demo.",
    ),
    DemoInvoice(
        "INV-3004",
        "Deccan Logistics",
        "ap@deccan-logistics.example.com",
        "+919834567890",
        27_500,
        21,
        total_invoices=11,
        paid_late=5,
        defaulted=2,
        broken_promises=3,
        avg_invoice_inr=24_000,
        dispute_note=False,
        demonstrates="Unresponsive — Tier 3 final notice AND automatic human handoff.",
    ),
    DemoInvoice(
        "INV-3005",
        "Kiran & Co",
        "kiran@kiran-and-co.example.com",
        "+919845678901",
        41_000,
        12,
        total_invoices=8,
        paid_late=1,
        defaulted=0,
        broken_promises=0,
        avg_invoice_inr=20_000,
        dispute_note=True,
        demonstrates="Dispute-likely — never enters the cadence, goes straight to a human.",
    ),
    DemoInvoice(
        "INV-3006",
        "Meridian Packaging",
        "billing@meridian-pack.example.com",
        "+919856789012",
        22_000,
        15,
        total_invoices=17,
        paid_late=4,
        defaulted=0,
        broken_promises=0,
        avg_invoice_inr=15_000,
        dispute_note=False,
        demonstrates="RESERVED for the live payment. Do not touch during setup.",
    ),
    DemoInvoice(
        "INV-3007",
        "Vega Industries",
        "accounts@vega-industries.example.com",
        "+919867890123",
        16_500,
        30,
        total_invoices=13,
        paid_late=7,
        defaulted=0,
        broken_promises=2,
        avg_invoice_inr=9_000,
        dispute_note=False,
        demonstrates="Repeat promise-breaker — history feeds the diagnosis.",
    ),
    DemoInvoice(
        "INV-3008",
        "Lotus Supplies",
        "ap@lotus-supplies.example.com",
        "+919878901234",
        9_500,
        8,
        total_invoices=6,
        paid_late=1,
        defaulted=0,
        broken_promises=0,
        avg_invoice_inr=8_500,
        dispute_note=False,
        demonstrates="Small invoice — pay this one to move the recovered counter early.",
    ),
]


def to_row(item: DemoInvoice) -> InvoiceIngestRow:
    due = today_ist()
    return InvoiceIngestRow.model_validate(
        {
            "invoice_number": item.number,
            "customer_name": item.customer,
            "customer_email": item.email,
            "customer_phone": item.phone,
            "amount_inr": str(item.amount_inr),
            "issued_at": str(due),
            "due_at": str(due),
            "terms_days": 30,
            "customer_total_invoices": item.total_invoices,
            "customer_invoices_paid_late": item.paid_late,
            "customer_invoices_defaulted": item.defaulted,
            "customer_broken_promises": item.broken_promises,
            "customer_avg_invoice_inr": str(item.avg_invoice_inr),
            "has_prior_dispute_note": item.dispute_note,
            # Rebasing uses this, so the tier boundaries land correctly today.
            "gen_days_overdue": item.days_overdue,
        }
    )


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--no-provision", action="store_true")
    args = ap.parse_args()

    from sqlalchemy import text

    from app.core.db import engine

    with Session(engine) as session:
        session.exec(text(f"TRUNCATE {', '.join(TABLES)} RESTART IDENTITY CASCADE"))
        session.commit()
        print(f"Wiped the ledger. Seeding {len(DEMO_SET)} demo invoices…\n")

        report = ingest_batch(session, [to_row(i) for i in DEMO_SET], rebase_dates=True)
        if report.failed:
            for err in report.errors:
                print(f"  ✗ {err.invoice_number}: {err.error}")
            sys.exit(1)

    if not args.no_provision:
        # Razorpay allows roughly six payment links a minute in test mode. Eight takes
        # about ninety seconds; sixty took ten minutes, which is why this set is eight.
        # Razorpay test mode allows roughly six payment links a minute. The client's
        # default 1.5s pacing is fine for a retry of one or two, but a batch of eight
        # trips the limit part way through — so this run is deliberately slower.
        print("Creating payment links (paced for Razorpay's rate limit, ~90s)…")
        started = time.time()
        client = RazorpayClient()
        client._min_interval = 11.0
        with Session(engine) as session:
            result = provision_batch(session, client=client)
        print(
            f"  {result['provisioned']} created, {len(result['failed'])} failed "
            f"({time.time() - started:.0f}s)\n"
        )
        for failure in result["failed"]:
            print(f"  ✗ {failure['invoice_number']}: {failure['error'][:90]}")

    with Session(engine) as session:
        links = {pl.invoice_id: pl for pl in session.exec(select(PaymentLink)).all()}
        invoices = {i.invoice_number: i for i in session.exec(select(Invoice)).all()}

    print("Demo ledger")
    print("=" * 96)
    for item in DEMO_SET:
        invoice = invoices.get(item.number)
        if invoice is None:
            continue
        link = links.get(invoice.id)
        print(
            f"{item.number}  {item.customer:<20} {format_inr(invoice.amount_paise):>9}  "
            f"{invoice.days_overdue:>2}d overdue"
        )
        print(f"          {item.demonstrates}")
        print(f"          {link.short_url if link else '(no payment link)'}\n")

    print("Next: open the dashboard and press “Run recovery cycle”.")


if __name__ == "__main__":
    main()
