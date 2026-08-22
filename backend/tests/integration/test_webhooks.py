"""Webhook handling and reconciliation. Doc §6.

These are the tests that answer the two questions a payments engineer will ask:
"what happens when the same webhook arrives twice?" and "what stops someone forging
one?" Both answers need to be demonstrable, not asserted.
"""

import json

import pytest
from fastapi.testclient import TestClient
from sqlmodel import select

from app.core.constants import InvoiceStatus, PromiseStatus
from app.integrations.razorpay_signature import compute_signature
from app.main import create_app
from app.models import (
    AuditAction,
    AuditLog,
    PaymentLink,
    PaymentLinkStatus,
    Promise,
    ReconciliationEvent,
)

SECRET = "PLACEHOLDER"  # matches RAZORPAY_WEBHOOK_SECRET in the test environment


@pytest.fixture
def api(session):
    with TestClient(create_app()) as c:
        yield c


@pytest.fixture
def link(session, invoice) -> PaymentLink:
    pl = PaymentLink(
        invoice_id=invoice.id,
        razorpay_payment_link_id="plink_TEST0001",
        reference_id=f"vsl-{invoice.invoice_number}",
        short_url="https://rzp.io/rzp/test",
        amount_expected_paise=invoice.amount_paise,
    )
    session.add(pl)
    session.commit()
    session.refresh(pl)
    return pl


def build_payload(
    link_id="plink_TEST0001",
    *,
    event="payment_link.paid",
    amount=4_200_000,
    amount_paid=4_200_000,
    notes=None,
    reference_id="vsl-INV-TEST",
    status="paid",
) -> dict:
    return {
        "entity": "event",
        "event": event,
        "contains": ["payment_link", "payment"],
        "payload": {
            "payment_link": {
                "entity": {
                    "id": link_id,
                    "reference_id": reference_id,
                    "amount": amount,
                    "amount_paid": amount_paid,
                    "status": status,
                    "notes": notes if notes is not None else {},
                }
            },
            "payment": {"entity": {"id": "pay_TEST", "amount": amount_paid}},
        },
    }


def post(api, payload: dict, *, event_id="evt_test_001", secret=SECRET, tamper=False):
    raw = json.dumps(payload).encode()
    sig = compute_signature(raw, secret)
    if tamper:
        raw = raw.replace(b'"amount_paid"', b'"amount_paid" ')  # body no longer matches sig
    return api.post(
        "/api/webhooks/razorpay",
        content=raw,
        headers={
            "X-Razorpay-Signature": sig,
            "X-Razorpay-Event-Id": event_id,
            "Content-Type": "application/json",
        },
    )


# ---------------------------------------------------------------------------
# Signature verification. Doc §6.
# ---------------------------------------------------------------------------


def test_valid_signature_is_accepted(api, session, invoice, link):
    assert post(api, build_payload()).status_code == 200


def test_wrong_secret_is_rejected_and_stores_nothing(api, session, invoice, link):
    """A forged webhook must not be able to mark an invoice paid."""
    resp = post(api, build_payload(), secret="not-the-real-secret")
    assert resp.status_code == 400

    assert session.exec(select(ReconciliationEvent)).all() == []
    session.refresh(invoice)
    assert invoice.status != InvoiceStatus.RECOVERED
    assert invoice.amount_paid_paise == 0


def test_tampered_body_is_rejected(api, session, invoice, link):
    """The signature covers the exact bytes; a one-character edit invalidates it."""
    assert post(api, build_payload(), tamper=True).status_code == 400
    assert session.exec(select(ReconciliationEvent)).all() == []


def test_missing_signature_is_rejected(api, session, invoice, link):
    resp = api.post("/api/webhooks/razorpay", content=b'{"event":"payment_link.paid"}')
    assert resp.status_code == 400


def test_rejected_webhooks_are_audited(api, session, invoice, link):
    post(api, build_payload(), secret="wrong")
    entry = session.exec(
        select(AuditLog).where(AuditLog.action == AuditAction.WEBHOOK_SIGNATURE_INVALID)
    ).one()
    assert entry.detail["had_signature"] is True


# ---------------------------------------------------------------------------
# Idempotency. Doc §6 — the test that wins the argument.
# ---------------------------------------------------------------------------


def test_same_event_five_times_counts_once(api, session, invoice, link):
    """Razorpay delivers at-least-once. Five deliveries, one payment."""
    payload = build_payload()
    statuses = [post(api, payload, event_id="evt_dup").json()["status"] for _ in range(5)]

    assert statuses[0] == "processed"
    assert statuses[1:] == ["duplicate_ignored"] * 4

    assert len(session.exec(select(ReconciliationEvent)).all()) == 1
    session.refresh(invoice)
    assert invoice.amount_paid_paise == 4_200_000  # not 21,000,000
    assert invoice.status == InvoiceStatus.RECOVERED


def test_duplicate_returns_200_so_razorpay_stops_retrying(api, session, invoice, link):
    payload = build_payload()
    post(api, payload, event_id="evt_x")
    second = post(api, payload, event_id="evt_x")
    assert second.status_code == 200


def test_distinct_events_are_both_processed(api, session, invoice, link):
    post(
        api,
        build_payload(
            amount_paid=2_000_000, event="payment_link.partially_paid", status="partially_paid"
        ),
        event_id="evt_a",
    )
    post(api, build_payload(amount_paid=4_200_000), event_id="evt_b")
    assert len(session.exec(select(ReconciliationEvent)).all()) == 2


def test_body_hash_dedupes_when_the_header_is_absent(api, session, invoice, link):
    """A delivery without an event id must still deduplicate."""
    raw = json.dumps(build_payload()).encode()
    headers = {
        "X-Razorpay-Signature": compute_signature(raw, SECRET),
        "Content-Type": "application/json",
    }
    first = api.post("/api/webhooks/razorpay", content=raw, headers=headers)
    second = api.post("/api/webhooks/razorpay", content=raw, headers=headers)
    assert first.json()["status"] == "processed"
    assert second.json()["status"] == "duplicate_ignored"


# ---------------------------------------------------------------------------
# Reconciliation outcomes.
# ---------------------------------------------------------------------------


def test_full_payment_marks_the_invoice_recovered(api, session, invoice, link):
    post(api, build_payload())
    session.refresh(invoice)
    session.refresh(link)
    assert invoice.status == InvoiceStatus.RECOVERED
    assert invoice.recovered_at is not None
    assert invoice.outstanding_paise == 0
    assert link.status == PaymentLinkStatus.PAID


def test_partial_payment_keeps_the_invoice_in_the_queue(api, session, invoice, link):
    """Half paid is not paid. The balance is still owed."""
    post(
        api,
        build_payload(
            event="payment_link.partially_paid", amount_paid=2_000_000, status="partially_paid"
        ),
    )
    session.refresh(invoice)
    assert invoice.status == InvoiceStatus.PARTIALLY_PAID
    assert invoice.amount_paid_paise == 2_000_000
    assert invoice.outstanding_paise == 2_200_000
    assert invoice.recovered_at is None


def test_partial_then_full_settles_without_double_counting(api, session, invoice, link):
    """Razorpay reports a running total, so the second event must not be added on top."""
    post(
        api,
        build_payload(
            event="payment_link.partially_paid", amount_paid=2_000_000, status="partially_paid"
        ),
        event_id="evt_1",
    )
    post(api, build_payload(amount_paid=4_200_000), event_id="evt_2")
    session.refresh(invoice)
    assert invoice.amount_paid_paise == 4_200_000  # not 6,200,000
    assert invoice.status == InvoiceStatus.RECOVERED


def test_out_of_order_delivery_cannot_walk_the_balance_backwards(api, session, invoice, link):
    """A stale event arriving late must not un-pay an invoice."""
    post(api, build_payload(amount_paid=4_200_000), event_id="evt_full")
    post(
        api,
        build_payload(
            event="payment_link.partially_paid", amount_paid=1_000_000, status="partially_paid"
        ),
        event_id="evt_stale",
    )
    session.refresh(invoice)
    assert invoice.amount_paid_paise == 4_200_000
    assert invoice.status == InvoiceStatus.RECOVERED


def test_overpayment_still_settles(api, session, invoice, link):
    post(api, build_payload(amount_paid=5_000_000))
    session.refresh(invoice)
    assert invoice.status == InvoiceStatus.RECOVERED
    assert invoice.outstanding_paise == 0


# ---------------------------------------------------------------------------
# Matching. Three independent paths, none of them the amount.
# ---------------------------------------------------------------------------


def test_matches_by_payment_link_id(api, session, invoice, link):
    post(api, build_payload(link_id=link.razorpay_payment_link_id))
    event = session.exec(select(ReconciliationEvent)).one()
    assert event.match_strategy == "payment_link_id"
    assert event.matched_invoice_id == invoice.id


def test_falls_back_to_notes_invoice_id(api, session, invoice, link):
    """If the link id is unrecognised, notes still identify the invoice."""
    post(api, build_payload(link_id="plink_UNKNOWN", notes={"invoice_id": str(invoice.id)}))
    event = session.exec(select(ReconciliationEvent)).one()
    assert event.match_strategy == "notes.invoice_id"
    assert event.matched_invoice_id == invoice.id


def test_falls_back_to_reference_id(api, session, invoice, link):
    post(api, build_payload(link_id="plink_UNKNOWN", reference_id=link.reference_id))
    event = session.exec(select(ReconciliationEvent)).one()
    assert event.match_strategy == "reference_id"


def test_unmatched_payment_is_recorded_not_guessed(api, session, invoice, link):
    """An unidentifiable payment surfaces for a human. It never picks a likely invoice."""
    post(api, build_payload(link_id="plink_NOPE", reference_id="nope", notes={}))

    event = session.exec(select(ReconciliationEvent)).one()
    assert event.processing_error == "unmatched_payment"
    assert event.matched_invoice_id is None

    session.refresh(invoice)
    assert invoice.amount_paid_paise == 0
    assert session.exec(
        select(AuditLog).where(AuditLog.action == AuditAction.RECONCILIATION_UNMATCHED)
    ).one()


def test_garbage_invoice_id_in_notes_does_not_crash(api, session, invoice, link):
    post(api, build_payload(link_id="plink_UNKNOWN", notes={"invoice_id": "not-a-uuid"}))
    assert session.exec(select(ReconciliationEvent)).one().processing_error == "unmatched_payment"


# ---------------------------------------------------------------------------
# Other event types and side effects.
# ---------------------------------------------------------------------------


def test_irrelevant_events_are_stored_but_move_no_money(api, session, invoice, link):
    post(api, build_payload(event="payment_link.cancelled"))
    session.refresh(invoice)
    assert invoice.amount_paid_paise == 0
    assert session.exec(select(ReconciliationEvent)).one().processed_at is not None


def test_payment_resolves_an_active_promise(api, session, invoice, link):
    """A promise that was kept must close, not linger and later count as broken."""
    from datetime import date

    session.add(
        Promise(
            invoice_id=invoice.id,
            promised_date=date(2026, 9, 1),
            source_message_excerpt="paying friday",
            extraction_confidence=0.9,
            tier_at_pause=2,
        )
    )
    session.commit()

    post(api, build_payload())

    promise = session.exec(select(Promise)).one()
    assert promise.status == PromiseStatus.KEPT
    assert promise.resolved_at is not None


def test_reconciliation_is_audited(api, session, invoice, link):
    post(api, build_payload())
    entry = session.exec(
        select(AuditLog).where(AuditLog.action == AuditAction.PAYMENT_RECONCILED)
    ).one()
    assert entry.detail["total_paid_paise"] == 4_200_000
    assert entry.detail["new_status"] == InvoiceStatus.RECOVERED
    assert entry.detail["match_strategy"] == "payment_link_id"


def test_malformed_json_is_rejected(api, session):
    raw = b"{not json"
    resp = api.post(
        "/api/webhooks/razorpay",
        content=raw,
        headers={"X-Razorpay-Signature": compute_signature(raw, SECRET)},
    )
    assert resp.status_code == 400
