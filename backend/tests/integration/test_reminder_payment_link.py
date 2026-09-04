"""A live reminder must carry a way to pay.

Provisioning had one live caller: an endpoint a merchant clicks per invoice. Nothing
provisioned on import, on ERP sync, or on a schedule, and the recovery cycle only
READ `PaymentLink` — so an imported ledger got no links and every reminder went out
without one. The product's own settings screen promises the opposite: "until this is
connected, live invoices cannot be issued a link", which reads as a promise that once
connected they will be.
"""

import uuid

import pytest
from sqlmodel import select

from app.models import Customer, Invoice, Merchant, PaymentLink
from app.services.payment_connections import PaymentConnectionRequiredError
from app.services.recovery import _provision_link


@pytest.fixture
def live_invoice(session):
    merchant = Merchant(
        name="Collector Ltd",
        contact_email="ops@collector.example",
        is_demo=False,
        mode="live",
        onboarding_state={},
    )
    session.add(merchant)
    session.commit()
    session.refresh(merchant)

    customer = Customer(
        merchant_id=merchant.id, name="Overdue Buyer", email="buyer@overdue.example"
    )
    session.add(customer)
    session.commit()
    session.refresh(customer)

    from datetime import UTC, datetime, timedelta

    invoice = Invoice(
        merchant_id=merchant.id,
        customer_id=customer.id,
        invoice_number=f"INV-LINK-{uuid.uuid4().hex[:6]}",
        amount_paise=250000,
        outstanding_paise=250000,
        issued_at=datetime.now(UTC) - timedelta(days=20),
        due_at=datetime.now(UTC) - timedelta(days=10),
        terms_days=10,
    )
    session.add(invoice)
    session.commit()
    session.refresh(invoice)
    return invoice


def test_a_live_invoice_gets_a_link_when_the_cycle_needs_one(session, live_invoice, monkeypatch):
    created = PaymentLink(
        invoice_id=live_invoice.id,
        razorpay_payment_link_id="plink_test",
        reference_id="vsl-test",
        short_url="https://rzp.io/rzp/abc",
        status="created",
        amount_expected_paise=live_invoice.amount_paise,
        amount_paid_paise=0,
        accept_partial=True,
        raw_response={},
    )
    monkeypatch.setattr("app.services.recovery.provision_for_invoice", lambda s, i, **kw: created)

    assert _provision_link(session, live_invoice) is created


def test_a_merchant_who_has_not_connected_razorpay_still_gets_chased(
    session, live_invoice, monkeypatch
):
    def refuse(*_args, **_kwargs):
        raise PaymentConnectionRequiredError("No Razorpay account connected")

    monkeypatch.setattr("app.services.recovery.provision_for_invoice", refuse)

    # None, not an exception: a chase without a link is worth far more than no chase.
    assert _provision_link(session, live_invoice) is None


def test_a_provider_failure_does_not_stop_the_reminder(session, live_invoice, monkeypatch):
    def blow_up(*_args, **_kwargs):
        raise RuntimeError("Razorpay is having a bad day")

    monkeypatch.setattr("app.services.recovery.provision_for_invoice", blow_up)

    assert _provision_link(session, live_invoice) is None
    # The session is usable afterwards, so the cycle carries on to the next invoice.
    assert session.exec(select(Invoice).where(Invoice.id == live_invoice.id)).first() is not None
