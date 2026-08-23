"""The ingestion DTO is the boundary that keeps eval labels out of the system."""

from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.schemas.invoice import InvoiceIngestRow

BASE = {
    "invoice_number": "INV-2291",
    "customer_name": "ABC Traders",
    "customer_email": "abc@example.com",
    "amount_inr": "42000",
    "issued_at": "2026-07-01",
    "due_at": "2026-08-01",
}


def test_ground_truth_columns_are_dropped_at_the_boundary():
    """The single most important property in Phase 2.

    A classifier scored against a label it was shown measures nothing. The CSV carries
    labels for the Phase 11 eval; this parse step is where they stop.
    """
    row = InvoiceIngestRow.model_validate(
        {**BASE, "ground_truth_reason": "oversight", "ground_truth_outcome": "needs_one_nudge"}
    )
    dumped = row.model_dump()
    assert not [k for k in dumped if "ground_truth" in k]
    assert not hasattr(row, "ground_truth_reason")


def test_generator_offset_is_accepted_but_is_not_an_answer():
    """`gen_days_overdue` is read, unlike the ground-truth columns.

    It says how many days overdue the row was seeded to be, which the seeder needs in
    order to rebase a stale ledger onto today's tier boundaries. It is bookkeeping
    about dates, not a label about the customer, so it cannot leak a diagnosis the
    classifier is supposed to work out for itself. It is never written to the invoice.
    """
    row = InvoiceIngestRow.model_validate({**BASE, "gen_days_overdue": "3"})
    assert row.gen_days_overdue == 3


def test_unknown_columns_are_still_dropped():
    row = InvoiceIngestRow.model_validate({**BASE, "internal_score": "0.9", "notes": "x"})
    dumped = row.model_dump()
    assert "internal_score" not in dumped
    assert "notes" not in dumped


def test_amount_parses_as_decimal_not_float():
    """Float parsing would lose precision before app.core.money could refuse it."""
    row = InvoiceIngestRow.model_validate({**BASE, "amount_inr": "18500.50"})
    assert isinstance(row.amount_inr, Decimal)
    assert row.amount_paise == 1_850_050


def test_amount_round_trips_to_paise():
    assert InvoiceIngestRow.model_validate(BASE).amount_paise == 4_200_000


def test_zero_amount_is_rejected():
    with pytest.raises(ValidationError):
        InvoiceIngestRow.model_validate({**BASE, "amount_inr": "0"})


def test_due_before_issued_is_rejected():
    with pytest.raises(ValidationError, match="precedes"):
        InvoiceIngestRow.model_validate({**BASE, "issued_at": "2026-08-01", "due_at": "2026-07-01"})


def test_contradictory_history_is_rejected():
    """A customer cannot have paid 9 invoices late out of 5 total.

    Caught here as well as by the database CHECK, because a bad row should be reported
    against its invoice number rather than aborting the batch with an IntegrityError.
    """
    with pytest.raises(ValidationError, match="exceeds total"):
        InvoiceIngestRow.model_validate(
            {**BASE, "customer_total_invoices": 5, "customer_invoices_paid_late": 9}
        )


@pytest.mark.parametrize(
    ("given", "expected"),
    [
        ("true", True),
        ("True", True),
        ("1", True),
        ("yes", True),
        ("false", False),
        ("", False),
        ("no", False),
    ],
)
def test_csv_boolean_spellings(given, expected):
    """CSV has no boolean type; a spreadsheet round-trip produces all of these."""
    row = InvoiceIngestRow.model_validate({**BASE, "has_prior_dispute_note": given})
    assert row.has_prior_dispute_note is expected


def test_blank_phone_becomes_none():
    assert InvoiceIngestRow.model_validate({**BASE, "customer_phone": ""}).customer_phone is None


def test_invalid_email_is_rejected():
    with pytest.raises(ValidationError):
        InvoiceIngestRow.model_validate({**BASE, "customer_email": "not-an-email"})
