"""Batch ingestion against a real schema. Doc §3 Stage 1."""

from datetime import date, timedelta
from decimal import Decimal

import pytest
from sqlmodel import select

from app.core.clock import IST, days_overdue, today_ist
from app.core.constants import InvoiceStatus
from app.models import AuditAction, AuditLog, Customer, Invoice
from app.schemas.invoice import InvoiceIngestRow
from app.services.ingestion import ingest_batch


def row(number: str, *, overdue: int = 5, email: str = "abc@example.com", **kw) -> InvoiceIngestRow:
    due = today_ist() - timedelta(days=overdue)
    return InvoiceIngestRow.model_validate(
        {
            "invoice_number": number,
            "customer_name": "ABC Traders",
            "customer_email": email,
            "customer_phone": "+919876543210",
            "amount_inr": "42000",
            "issued_at": (due - timedelta(days=30)).isoformat(),
            "due_at": due.isoformat(),
            **kw,
        }
    )


# ---------------------------------------------------------------------------
# Idempotency — the seed script and demo reset both depend on this.
# ---------------------------------------------------------------------------


def test_reingesting_the_same_batch_changes_nothing(session):
    rows = [row("INV-1"), row("INV-2", email="b@example.com")]

    first = ingest_batch(session, rows)
    assert (first.ingested, first.skipped_duplicates) == (2, 0)

    second = ingest_batch(session, rows)
    assert (second.ingested, second.skipped_duplicates) == (0, 2)
    assert len(session.exec(select(Invoice)).all()) == 2


def test_duplicate_within_a_single_batch_is_skipped(session):
    """A file containing the same invoice twice must not abort the whole batch.

    Without an in-batch guard, both rows pass the pre-existing-numbers check and the
    second one fails on the unique index at commit, losing the other 59 invoices.
    """
    report = ingest_batch(session, [row("INV-DUP"), row("INV-DUP")])
    assert (report.ingested, report.skipped_duplicates) == (1, 1)


def test_one_bad_row_does_not_lose_the_batch(session):
    """A partial ledger is recoverable; a rejected one is not."""
    good_a, good_b = row("INV-OK-1"), row("INV-OK-2", email="b@example.com")
    bad = row("INV-BAD")
    # Bypasses the DTO's own validation to simulate a row that only the database
    # rejects — exactly the class of failure that must not take the batch down.
    object.__setattr__(bad, "amount_inr", Decimal("-100"))

    report = ingest_batch(session, [good_a, bad, good_b])
    assert report.ingested == 2
    assert report.failed == 1
    assert report.errors[0].invoice_number == "INV-BAD"
    assert {i.invoice_number for i in session.exec(select(Invoice)).all()} == {
        "INV-OK-1",
        "INV-OK-2",
    }


# ---------------------------------------------------------------------------
# Money round-trip.
# ---------------------------------------------------------------------------


def test_rupees_round_trip_through_the_database(session):
    ingest_batch(session, [row("INV-MONEY")])
    invoice = session.exec(select(Invoice)).one()
    assert invoice.amount_paise == 4_200_000  # stored as integer paise
    assert invoice.amount_display == "₹42,000"  # rendered in Indian grouping


def test_paise_amounts_survive(session):
    ingest_batch(session, [row("INV-PAISE", amount_inr="18500.50")])
    assert session.exec(select(Invoice)).one().amount_paise == 1_850_050


# ---------------------------------------------------------------------------
# Customers.
# ---------------------------------------------------------------------------


def test_customers_are_deduplicated_by_email(session):
    ingest_batch(session, [row("INV-1"), row("INV-2"), row("INV-3")])
    assert len(session.exec(select(Customer)).all()) == 1
    assert len(session.exec(select(Invoice)).all()) == 3


def test_customer_history_is_persisted_for_diagnosis(session):
    ingest_batch(
        session,
        [
            row(
                "INV-H",
                customer_total_invoices=10,
                customer_invoices_paid_late=4,
                customer_invoices_defaulted=0,
                customer_broken_promises=1,
            )
        ],
    )
    c = session.exec(select(Customer)).one()
    assert (c.total_invoices, c.invoices_paid_late, c.broken_promises) == (10, 4, 1)
    assert c.on_time_rate == pytest.approx(0.6)
    assert c.always_eventually_pays is True  # late but never defaulted -> cash-constrained


def test_phone_is_captured_for_razorpay(session):
    """Provisioning fails without a contact number, so ingestion must carry it."""
    ingest_batch(session, [row("INV-PHONE")])
    assert session.exec(select(Customer)).one().phone == "+919876543210"


# ---------------------------------------------------------------------------
# Status and dates.
# ---------------------------------------------------------------------------


def test_overdue_invoice_enters_the_chase_queue(session):
    ingest_batch(session, [row("INV-LATE", overdue=5)])
    inv = session.exec(select(Invoice)).one()
    assert inv.status == InvoiceStatus.CHASING
    assert inv.days_overdue == 5


def test_not_yet_due_invoice_waits(session):
    future = today_ist() + timedelta(days=10)
    r = InvoiceIngestRow.model_validate(
        {
            "invoice_number": "INV-FUTURE",
            "customer_name": "ABC",
            "customer_email": "abc@example.com",
            "amount_inr": "1000",
            "issued_at": date.today().isoformat(),
            "due_at": future.isoformat(),
        }
    )
    ingest_batch(session, [r])
    inv = session.exec(select(Invoice)).one()
    assert inv.status == InvoiceStatus.PENDING
    assert inv.days_overdue == 0


def test_rebase_places_invoices_on_tier_boundaries(session):
    """A CSV generated last week must still demo correctly today."""
    stale_due = today_ist() - timedelta(days=21)
    r = InvoiceIngestRow.model_validate(
        {
            "invoice_number": "INV-REBASE",
            "customer_name": "ABC",
            "customer_email": "abc@example.com",
            "amount_inr": "1000",
            "issued_at": (stale_due - timedelta(days=30)).isoformat(),
            "due_at": stale_due.isoformat(),
        }
    )
    ingest_batch(session, [r], rebase_dates=True)
    assert session.exec(select(Invoice)).one().days_overdue == 21


def test_due_date_is_anchored_to_ist_midnight(session):
    """Anchoring to UTC midnight would shift every tier boundary by 5.5 hours.

    Asserted on the instant, not on `.hour`: Postgres renders TIMESTAMPTZ in the
    session's timezone, so the returned representation depends on the client's
    configuration while the stored instant does not.
    """
    ingest_batch(session, [row("INV-TZ", overdue=3)])
    inv = session.exec(select(Invoice)).one()

    assert days_overdue(inv.due_at) == 3
    in_ist = inv.due_at.astimezone(IST)
    assert (in_ist.hour, in_ist.minute) == (0, 0)  # midnight, in India
    assert in_ist.date() == today_ist() - timedelta(days=3)


# ---------------------------------------------------------------------------
# Labels must not reach the database.
# ---------------------------------------------------------------------------


def test_eval_labels_never_reach_a_persisted_row(session):
    """End-to-end version of the DTO test: labels present in the input, absent in the DB."""
    raw = {
        "invoice_number": "INV-LABEL",
        "customer_name": "ABC",
        "customer_email": "abc@example.com",
        "amount_inr": "1000",
        "issued_at": "2026-07-01",
        "due_at": "2026-08-01",
        "ground_truth_reason": "dispute_likely",
        "ground_truth_outcome": "would_default",
    }
    ingest_batch(session, [InvoiceIngestRow.model_validate(raw)])

    inv = session.exec(select(Invoice)).one()
    assert inv.reason_category is None  # diagnosis is Phase 6's job, not ingestion's
    assert inv.reason_explanation is None

    serialized = str({k: v for k, v in inv.__dict__.items() if not k.startswith("_")})
    assert "dispute_likely" not in serialized
    assert "would_default" not in serialized


def test_dispute_note_is_carried_but_does_not_diagnose(session):
    """has_prior_dispute_note is an input signal; the classification happens later."""
    ingest_batch(session, [row("INV-DISP", has_prior_dispute_note=True)])
    inv = session.exec(select(Invoice)).one()
    assert inv.has_prior_dispute_note is True
    assert inv.reason_category is None


# ---------------------------------------------------------------------------
# Audit trail. Doc §3 Stage 6.
# ---------------------------------------------------------------------------


def test_every_ingested_invoice_is_audited(session):
    ingest_batch(session, [row("INV-A"), row("INV-B")])
    entries = session.exec(
        select(AuditLog).where(AuditLog.action == AuditAction.INVOICE_INGESTED)
    ).all()
    assert len(entries) == 2
    assert all(e.detail["amount_paise"] == 4_200_000 for e in entries)


def test_rebase_uses_the_generators_offset_not_the_files_age(session):
    """A ledger written days ago must still land on today's tier boundaries.

    Recomputing the offset from the CSV's absolute dates ages with the file: a file
    written yesterday puts every invoice one day past where it was seeded to sit, and
    the "just below the threshold" cases the demo relies on disappear.
    """
    long_stale = today_ist() - timedelta(days=99)
    r = InvoiceIngestRow.model_validate(
        {
            "invoice_number": "INV-GEN",
            "customer_name": "ABC",
            "customer_email": "abc@example.com",
            "amount_inr": "1000",
            "issued_at": (long_stale - timedelta(days=30)).isoformat(),
            "due_at": long_stale.isoformat(),
            "gen_days_overdue": 10,
        }
    )
    ingest_batch(session, [r], rebase_dates=True)
    assert session.exec(select(Invoice)).one().days_overdue == 10


def test_generator_offset_is_never_persisted(session):
    """It is bookkeeping for rebasing, not a column on the invoice."""
    ingest_batch(session, [row("INV-GEN2", overdue=5)], rebase_dates=True)
    inv = session.exec(select(Invoice)).one()
    assert not hasattr(inv, "gen_days_overdue")


def test_rebase_falls_back_when_the_offset_is_absent(session):
    """A real merchant export has no generator metadata; it must still rebase."""
    stale = today_ist() - timedelta(days=14)
    r = InvoiceIngestRow.model_validate(
        {
            "invoice_number": "INV-NOGEN",
            "customer_name": "ABC",
            "customer_email": "abc@example.com",
            "amount_inr": "1000",
            "issued_at": (stale - timedelta(days=30)).isoformat(),
            "due_at": stale.isoformat(),
        }
    )
    ingest_batch(session, [r], rebase_dates=True)
    assert session.exec(select(Invoice)).one().days_overdue == 14
