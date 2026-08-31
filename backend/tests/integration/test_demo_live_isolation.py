"""The demo console must never see, count, export or change a live merchant's ledger.

This was enforced only by row-level security, which the deployment's connecting role
bypasses. With two live merchants registered, the operator dashboard listed their
invoices, folded their receivables into the demo's headline figures, exported their
customers' email addresses, and wrote one of their invoices off — closing the payment
link with it.

These tests run as the ordinary (RLS-bypassing) test role on purpose. That is the
configuration the leak appeared in, so a fix that only works under a restricted role
would pass a test written the other way round and still ship broken.
"""

import uuid

import pytest
from sqlmodel import Session, select

from app.core.clock import utcnow
from app.core.config import settings
from app.core.constants import InvoiceStatus
from app.models import Customer, Invoice, Merchant, Promise
from app.services.exports import queue_invoices
from app.services.metrics import compute_metrics

AUTH = {"X-Admin-Key": settings.admin_api_key}


@pytest.fixture
def live_invoice(session: Session, invoice: Invoice) -> Invoice:
    """A live merchant's invoice sitting alongside the demo's, as production would."""
    merchant = Merchant(
        name="Live Tenant Ltd",
        legal_name="Live Tenant Ltd",
        contact_email="owner@livetenant.example",
        mode="live",
        status="active",
        is_demo=False,
    )
    session.add(merchant)
    session.flush()
    customer = Customer(
        merchant_id=merchant.id,
        name="Live Tenant Buyer",
        email="ap@livetenant.example",
    )
    session.add(customer)
    session.flush()
    invoice = Invoice(
        merchant_id=merchant.id,
        customer_id=customer.id,
        # Deliberately the same number the demo ledger uses somewhere: correlation
        # must be by identity, never by invoice number.
        invoice_number="INV-LIVE-1",
        amount_paise=9_900_000,
        issued_at=utcnow(),
        due_at=utcnow(),
        status=InvoiceStatus.CHASING,
    )
    session.add(invoice)
    session.commit()
    session.refresh(invoice)
    return invoice


def test_the_queue_does_not_list_a_live_merchants_invoice(client, live_invoice, invoice):
    rows = client.get("/api/dashboard/queue", headers=AUTH).json()
    numbers = {row["invoice_number"] for row in rows}
    assert invoice.invoice_number in numbers
    assert live_invoice.invoice_number not in numbers, (
        "a live merchant's customer and receivable appeared in the demo console"
    )


def test_headline_metrics_exclude_live_receivables(session, live_invoice, invoice):
    metrics = compute_metrics(session)
    assert metrics.invoices_total == 1, (
        "live invoices were counted into the demo's headline figures; the demo would "
        "show a stranger's receivables to whoever is watching"
    )


def test_invoice_detail_is_not_readable_for_a_live_merchant(client, live_invoice):
    assert client.get(f"/api/dashboard/invoices/{live_invoice.id}", headers=AUTH).status_code == 404


def test_write_off_cannot_touch_a_live_merchants_invoice(client, session, live_invoice):
    response = client.post(f"/api/dashboard/invoices/{live_invoice.id}/write-off", headers=AUTH)
    assert response.status_code == 404
    session.refresh(live_invoice)
    assert live_invoice.status == InvoiceStatus.CHASING, (
        "the demo operator wrote off a real merchant's receivable"
    )


def test_escalation_cannot_touch_a_live_merchants_invoice(client, session, live_invoice):
    response = client.post(f"/api/dashboard/invoices/{live_invoice.id}/escalate", headers=AUTH)
    assert response.status_code == 404
    session.refresh(live_invoice)
    assert live_invoice.escalated_to_human_at is None


def test_exports_do_not_carry_live_customer_data(session, live_invoice, invoice):
    sheet = queue_invoices(session)
    rendered = "\n".join(str(cell) for row in sheet.rows for cell in row)
    assert live_invoice.invoice_number not in rendered, (
        "an export handed a live merchant's ledger to the demo operator"
    )
    assert "ap@livetenant.example" not in rendered
    assert invoice.invoice_number in rendered


def test_promises_list_is_scoped_to_the_demo(client, session, live_invoice, invoice):
    session.add(
        Promise(
            invoice_id=live_invoice.id,
            promised_date=utcnow().date(),
            tier_at_pause=1,
            source_message_excerpt="live tenant promise",
            extraction_confidence=0.9,
        )
    )
    session.commit()

    rows = client.get("/api/dashboard/promises", headers=AUTH).json()
    assert all(row["invoice_number"] != live_invoice.invoice_number for row in rows), (
        "`promises` carries no merchant_id, so no policy scopes it; the demo listed a "
        "live merchant's payment commitment"
    )


def test_a_simulated_reply_cannot_be_injected_into_a_live_invoice(
    client, live_invoice, monkeypatch
):
    """Even with the demo control switched on, the ledger boundary must hold."""
    monkeypatch.setattr(settings, "allow_simulated_replies", True)
    response = client.post(
        f"/api/invoices/{live_invoice.id}/simulate-reply",
        json={"body": "I will pay on Friday", "use_llm": False},
        headers=AUTH,
    )
    assert response.status_code == 404, (
        "a fabricated customer statement was written into a real merchant's audit trail"
    )


def test_an_unknown_invoice_and_a_live_one_are_indistinguishable(client, live_invoice):
    """The 404 must not become an oracle for which ids exist."""
    missing = client.get(f"/api/dashboard/invoices/{uuid.uuid4()}", headers=AUTH)
    live = client.get(f"/api/dashboard/invoices/{live_invoice.id}", headers=AUTH)
    assert missing.status_code == live.status_code == 404
    assert missing.json() == live.json()


def test_the_demo_still_sees_its_own_ledger(client, session, invoice):
    """The boundary must not be implemented by breaking the demo."""
    rows = client.get("/api/dashboard/queue", headers=AUTH).json()
    assert [row["invoice_number"] for row in rows] == [invoice.invoice_number]
    assert session.exec(select(Invoice)).all()
