"""Payment link provisioning. Doc §3 Stage 1, §4.

Razorpay is faked at the client boundary — the seam that exists precisely so these
paths can be exercised without network, rate limits, or leftover objects in a real
account. Everything above the fake is production code.
"""

import uuid

import pytest
from sqlmodel import select

from app.integrations.razorpay_client import (
    PaymentLinkResult,
    RazorpayPermanentError,
    RazorpayTransientError,
)
from app.models import AuditAction, AuditLog, Invoice, PaymentLink
from app.services.provisioning import (
    ProvisioningError,
    provision_batch,
    provision_for_invoice,
    reference_id_for,
)


class FakeRazorpay:
    """Records calls and hands back Razorpay-shaped payloads."""

    def __init__(self, *, fail_with: Exception | None = None):
        self.calls: list[dict] = []
        self.fail_with = fail_with

    def create_payment_link(self, **kw) -> PaymentLinkResult:
        self.calls.append(kw)
        if self.fail_with:
            raise self.fail_with
        n = len(self.calls)
        return PaymentLinkResult.from_payload(
            {
                "id": f"plink_FAKE{n:04d}",
                "short_url": f"https://rzp.io/rzp/fake{n}",
                "reference_id": kw["reference_id"],
                "status": "created",
                "amount": kw["amount_paise"],
                "amount_paid": 0,
                "notes": kw["notes"],
            }
        )


@pytest.fixture
def fake():
    return FakeRazorpay()


# ---------------------------------------------------------------------------
# Idempotency — the property that keeps a customer from getting two bills.
# ---------------------------------------------------------------------------


def test_provisioning_twice_reuses_the_same_link(session, invoice, fake):
    first = provision_for_invoice(session, invoice.id, client=fake)
    second = provision_for_invoice(session, invoice.id, client=fake)

    assert first.razorpay_payment_link_id == second.razorpay_payment_link_id
    assert len(fake.calls) == 1, "Razorpay must not be called again once a link exists"
    assert len(session.exec(select(PaymentLink)).all()) == 1


def test_reference_id_is_stable_for_an_invoice(invoice):
    """A retry after a timeout Razorpay actually processed must collide, not duplicate."""
    assert reference_id_for(invoice) == reference_id_for(invoice)
    assert invoice.invoice_number in reference_id_for(invoice)


def test_second_link_for_one_invoice_is_impossible(session, invoice, fake):
    """Belt and braces: the database refuses even if the service logic is bypassed."""
    from sqlalchemy.exc import IntegrityError

    provision_for_invoice(session, invoice.id, client=fake)
    session.add(
        PaymentLink(
            invoice_id=invoice.id,
            razorpay_payment_link_id="plink_SNEAKY",
            reference_id="sneaky",
            short_url="https://example.com",
            amount_expected_paise=1000,
        )
    )
    with pytest.raises(IntegrityError):
        session.commit()


# ---------------------------------------------------------------------------
# What gets sent to Razorpay.
# ---------------------------------------------------------------------------


def test_notes_carry_the_invoice_id_for_reconciliation(session, invoice, fake):
    """Phase 4 matches on this. Matching on amount would be ambiguous."""
    provision_for_invoice(session, invoice.id, client=fake)
    notes = fake.calls[0]["notes"]
    assert notes["invoice_id"] == str(invoice.id)
    assert notes["invoice_number"] == invoice.invoice_number


def test_amount_is_sent_in_paise(session, invoice, fake):
    provision_for_invoice(session, invoice.id, client=fake)
    assert fake.calls[0]["amount_paise"] == invoice.amount_paise == 4_200_000


def test_partial_payment_is_allowed(session, invoice, fake):
    """A customer paying half is a customer paying."""
    link = provision_for_invoice(session, invoice.id, client=fake)
    assert fake.calls[0]["accept_partial"] is True
    assert link.accept_partial is True


def test_customer_contact_details_are_passed(session, invoice, customer, fake):
    provision_for_invoice(session, invoice.id, client=fake)
    call = fake.calls[0]
    assert call["customer_email"] == customer.email
    assert call["customer_phone"] == customer.phone


# ---------------------------------------------------------------------------
# Failure handling.
# ---------------------------------------------------------------------------


def test_permanent_failure_is_recorded_and_retryable_later(session, invoice):
    """A refused request must leave the invoice provisionable, not half-broken."""
    broken = FakeRazorpay(fail_with=RazorpayPermanentError("product not enabled"))
    with pytest.raises(ProvisioningError, match="product not enabled"):
        provision_for_invoice(session, invoice.id, client=broken)

    assert session.exec(select(PaymentLink)).all() == []

    entry = session.exec(
        select(AuditLog).where(AuditLog.action == AuditAction.VA_PROVISION_FAILED)
    ).one()
    assert entry.detail["retryable"] is False

    # And a later attempt with a working client succeeds.
    link = provision_for_invoice(session, invoice.id, client=FakeRazorpay())
    assert link.razorpay_payment_link_id.startswith("plink_")


def test_transient_failure_is_marked_retryable(session, invoice):
    broken = FakeRazorpay(fail_with=RazorpayTransientError("gateway timeout"))
    with pytest.raises(ProvisioningError):
        provision_for_invoice(session, invoice.id, client=broken)

    entry = session.exec(
        select(AuditLog).where(AuditLog.action == AuditAction.VA_PROVISION_FAILED)
    ).one()
    assert entry.detail["retryable"] is True


def test_missing_invoice_is_rejected(session, fake):
    with pytest.raises(ProvisioningError, match="does not exist"):
        provision_for_invoice(session, uuid.uuid4(), client=fake)


# ---------------------------------------------------------------------------
# Batch behaviour.
# ---------------------------------------------------------------------------


def _extra_invoices(session, merchant, customer, count: int) -> None:
    from datetime import UTC, datetime

    for i in range(count):
        session.add(
            Invoice(
                merchant_id=merchant.id,
                customer_id=customer.id,
                invoice_number=f"INV-B{i}",
                amount_paise=100_000 * (i + 1),
                issued_at=datetime(2026, 7, 1, tzinfo=UTC),
                due_at=datetime(2026, 8, 1, tzinfo=UTC),
            )
        )
    session.commit()


def test_batch_provisions_everything_missing(session, merchant, customer, fake):
    _extra_invoices(session, merchant, customer, 5)
    report = provision_batch(session, client=fake)

    assert report["provisioned"] == 5
    assert report["failed"] == []
    assert len(session.exec(select(PaymentLink)).all()) == 5


def test_batch_skips_already_provisioned_invoices(session, merchant, customer, fake):
    """Re-running a batch must be a cheap no-op, not 60 more API calls."""
    _extra_invoices(session, merchant, customer, 3)
    provision_batch(session, client=fake)
    calls_after_first = len(fake.calls)

    second = provision_batch(session, client=fake)
    assert second["considered"] == 0
    assert second["provisioned"] == 0
    assert len(fake.calls) == calls_after_first


def test_one_failure_does_not_stop_the_batch(session, merchant, customer):
    """Razorpay rate-limits test mode; a partial batch must keep what it got."""
    _extra_invoices(session, merchant, customer, 4)

    class FlakyRazorpay(FakeRazorpay):
        def create_payment_link(self, **kw):
            if kw["reference_id"].endswith("B2"):
                raise RazorpayTransientError("rate limited")
            return super().create_payment_link(**kw)

    report = provision_batch(session, client=FlakyRazorpay())
    assert report["provisioned"] == 3
    assert len(report["failed"]) == 1
    assert report["failed"][0]["invoice_number"] == "INV-B2"
    assert len(session.exec(select(PaymentLink)).all()) == 3


def test_batch_respects_a_limit(session, merchant, customer, fake):
    _extra_invoices(session, merchant, customer, 6)
    report = provision_batch(session, limit=2, client=fake)
    assert report["provisioned"] == 2


# ---------------------------------------------------------------------------
# Audit trail.
# ---------------------------------------------------------------------------


def test_provisioning_is_audited(session, invoice, fake):
    link = provision_for_invoice(session, invoice.id, client=fake)
    entry = session.exec(
        select(AuditLog).where(AuditLog.action == AuditAction.VA_PROVISIONED)
    ).one()
    assert entry.detail["payment_link_id"] == link.razorpay_payment_link_id
    assert entry.detail["short_url"] == link.short_url
