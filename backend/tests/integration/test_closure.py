"""Payment link closure after recovery. P0 correctness.

"Stop recovery" has to include the payment route. A recovered invoice whose link is
still live is a customer who can pay twice into a settled bill — discovered, usually,
by that customer.

Razorpay is faked at the client boundary. The one behaviour these fakes copy from the
live API was verified against it directly: cancelling an already-cancelled link raises
a permanent error reading "cannot cancel or expire a cancelled link".
"""

import json
from datetime import timedelta

import pytest
from sqlmodel import select

from app.core.clock import utcnow
from app.core.config import settings
from app.core.constants import InvoiceStatus
from app.integrations.razorpay_client import (
    PaymentLinkResult,
    RazorpayPermanentError,
    RazorpayTransientError,
)
from app.integrations.razorpay_signature import compute_signature
from app.models import AuditAction, AuditLog, PaymentLink
from app.models.payment_link import MAX_CLOSURE_ATTEMPTS, PaymentLinkStatus
from app.services.closure import (
    close_link_for_invoice,
    close_payment_link,
    retry_pending_closures,
)


class FakeRazorpay:
    def __init__(self, *, raise_on_cancel: Exception | None = None, fetch_status="cancelled"):
        self.raise_on_cancel = raise_on_cancel
        self.fetch_status = fetch_status
        self.cancels: list[str] = []
        self.fetches: list[str] = []

    def cancel_payment_link(self, link_id: str) -> PaymentLinkResult:
        self.cancels.append(link_id)
        if self.raise_on_cancel:
            raise self.raise_on_cancel
        return PaymentLinkResult.from_payload(
            {
                "id": link_id,
                "short_url": "https://rzp.io/x",
                "reference_id": "r",
                "status": "cancelled",
                "amount": 1,
                "amount_paid": 0,
            }
        )

    def fetch_payment_link(self, link_id: str) -> PaymentLinkResult:
        self.fetches.append(link_id)
        return PaymentLinkResult.from_payload(
            {
                "id": link_id,
                "short_url": "https://rzp.io/x",
                "reference_id": "r",
                "status": self.fetch_status,
                "amount": 1,
                "amount_paid": 0,
            }
        )


@pytest.fixture
def link(session, invoice) -> PaymentLink:
    pl = PaymentLink(
        invoice_id=invoice.id,
        razorpay_payment_link_id="plink_CLOSE1",
        reference_id=f"vsl-{invoice.invoice_number}",
        short_url="https://rzp.io/rzp/close1",
        amount_expected_paise=invoice.amount_paise,
        status=PaymentLinkStatus.CREATED,
    )
    session.add(pl)
    session.commit()
    session.refresh(pl)
    return pl


def pay(api, invoice, link, *, amount_paid: int, event_id="evt_close"):
    payload = {
        "entity": "event",
        "event": "payment_link.paid"
        if amount_paid >= invoice.amount_paise
        else "payment_link.partially_paid",
        "payload": {
            "payment_link": {
                "entity": {
                    "id": link.razorpay_payment_link_id,
                    "reference_id": link.reference_id,
                    "amount": invoice.amount_paise,
                    "amount_paid": amount_paid,
                    "status": "paid" if amount_paid >= invoice.amount_paise else "partially_paid",
                    "notes": {"invoice_id": str(invoice.id)},
                }
            }
        },
    }
    raw = json.dumps(payload).encode()
    return api.post(
        "/api/webhooks/razorpay",
        content=raw,
        headers={
            "X-Razorpay-Signature": compute_signature(raw, settings.razorpay_webhook_secret),
            "X-Razorpay-Event-Id": event_id,
            "Content-Type": "application/json",
        },
    )


@pytest.fixture
def api(session):
    from fastapi.testclient import TestClient

    from app.main import create_app

    with TestClient(create_app()) as c:
        yield c


# ===========================================================================
# When closure happens.
# ===========================================================================


def test_full_payment_closes_the_link(session, invoice, link):
    fake = FakeRazorpay()
    invoice.amount_paid_paise = invoice.amount_paise
    invoice.status = InvoiceStatus.RECOVERED
    session.add(invoice)
    session.commit()

    assert close_link_for_invoice(session, invoice.id, client=fake) is True
    assert fake.cancels == [link.razorpay_payment_link_id]

    session.refresh(link)
    assert link.status == PaymentLinkStatus.CANCELLED
    assert link.is_open is False
    assert link.cancelled_at is not None


def test_partial_payment_leaves_the_link_open(api, session, invoice, link):
    """The customer still needs somewhere to pay the balance."""
    pay(api, invoice, link, amount_paid=invoice.amount_paise // 2)

    session.refresh(invoice)
    session.refresh(link)
    assert invoice.status == InvoiceStatus.PARTIALLY_PAID
    assert link.is_open is True
    assert link.closure_attempts == 0


def test_a_link_we_already_closed_is_not_closed_again(session, invoice, link):
    """Idempotent on `cancelled_at`, which only this module sets.

    Keying on `status` would be wrong: reconciliation writes "paid" from the webhook
    payload, so the link would look finished before Razorpay was ever told.
    """
    link.status = PaymentLinkStatus.CANCELLED
    link.cancelled_at = utcnow()
    session.add(link)
    session.commit()

    fake = FakeRazorpay()
    assert close_link_for_invoice(session, invoice.id, client=fake) is True
    assert fake.cancels == []


def test_a_paid_status_alone_does_not_skip_the_cancellation(session, invoice, link):
    """The gap this closes: without it, Razorpay was never actually called."""
    link.status = PaymentLinkStatus.PAID  # as reconciliation would have set it
    session.add(link)
    session.commit()

    fake = FakeRazorpay()
    close_link_for_invoice(session, invoice.id, client=fake)
    assert fake.cancels == [link.razorpay_payment_link_id]


# ===========================================================================
# Already cancelled upstream. Verified wording from the live API.
# ===========================================================================


def test_already_cancelled_upstream_counts_as_closed(session, invoice, link):
    """Razorpay: "cannot cancel or expire a cancelled link".

    Not a failure — the goal is that no further money can arrive, and it cannot.
    Treating it as an error would retry forever against a link already where we want it.
    """
    fake = FakeRazorpay(
        raise_on_cancel=RazorpayPermanentError("cannot cancel or expire a cancelled link")
    )
    assert close_payment_link(session, link, client=fake) is True

    session.refresh(link)
    assert link.is_open is False
    assert link.closure_error is None
    assert link.next_closure_retry_at is None
    assert fake.fetches, "should confirm the real upstream status rather than assume"


def test_the_confirmed_upstream_status_is_stored(session, invoice, link):
    fake = FakeRazorpay(
        raise_on_cancel=RazorpayPermanentError("cannot cancel or expire a paid link"),
        fetch_status="paid",
    )
    close_payment_link(session, link, client=fake)
    session.refresh(link)
    assert link.status == PaymentLinkStatus.PAID


# ===========================================================================
# Failure never undoes the payment.
# ===========================================================================


def test_a_closure_failure_leaves_the_payment_intact(api, session, invoice, link, monkeypatch):
    """The money is committed before Razorpay is called. A closure problem must not
    reach back and undo a payment that genuinely arrived."""
    import app.services.closure as closure_mod

    monkeypatch.setattr(
        closure_mod,
        "get_razorpay_client",
        lambda: FakeRazorpay(raise_on_cancel=RazorpayTransientError("gateway timeout")),
    )

    resp = pay(api, invoice, link, amount_paid=invoice.amount_paise)
    assert resp.status_code == 200
    assert resp.json()["status"] == "processed"

    session.expire_all()
    session.refresh(invoice)
    assert invoice.status == InvoiceStatus.RECOVERED
    assert invoice.amount_paid_paise == invoice.amount_paise

    session.refresh(link)
    assert link.closure_error is not None
    assert link.next_closure_retry_at is not None, "must become a retryable task"


def test_a_transient_failure_schedules_a_retry(session, invoice, link):
    fake = FakeRazorpay(raise_on_cancel=RazorpayTransientError("503"))
    assert close_payment_link(session, link, client=fake) is False

    session.refresh(link)
    assert link.closure_attempts == 1
    assert link.next_closure_retry_at > utcnow()
    assert link.is_open is True


def test_a_permanent_failure_is_not_retried(session, invoice, link):
    """Retrying a malformed request burns rate limit for an unchanging answer."""
    fake = FakeRazorpay(raise_on_cancel=RazorpayPermanentError("some other 4xx"))
    assert close_payment_link(session, link, client=fake) is False

    session.refresh(link)
    assert link.next_closure_retry_at is None
    assert link.closure_error is not None


# ===========================================================================
# Retry.
# ===========================================================================


def _make_due(session, link) -> None:
    link.next_closure_retry_at = utcnow() - timedelta(seconds=1)
    session.add(link)
    session.commit()


def test_a_retry_closes_the_link(session, invoice, link):
    invoice.amount_paid_paise = invoice.amount_paise
    invoice.status = InvoiceStatus.RECOVERED
    session.add(invoice)
    close_payment_link(
        session, link, client=FakeRazorpay(raise_on_cancel=RazorpayTransientError("503"))
    )
    _make_due(session, link)

    assert retry_pending_closures(session, client=FakeRazorpay()) == {
        "attempted": 1,
        "closed": 1,
    }
    session.refresh(link)
    assert link.is_open is False


def test_retries_before_the_backoff_are_skipped(session, invoice, link):
    invoice.amount_paid_paise = invoice.amount_paise
    session.add(invoice)
    close_payment_link(
        session, link, client=FakeRazorpay(raise_on_cancel=RazorpayTransientError("503"))
    )

    fake = FakeRazorpay()
    assert retry_pending_closures(session, client=fake)["attempted"] == 0
    assert fake.cancels == []


def test_closure_retries_are_bounded(session, invoice, link):
    """Otherwise a persistently failing link retries forever against our own provider."""
    invoice.amount_paid_paise = invoice.amount_paise
    session.add(invoice)
    session.commit()

    for _ in range(MAX_CLOSURE_ATTEMPTS + 3):
        _make_due(session, link)
        retry_pending_closures(
            session, client=FakeRazorpay(raise_on_cancel=RazorpayTransientError("503"))
        )
        session.refresh(link)
        if link.next_closure_retry_at is None:
            break

    assert link.closure_attempts <= MAX_CLOSURE_ATTEMPTS
    assert link.needs_closure is False


def test_a_retry_is_dropped_if_the_invoice_is_no_longer_fully_paid(session, invoice, link):
    close_payment_link(
        session, link, client=FakeRazorpay(raise_on_cancel=RazorpayTransientError("503"))
    )
    _make_due(session, link)

    fake = FakeRazorpay()
    assert retry_pending_closures(session, client=fake)["attempted"] == 1
    assert fake.cancels == [], "invoice is not fully paid, so nothing to close"

    session.refresh(link)
    assert link.next_closure_retry_at is None


# ===========================================================================
# Idempotency.
# ===========================================================================


def test_a_replayed_webhook_does_not_close_twice(api, session, invoice, link, monkeypatch):
    import app.services.closure as closure_mod

    fake = FakeRazorpay()
    monkeypatch.setattr(closure_mod, "get_razorpay_client", lambda: fake)

    for _ in range(4):
        pay(api, invoice, link, amount_paid=invoice.amount_paise, event_id="evt_same")

    assert len(fake.cancels) == 1, "the duplicate webhooks must not re-cancel"

    session.expire_all()
    session.refresh(invoice)
    assert invoice.amount_paid_paise == invoice.amount_paise


def test_closure_is_audited_both_ways(session, invoice, link):
    close_payment_link(
        session, link, client=FakeRazorpay(raise_on_cancel=RazorpayTransientError("503"))
    )
    failed = session.exec(
        select(AuditLog).where(AuditLog.action == AuditAction.PAYMENT_LINK_CLOSE_FAILED)
    ).one()
    assert failed.detail["retryable"] is True

    _make_due(session, link)
    close_payment_link(session, link, client=FakeRazorpay())
    closed = session.exec(
        select(AuditLog).where(AuditLog.action == AuditAction.PAYMENT_LINK_CLOSED)
    ).one()
    assert closed.detail["payment_link_id"] == link.razorpay_payment_link_id


# ===========================================================================
# The operator's retry button. P1.
# ===========================================================================


def test_an_operator_can_retry_a_failed_closure(session, invoice, link, monkeypatch):
    from fastapi.testclient import TestClient

    import app.api.dashboard as dashboard_mod
    from app.core.config import settings
    from app.main import create_app

    invoice.amount_paid_paise = invoice.amount_paise
    invoice.status = InvoiceStatus.RECOVERED
    session.add(invoice)
    close_payment_link(
        session, link, client=FakeRazorpay(raise_on_cancel=RazorpayTransientError("503"))
    )
    session.refresh(link)
    assert link.closure_error is not None

    monkeypatch.setattr(
        dashboard_mod,
        "close_payment_link",
        lambda sess, link_, **kw: close_payment_link(sess, link_, client=FakeRazorpay()),
    )

    with TestClient(create_app()) as api:
        api.headers.update({"X-Admin-Key": settings.admin_api_key})
        resp = api.post(f"/api/dashboard/exceptions/links/{link.id}/retry-closure")

    assert resp.status_code == 200
    assert resp.json()["closed"] is True

    session.expire_all()
    session.refresh(link)
    assert link.cancelled_at is not None


def test_retrying_an_already_closed_link_is_a_safe_no_op(session, invoice, link):
    from fastapi.testclient import TestClient

    from app.core.config import settings
    from app.main import create_app

    link.cancelled_at = utcnow()
    link.status = PaymentLinkStatus.CANCELLED
    session.add(link)
    session.commit()

    with TestClient(create_app()) as api:
        api.headers.update({"X-Admin-Key": settings.admin_api_key})
        resp = api.post(f"/api/dashboard/exceptions/links/{link.id}/retry-closure")

    assert resp.status_code == 200
    assert resp.json()["note"] == "already closed"


def test_closure_cannot_be_forced_on_an_unpaid_invoice(session, invoice, link):
    """Closing the link on an unpaid invoice would remove the customer's way to pay.

    The button exists to finish a closure that failed, not to let an operator revoke
    payment routes.
    """
    from fastapi.testclient import TestClient

    from app.core.config import settings
    from app.main import create_app

    assert invoice.amount_paid_paise == 0

    with TestClient(create_app()) as api:
        api.headers.update({"X-Admin-Key": settings.admin_api_key})
        resp = api.post(f"/api/dashboard/exceptions/links/{link.id}/retry-closure")

    assert resp.status_code == 409
    session.refresh(link)
    assert link.cancelled_at is None


def test_closure_retry_requires_authentication(session, invoice, link):
    from fastapi.testclient import TestClient

    from app.main import create_app

    with TestClient(create_app()) as anon:
        resp = anon.post(f"/api/dashboard/exceptions/links/{link.id}/retry-closure")
    assert resp.status_code == 401


def test_the_closure_retry_is_audited(session, invoice, link):
    from fastapi.testclient import TestClient

    from app.core.config import settings
    from app.main import create_app

    invoice.amount_paid_paise = invoice.amount_paise
    session.add(invoice)
    session.commit()

    with TestClient(create_app()) as api:
        api.headers.update({"X-Admin-Key": settings.admin_api_key})
        api.post(f"/api/dashboard/exceptions/links/{link.id}/retry-closure")

    session.expire_all()
    assert session.exec(
        select(AuditLog).where(AuditLog.action == AuditAction.PAYMENT_LINK_CLOSE_RETRIED)
    ).first()


# ===========================================================================
# Writing off must also shut the route money could still arrive by.
#
# The audit's finding: the write-off endpoint changed the invoice status and audited
# it, and nothing else. The Razorpay link stayed live, so a customer opening an old
# reminder could pay an invoice the merchant had already removed from the books.
# ===========================================================================


def test_writing_off_closes_the_payment_link(session, invoice, monkeypatch):
    from fastapi.testclient import TestClient

    from app.core.config import settings as app_settings
    from app.main import create_app

    link = PaymentLink(
        invoice_id=invoice.id,
        razorpay_payment_link_id="plink_WRITEOFF",
        reference_id="vsl-writeoff",
        short_url="https://rzp.io/rzp/writeoff",
        amount_expected_paise=invoice.amount_paise,
    )
    session.add(link)
    session.commit()

    cancelled: list[str] = []

    class FakeClient:
        def cancel_payment_link(self, link_id):
            cancelled.append(link_id)
            return PaymentLinkResult(
                id=link_id,
                short_url="https://rzp.io/rzp/writeoff",
                reference_id="vsl-writeoff",
                status=PaymentLinkStatus.CANCELLED,
                amount_paise=0,
                amount_paid_paise=0,
                raw={},
            )

    monkeypatch.setattr("app.services.closure.get_razorpay_client", lambda: FakeClient())

    with TestClient(create_app()) as client:
        client.headers.update({"X-Admin-Key": app_settings.admin_api_key})
        response = client.post(f"/api/dashboard/invoices/{invoice.id}/write-off")

    assert response.status_code == 200
    assert response.json()["payment_link_closed"] is True
    assert cancelled == ["plink_WRITEOFF"]

    session.refresh(link)
    assert link.cancelled_at is not None


def test_a_written_off_invoice_stays_eligible_for_closure_retry(session, invoice):
    """The retry sweep previously only ever considered fully paid invoices, so a
    closure that failed during a write-off could never be retried."""
    invoice.status = InvoiceStatus.WRITTEN_OFF
    session.add(invoice)
    session.commit()
    assert invoice.link_should_be_closed is True
    assert invoice.is_fully_paid is False
