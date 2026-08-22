"""The database-level guarantees Phases 4-8 rely on.

Each of these is a rule the application also enforces in Python. They are duplicated
in the schema because the Python check protects one code path, while the constraint
protects the table — and under a retried webhook, an overlapping scheduler run, or a
hand-typed psql statement, the table is the only thing still standing.
"""

import uuid
from datetime import UTC, date, datetime

import pytest
from sqlalchemy.exc import IntegrityError, InternalError
from sqlmodel import select

from app.core.constants import PromiseStatus
from app.models import AuditLog, Invoice, Promise, ReconciliationEvent, Reminder


def _event(event_id: str = "evt_test_001") -> ReconciliationEvent:
    return ReconciliationEvent(
        provider_event_id=event_id,
        event_type="virtual_account.credited",
        raw_payload={"event": "virtual_account.credited"},
        signature_verified=True,
    )


# ---------------------------------------------------------------------------
# Idempotency. Doc §6.
# ---------------------------------------------------------------------------


def test_duplicate_webhook_event_id_is_rejected(session):
    """The dedup key that stops a retried webhook double-counting revenue.

    Razorpay delivers at-least-once. Phase 4's handler inserts this row before doing
    any work, so this constraint IS the deduplication — not an in-memory set, which
    forgets on restart and is not shared across workers.
    """
    session.add(_event())
    session.commit()

    session.add(_event())
    with pytest.raises(IntegrityError):
        session.commit()


def test_distinct_event_ids_both_persist(session):
    session.add(_event("evt_a"))
    session.add(_event("evt_b"))
    session.commit()
    assert len(session.exec(select(ReconciliationEvent)).all()) == 2


# ---------------------------------------------------------------------------
# The reminder cap. Doc §3 Stage 3.
# ---------------------------------------------------------------------------


def test_reminder_cap_is_enforced_by_the_database(session, invoice):
    """ "Maximum of 3 automated reminders" is a compliance promise, not a preference."""
    invoice.reminders_sent = 4
    session.add(invoice)
    with pytest.raises(IntegrityError):
        session.commit()


def test_reminder_cap_allows_exactly_three(session, invoice):
    invoice.reminders_sent = 3
    session.add(invoice)
    session.commit()
    assert invoice.reminders_sent == 3


def test_same_tier_cannot_be_sent_twice(session, invoice):
    """Guards against an overlapping scheduler cycle re-sending Tier 2."""
    for _ in range(2):
        session.add(Reminder(invoice_id=invoice.id, tier=2, tone="firm", subject="s", body="b"))
    with pytest.raises(IntegrityError):
        session.commit()


def test_different_tiers_coexist(session, invoice):
    session.add(Reminder(invoice_id=invoice.id, tier=1, tone="polite", subject="s", body="b"))
    session.add(Reminder(invoice_id=invoice.id, tier=2, tone="firm", subject="s", body="b"))
    session.commit()
    assert len(session.exec(select(Reminder)).all()) == 2


# ---------------------------------------------------------------------------
# Promises. Doc §3 Stage 4.
# ---------------------------------------------------------------------------


def _promise(invoice_id, status=PromiseStatus.ACTIVE, day=15) -> Promise:
    return Promise(
        invoice_id=invoice_id,
        promised_date=date(2026, 8, day),
        source_message_excerpt="I'll clear this by the 15th",
        extraction_confidence=0.9,
        status=status,
        tier_at_pause=2,
    )


def test_only_one_active_promise_per_invoice(session, invoice):
    """Two simultaneous active promises would make the pause window ambiguous."""
    session.add(_promise(invoice.id))
    session.commit()

    session.add(_promise(invoice.id, day=20))
    with pytest.raises(IntegrityError):
        session.commit()


def test_resolved_promises_accumulate_as_history(session, invoice):
    """The partial index must not forbid a repeat offender's second promise.

    A plain UNIQUE(invoice_id) would block exactly the case the broken-promise metric
    exists to measure.
    """
    session.add(_promise(invoice.id, status=PromiseStatus.BROKEN, day=10))
    session.add(_promise(invoice.id, status=PromiseStatus.BROKEN, day=12))
    session.add(_promise(invoice.id, status=PromiseStatus.ACTIVE, day=20))
    session.commit()
    assert len(session.exec(select(Promise)).all()) == 3


def test_extraction_confidence_must_be_a_probability(session, invoice):
    p = _promise(invoice.id)
    p.extraction_confidence = 1.5
    session.add(p)
    with pytest.raises(IntegrityError):
        session.commit()


# ---------------------------------------------------------------------------
# Money.
# ---------------------------------------------------------------------------


def test_amount_paid_cannot_go_negative(session, invoice):
    """Reconciliation only ever adds; a negative balance means a bug upstream."""
    invoice.amount_paid_paise = -1
    session.add(invoice)
    with pytest.raises(IntegrityError):
        session.commit()


def test_invoice_amount_must_be_positive(session, merchant, customer):
    session.add(
        Invoice(
            merchant_id=merchant.id,
            customer_id=customer.id,
            invoice_number="INV-ZERO",
            amount_paise=0,
            issued_at=datetime(2026, 7, 1, tzinfo=UTC),
            due_at=datetime(2026, 8, 1, tzinfo=UTC),
        )
    )
    with pytest.raises(IntegrityError):
        session.commit()


def test_due_date_cannot_precede_issue_date(session, merchant, customer):
    session.add(
        Invoice(
            merchant_id=merchant.id,
            customer_id=customer.id,
            invoice_number="INV-BACKWARDS",
            amount_paise=1000,
            issued_at=datetime(2026, 8, 1, tzinfo=UTC),
            due_at=datetime(2026, 7, 1, tzinfo=UTC),
        )
    )
    with pytest.raises(IntegrityError):
        session.commit()


def test_invoice_number_is_unique(session, merchant, customer, invoice):
    """Batch ingestion is idempotent on this column (Phase 2)."""
    session.add(
        Invoice(
            merchant_id=merchant.id,
            customer_id=customer.id,
            invoice_number=invoice.invoice_number,
            amount_paise=1000,
            issued_at=datetime(2026, 7, 1, tzinfo=UTC),
            due_at=datetime(2026, 8, 1, tzinfo=UTC),
        )
    )
    with pytest.raises(IntegrityError):
        session.commit()


# ---------------------------------------------------------------------------
# Customer history coherence. Doc §3 Stage 2.
# ---------------------------------------------------------------------------


def test_late_count_cannot_exceed_total_invoices(session, customer):
    """A contradictory history would silently corrupt every future diagnosis."""
    customer.invoices_paid_late = 99
    session.add(customer)
    with pytest.raises(IntegrityError):
        session.commit()


# ---------------------------------------------------------------------------
# Append-only audit log. Doc §3 Stage 6.
# ---------------------------------------------------------------------------


def test_audit_log_rows_cannot_be_updated(session, invoice):
    """The demo presents this log as evidence; editable evidence is not evidence."""
    entry = AuditLog(invoice_id=invoice.id, actor="policy", action="policy_evaluated", detail={})
    session.add(entry)
    session.commit()

    entry.action = "tampered"
    session.add(entry)
    with pytest.raises((IntegrityError, InternalError), match="append-only"):
        session.commit()


def test_audit_log_rows_cannot_be_deleted(session, invoice):
    entry = AuditLog(invoice_id=invoice.id, actor="ai", action="diagnosed", detail={})
    session.add(entry)
    session.commit()

    session.delete(entry)
    with pytest.raises((IntegrityError, InternalError), match="append-only"):
        session.commit()


def test_audit_log_accepts_inserts(session, invoice):
    for action in ("diagnosed", "reminder_sent", "payment_reconciled"):
        session.add(AuditLog(invoice_id=invoice.id, actor="system", action=action, detail={"n": 1}))
    session.commit()
    assert len(session.exec(select(AuditLog)).all()) == 3


def test_audit_log_survives_an_invoiceless_event(session):
    """An unmatched payment belongs to no invoice — and is worth keeping."""
    session.add(
        AuditLog(
            invoice_id=None,
            actor="razorpay",
            action="reconciliation_unmatched",
            detail={"event_id": "evt_x"},
        )
    )
    session.commit()
    assert session.exec(select(AuditLog)).one().invoice_id is None


# ---------------------------------------------------------------------------
# Virtual accounts. Doc §4.
# ---------------------------------------------------------------------------


def test_one_virtual_account_per_invoice(session, invoice):
    """A retried provisioning run must not create a second payable account."""
    from app.models import VirtualAccount

    for i in range(2):
        session.add(
            VirtualAccount(
                invoice_id=invoice.id,
                razorpay_va_id=f"va_{uuid.uuid4().hex[:10]}_{i}",
                razorpay_customer_id="cust_1",
                amount_expected_paise=invoice.amount_paise,
            )
        )
    with pytest.raises(IntegrityError):
        session.commit()
