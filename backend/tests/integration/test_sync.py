"""Direct Razorpay sync — the safety net for webhooks that never arrived.

This is not hypothetical. A real test payment was made against INV-3008 while no
tunnel was running: Razorpay held ₹9,500 as paid, the invoice showed unpaid, and no
amount of retrying on Razorpay's side would ever have fixed it. Webhooks are
at-least-once, not at-least-eventually.
"""

import pytest
from sqlmodel import select

from app.core.constants import InvoiceStatus
from app.integrations.razorpay_client import PaymentLinkResult, RazorpayTransientError
from app.models import AuditAction, AuditLog, PaymentLink, ReconciliationEvent
from app.services.sync import sync_payment_links


class FakeRazorpay:
    """Reports whatever Razorpay is supposed to think about the link."""

    def __init__(self, *, amount_paid: int, status="paid", raises: Exception | None = None):
        self.amount_paid = amount_paid
        self.status = status
        self.raises = raises
        self.fetches: list[str] = []

    def fetch_payment_link(self, link_id: str) -> PaymentLinkResult:
        self.fetches.append(link_id)
        if self.raises:
            raise self.raises
        return PaymentLinkResult.from_payload(
            {
                "id": link_id,
                "short_url": "https://rzp.io/x",
                "reference_id": "r",
                "status": self.status,
                "amount": 4_200_000,
                "amount_paid": self.amount_paid,
            }
        )

    def cancel_payment_link(self, link_id: str) -> PaymentLinkResult:
        return self.fetch_payment_link(link_id)


@pytest.fixture
def link(session, invoice) -> PaymentLink:
    pl = PaymentLink(
        invoice_id=invoice.id,
        razorpay_payment_link_id="plink_SYNC1",
        reference_id=f"vsl-{invoice.invoice_number}",
        short_url="https://rzp.io/rzp/sync1",
        amount_expected_paise=invoice.amount_paise,
    )
    session.add(pl)
    session.commit()
    session.refresh(pl)
    return pl


# ===========================================================================
# The case this exists for.
# ===========================================================================


def test_a_payment_with_no_webhook_is_recovered(session, invoice, link):
    fake = FakeRazorpay(amount_paid=invoice.amount_paise)
    report = sync_payment_links(session, client=fake)

    assert report == {"checked": 1, "recovered": 1, "errors": 0}
    session.refresh(invoice)
    assert invoice.status == InvoiceStatus.RECOVERED
    assert invoice.amount_paid_paise == invoice.amount_paise


def test_the_recovery_goes_through_the_normal_reconciliation_path(session, invoice, link):
    """Not a shortcut that writes the invoice directly — the same `process_event` a
    webhook uses, so matching, auditing, and link closure all still happen."""
    sync_payment_links(session, client=FakeRazorpay(amount_paid=invoice.amount_paise))

    event = session.exec(select(ReconciliationEvent)).one()
    assert event.match_strategy == "payment_link_id"
    assert event.matched_invoice_id == invoice.id

    actions = {a.action for a in session.exec(select(AuditLog)).all()}
    assert AuditAction.PAYMENT_RECONCILED in actions


def test_a_synced_payment_is_labelled_as_such(session, invoice, link):
    """A reviewer must be able to tell a synced payment from a webhook one."""
    sync_payment_links(session, client=FakeRazorpay(amount_paid=invoice.amount_paise))

    entry = session.exec(
        select(AuditLog).where(AuditLog.action == AuditAction.RECONCILIATION_SYNCED)
    ).one()
    assert "no webhook" in entry.detail["reason"]


def test_a_synced_event_is_not_marked_signature_verified(session, invoice, link):
    """It did not arrive as a signed webhook. It came from an authenticated call we
    made — a stronger guarantee, but a different one, and conflating them would make
    the audit trail lie about how we learned of the payment."""
    sync_payment_links(session, client=FakeRazorpay(amount_paid=invoice.amount_paise))
    assert session.exec(select(ReconciliationEvent)).one().signature_verified is False


# ===========================================================================
# Idempotency — the property that makes running this hourly safe.
# ===========================================================================


def test_running_the_sync_repeatedly_does_not_double_count(session, invoice, link):
    for _ in range(4):
        sync_payment_links(session, client=FakeRazorpay(amount_paid=invoice.amount_paise))

    session.refresh(invoice)
    assert invoice.amount_paid_paise == invoice.amount_paise, "not a multiple"
    assert len(session.exec(select(ReconciliationEvent)).all()) == 1


def test_an_already_paid_invoice_is_not_re_fetched(session, invoice, link):
    """Asking Razorpay about a settled invoice burns rate limit for no answer."""
    sync_payment_links(session, client=FakeRazorpay(amount_paid=invoice.amount_paise))

    fake = FakeRazorpay(amount_paid=invoice.amount_paise)
    assert sync_payment_links(session, client=fake)["checked"] == 0
    assert fake.fetches == []


def test_a_webhook_arriving_after_a_sync_does_not_double_count(session, invoice, link, api=None):
    """Both paths can legitimately fire for one payment. Reconciliation applies the
    running total with max(), so the second is a no-op."""
    import json

    from fastapi.testclient import TestClient

    from app.core.config import settings
    from app.integrations.razorpay_signature import compute_signature
    from app.main import create_app

    sync_payment_links(session, client=FakeRazorpay(amount_paid=invoice.amount_paise))

    payload = {
        "entity": "event",
        "event": "payment_link.paid",
        "payload": {
            "payment_link": {
                "entity": {
                    "id": link.razorpay_payment_link_id,
                    "reference_id": link.reference_id,
                    "amount": invoice.amount_paise,
                    "amount_paid": invoice.amount_paise,
                    "status": "paid",
                    "notes": {"invoice_id": str(invoice.id)},
                }
            }
        },
    }
    raw = json.dumps(payload).encode()
    with TestClient(create_app()) as client:
        client.post(
            "/api/webhooks/razorpay",
            content=raw,
            headers={
                "X-Razorpay-Signature": compute_signature(raw, settings.razorpay_webhook_secret),
                "X-Razorpay-Event-Id": "evt_late",
                "Content-Type": "application/json",
            },
        )

    session.expire_all()
    session.refresh(invoice)
    assert invoice.amount_paid_paise == invoice.amount_paise


# ===========================================================================
# Partial payments and failures.
# ===========================================================================


def test_a_partial_payment_is_synced_without_closing_the_invoice(session, invoice, link):
    half = invoice.amount_paise // 2
    report = sync_payment_links(
        session, client=FakeRazorpay(amount_paid=half, status="partially_paid")
    )

    assert report["recovered"] == 0
    session.refresh(invoice)
    assert invoice.status == InvoiceStatus.PARTIALLY_PAID
    assert invoice.amount_paid_paise == half


def test_a_later_larger_payment_still_syncs(session, invoice, link):
    """The event id includes the amount, so a further payment is a new event."""
    sync_payment_links(session, client=FakeRazorpay(amount_paid=1_000_000, status="partially_paid"))
    sync_payment_links(session, client=FakeRazorpay(amount_paid=invoice.amount_paise))

    session.refresh(invoice)
    assert invoice.status == InvoiceStatus.RECOVERED
    assert len(session.exec(select(ReconciliationEvent)).all()) == 2


def test_nothing_happens_when_razorpay_reports_no_payment(session, invoice, link):
    report = sync_payment_links(session, client=FakeRazorpay(amount_paid=0, status="created"))
    assert report == {"checked": 1, "recovered": 0, "errors": 0}
    session.refresh(invoice)
    assert invoice.amount_paid_paise == 0


def test_a_razorpay_outage_is_counted_not_crashed(session, invoice, link):
    """A failed sync must not take the scheduler down with it."""
    fake = FakeRazorpay(amount_paid=0, raises=RazorpayTransientError("503"))
    report = sync_payment_links(session, client=fake)
    assert report == {"checked": 1, "recovered": 0, "errors": 1}


def test_the_sync_can_target_one_invoice(session, invoice, link):
    fake = FakeRazorpay(amount_paid=invoice.amount_paise)
    assert sync_payment_links(session, client=fake, invoice_number="NOPE")["checked"] == 0
    assert fake.fetches == []
