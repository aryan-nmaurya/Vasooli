"""Failed webhook reprocessing. P0 reliability.

The gap these close: a webhook is acknowledged with 200 as soon as it is stored,
because that is what stops Razorpay redelivering. If reconciliation then failed, the
failure existed only in a log line — the merchant saw an unpaid invoice, the customer
had a receipt, and nothing anywhere connected the two.
"""

import json
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlmodel import select

from app.core.clock import utcnow
from app.core.config import settings
from app.core.constants import InvoiceStatus
from app.integrations.razorpay_signature import compute_signature
from app.main import create_app
from app.models import AuditAction, AuditLog, PaymentLink, ReconciliationEvent
from app.models.reconciliation_event import MAX_EVENT_ATTEMPTS, EventStatus
from app.services.reconciliation import (
    reprocess_event,
    retry_failed_events,
)


@pytest.fixture
def api(session):
    with TestClient(create_app()) as c:
        c.headers.update({"X-Admin-Key": settings.admin_api_key})
        yield c


@pytest.fixture
def link(session, invoice) -> PaymentLink:
    pl = PaymentLink(
        invoice_id=invoice.id,
        razorpay_payment_link_id="plink_RETRY1",
        reference_id=f"vsl-{invoice.invoice_number}",
        short_url="https://rzp.io/rzp/retry1",
        amount_expected_paise=invoice.amount_paise,
    )
    session.add(pl)
    session.commit()
    session.refresh(pl)
    return pl


def payload_for(invoice, link, *, amount_paid=None):
    return {
        "entity": "event",
        "event": "payment_link.paid",
        "payload": {
            "payment_link": {
                "entity": {
                    "id": link.razorpay_payment_link_id,
                    "reference_id": link.reference_id,
                    "amount": invoice.amount_paise,
                    "amount_paid": amount_paid or invoice.amount_paise,
                    "status": "paid",
                    "notes": {"invoice_id": str(invoice.id)},
                }
            }
        },
    }


def post(api, payload, *, event_id="evt_retry"):
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


# ===========================================================================
# Lifecycle state.
# ===========================================================================


def test_a_successful_webhook_is_marked_processed(api, session, invoice, link):
    post(api, payload_for(invoice, link))
    event = session.exec(select(ReconciliationEvent)).one()
    assert event.status == EventStatus.PROCESSED
    assert event.attempts == 1
    assert event.next_retry_at is None
    assert event.processing_error is None


def test_a_non_payment_event_is_ignored_not_failed(api, session, invoice, link):
    """A cancellation notice is not an error. Marking it failed would fill the
    exception queue with events that need no action."""
    payload = payload_for(invoice, link)
    payload["event"] = "payment_link.cancelled"
    post(api, payload)

    event = session.exec(select(ReconciliationEvent)).one()
    assert event.status == EventStatus.IGNORED
    assert event.next_retry_at is None


def test_an_unmatched_payment_is_terminal_not_retried(api, session, invoice, link):
    """Retrying cannot conjure a matching invoice — this needs a person."""
    payload = payload_for(invoice, link)
    payload["payload"]["payment_link"]["entity"].update(
        {"id": "plink_UNKNOWN", "reference_id": "nope", "notes": {}}
    )
    post(api, payload)

    event = session.exec(select(ReconciliationEvent)).one()
    assert event.status == EventStatus.FAILED
    assert event.processing_error == "unmatched_payment"
    assert event.next_retry_at is None
    assert event.is_exhausted is True


# ===========================================================================
# Processing failure becomes a retryable task.
# ===========================================================================


@pytest.fixture
def broken_reconciliation(monkeypatch):
    """Make reconciliation raise, as a transient database fault or a bug would.

    Patched in BOTH namespaces. The webhook handler and the retry path each imported
    `process_event` into their own module, so patching one leaves the other running
    the real thing — and a "retry" test would silently be testing success.
    """
    import app.api.webhooks as webhooks_mod
    import app.services.reconciliation as reconciliation_mod

    def boom(*a, **kw):
        raise RuntimeError("simulated processing failure")

    monkeypatch.setattr(webhooks_mod, "process_event", boom)
    monkeypatch.setattr(reconciliation_mod, "process_event", boom)


def test_a_processing_failure_is_recorded_not_lost(
    api, session, invoice, link, broken_reconciliation
):
    resp = post(api, payload_for(invoice, link))
    assert resp.status_code == 200, "must not 5xx — that makes Razorpay retry into a bug"
    assert resp.json()["status"] == "recorded_for_retry"

    event = session.exec(select(ReconciliationEvent)).one()
    assert event.status == EventStatus.FAILED
    assert "simulated processing failure" in event.processing_error
    assert event.next_retry_at is not None, "must become a retryable task"


def test_the_failure_is_audited(api, session, invoice, link, broken_reconciliation):
    post(api, payload_for(invoice, link))
    entry = session.exec(
        select(AuditLog).where(AuditLog.action == AuditAction.RECONCILIATION_FAILED)
    ).one()
    assert entry.detail["exhausted"] is False
    assert entry.detail["next_retry_at"] is not None


def test_the_raw_payload_survives_the_failure(api, session, invoice, link, broken_reconciliation):
    """Reprocessing later needs the original body, so it must be stored before
    processing is attempted."""
    post(api, payload_for(invoice, link))
    event = session.exec(select(ReconciliationEvent)).one()
    assert event.raw_payload["payload"]["payment_link"]["entity"]["amount_paid"] == (
        invoice.amount_paise
    )
    assert event.signature_verified is True


# ===========================================================================
# Retry.
# ===========================================================================


def _make_due(session, event):
    event.next_retry_at = utcnow() - timedelta(seconds=1)
    session.add(event)
    session.commit()


def test_a_retry_after_a_transient_failure_recovers_the_payment(
    api, session, invoice, link, monkeypatch
):
    """The headline case: money arrived, processing broke, the retry fixes it."""
    import app.api.webhooks as webhooks_mod

    real = webhooks_mod.process_event
    monkeypatch.setattr(
        webhooks_mod, "process_event", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom"))
    )
    post(api, payload_for(invoice, link))
    monkeypatch.setattr(webhooks_mod, "process_event", real)

    event = session.exec(select(ReconciliationEvent)).one()
    assert event.status == EventStatus.FAILED
    session.refresh(invoice)
    assert invoice.status != InvoiceStatus.RECOVERED

    _make_due(session, event)
    assert retry_failed_events(session) == {"attempted": 1, "recovered": 1}

    session.refresh(event)
    session.refresh(invoice)
    assert event.status == EventStatus.PROCESSED
    assert invoice.status == InvoiceStatus.RECOVERED
    assert invoice.amount_paid_paise == invoice.amount_paise


def test_a_retry_before_the_backoff_is_not_attempted(
    api, session, invoice, link, broken_reconciliation
):
    post(api, payload_for(invoice, link))
    assert retry_failed_events(session)["attempted"] == 0


def test_a_retry_that_fails_again_backs_off_further(
    api, session, invoice, link, broken_reconciliation
):
    post(api, payload_for(invoice, link))
    event = session.exec(select(ReconciliationEvent)).one()
    first_gap = event.next_retry_at - utcnow()

    _make_due(session, event)
    retry_failed_events(session)
    session.refresh(event)

    assert event.attempts == 2
    assert event.next_retry_at - utcnow() > first_gap


def test_retries_are_bounded(api, session, invoice, link, broken_reconciliation):
    """Otherwise a poison payload becomes a retry storm against our own database."""
    post(api, payload_for(invoice, link))
    event = session.exec(select(ReconciliationEvent)).one()

    for _ in range(MAX_EVENT_ATTEMPTS + 3):
        if event.next_retry_at is None:
            break
        _make_due(session, event)
        retry_failed_events(session)
        session.refresh(event)

    assert event.attempts <= MAX_EVENT_ATTEMPTS
    assert event.is_exhausted is True
    assert event.next_retry_at is None


def test_a_processed_event_is_never_retried(api, session, invoice, link):
    post(api, payload_for(invoice, link))
    event = session.exec(select(ReconciliationEvent)).one()
    assert reprocess_event(session, event) is True
    session.refresh(event)
    assert event.attempts == 1, "no second attempt against an already-processed event"


# ===========================================================================
# Idempotency under retry — the property that makes all of this safe.
# ===========================================================================


def test_reprocessing_does_not_double_count(api, session, invoice, link, monkeypatch):
    import app.api.webhooks as webhooks_mod

    real = webhooks_mod.process_event
    monkeypatch.setattr(
        webhooks_mod, "process_event", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom"))
    )
    post(api, payload_for(invoice, link))
    monkeypatch.setattr(webhooks_mod, "process_event", real)

    event = session.exec(select(ReconciliationEvent)).one()
    for _ in range(4):
        _make_due(session, event)
        retry_failed_events(session)
        session.refresh(event)

    session.refresh(invoice)
    assert invoice.amount_paid_paise == invoice.amount_paise, "not a multiple"
    assert invoice.status == InvoiceStatus.RECOVERED


def test_a_duplicate_webhook_during_a_pending_retry_is_still_deduplicated(
    api, session, invoice, link, broken_reconciliation
):
    """The unique provider_event_id protection must survive the new retry path."""
    post(api, payload_for(invoice, link), event_id="evt_dup")
    second = post(api, payload_for(invoice, link), event_id="evt_dup")

    assert second.json()["status"] == "duplicate_ignored"
    assert len(session.exec(select(ReconciliationEvent)).all()) == 1


def test_out_of_order_events_do_not_walk_the_balance_backwards(api, session, invoice, link):
    post(api, payload_for(invoice, link), event_id="evt_full")
    stale = payload_for(invoice, link, amount_paid=1000)
    stale["event"] = "payment_link.partially_paid"
    post(api, stale, event_id="evt_stale")

    session.refresh(invoice)
    assert invoice.amount_paid_paise == invoice.amount_paise
    assert invoice.status == InvoiceStatus.RECOVERED


# ===========================================================================
# Operator endpoints.
# ===========================================================================


def test_the_exceptions_queue_surfaces_failed_events(
    api, session, invoice, link, broken_reconciliation
):
    post(api, payload_for(invoice, link))
    body = api.get("/api/dashboard/exceptions").json()

    assert body["total"] >= 1
    entry = body["reconciliation"][0]
    assert entry["event_id"] == "evt_retry"
    assert "simulated processing failure" in entry["error"]
    assert entry["attempts"] == 1


def test_an_operator_can_retry_an_exhausted_event(api, session, invoice, link, monkeypatch):
    """Manual retry deliberately ignores both the backoff and the attempt limit —
    that is what a human pressing the button is for."""
    import app.api.webhooks as webhooks_mod

    real = webhooks_mod.process_event
    monkeypatch.setattr(
        webhooks_mod, "process_event", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom"))
    )
    post(api, payload_for(invoice, link), event_id="evt_manual")
    monkeypatch.setattr(webhooks_mod, "process_event", real)

    event = session.exec(select(ReconciliationEvent)).one()
    event.attempts = MAX_EVENT_ATTEMPTS
    event.next_retry_at = None
    session.add(event)
    session.commit()

    resp = api.post("/api/dashboard/exceptions/events/evt_manual/retry")
    assert resp.status_code == 200
    assert resp.json()["recovered"] is True

    session.refresh(invoice)
    assert invoice.status == InvoiceStatus.RECOVERED


def test_the_manual_retry_is_audited(api, session, invoice, link, broken_reconciliation):
    post(api, payload_for(invoice, link), event_id="evt_audit")
    api.post("/api/dashboard/exceptions/events/evt_audit/retry")
    assert session.exec(
        select(AuditLog).where(AuditLog.action == AuditAction.RECONCILIATION_RETRIED)
    ).first()


def test_retrying_an_unknown_event_is_404(api):
    assert api.post("/api/dashboard/exceptions/events/nope/retry").status_code == 404


def test_the_exceptions_queue_requires_authentication(session):
    with TestClient(create_app()) as anon:
        assert anon.get("/api/dashboard/exceptions").status_code == 401
