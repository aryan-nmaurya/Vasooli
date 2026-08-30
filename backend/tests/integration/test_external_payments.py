"""Money recorded by a person rather than reported by a provider.

The failure these guard against is the one the system used to have by design: a
customer pays by NEFT, Vasooli never sees it, and the reminders keep going out.
"""

from datetime import UTC, date, datetime

import pytest
from fastapi.testclient import TestClient

from app.core.config import settings
from app.core.constants import InvoiceStatus, PromiseStatus
from app.main import create_app
from app.models import (
    AuditAction,
    AuditLog,
    ExternalPayment,
    Invoice,
    PaymentLink,
    PaymentMethod,
    Promise,
    ReconciliationEvent,
)
from app.models.reconciliation_event import EventStatus
from app.services.manual_payments import (
    ManualPaymentError,
    record_external_payment,
    reverse_external_payment,
)
from app.services.reconciliation import process_event
from tests.integration.test_webhooks import build_payload


@pytest.fixture
def api(session):
    with TestClient(create_app()) as c:
        c.headers.update({"X-Admin-Key": settings.admin_api_key})
        yield c


@pytest.fixture
def link(session, invoice) -> PaymentLink:
    pl = PaymentLink(
        invoice_id=invoice.id,
        razorpay_payment_link_id="plink_EXT0001",
        reference_id=f"vsl-{invoice.invoice_number}",
        short_url="https://rzp.io/rzp/test",
        amount_expected_paise=invoice.amount_paise,
    )
    session.add(pl)
    session.commit()
    session.refresh(pl)
    return pl


def make_event(session, *, amount_paid, event_id) -> ReconciliationEvent:
    """A stored, signature-verified webhook, in the shape reconciliation reads."""
    event = ReconciliationEvent(
        provider_event_id=event_id,
        event_type="payment_link.paid",
        raw_payload=build_payload(link_id="plink_EXT0001", amount_paid=amount_paid),
        signature_verified=True,
    )
    session.add(event)
    session.commit()
    session.refresh(event)
    return event


def record(session, invoice, *, amount, reference="UTR123456", method=PaymentMethod.BANK_TRANSFER):
    return record_external_payment(
        session,
        invoice_id=invoice.id,
        amount_paise=amount,
        method=method,
        reference=reference,
        received_on=date(2026, 8, 20),
        note="Seen on the bank statement",
        actor="human:test-operator",
    )


# ---------------------------------------------------------------------------
# The core promise: a customer who paid outside the link stops being chased.
# ---------------------------------------------------------------------------


def test_a_bank_transfer_settles_the_invoice_and_stops_the_chase(session, invoice):
    invoice.status = InvoiceStatus.CHASING
    session.add(invoice)
    session.commit()

    record(session, invoice, amount=invoice.amount_paise)

    session.refresh(invoice)
    assert invoice.status == InvoiceStatus.RECOVERED
    assert invoice.is_fully_paid
    assert invoice.recovered_at is not None
    assert not invoice.is_in_automation


def test_a_part_payment_leaves_the_balance_owed(session, invoice):
    record(session, invoice, amount=invoice.amount_paise // 4)

    session.refresh(invoice)
    assert invoice.status == InvoiceStatus.PARTIALLY_PAID
    assert not invoice.is_fully_paid
    assert invoice.outstanding_paise == invoice.amount_paise - (invoice.amount_paise // 4)


def test_an_active_promise_is_marked_kept(session, invoice):
    promise = Promise(
        invoice_id=invoice.id,
        promised_date=date(2026, 9, 1),
        status=PromiseStatus.ACTIVE,
        tier_at_pause=1,
        source_message_excerpt="I will pay by the 1st",
        extraction_confidence=0.9,
    )
    session.add(promise)
    session.commit()

    record(session, invoice, amount=invoice.amount_paise)

    session.refresh(promise)
    assert promise.status == PromiseStatus.KEPT
    assert promise.resolved_at is not None


def test_the_entry_is_audited_as_operator_asserted_not_as_a_verified_payment(session, invoice):
    record(session, invoice, amount=invoice.amount_paise)

    entry = session.exec(
        AuditLog.__table__.select().where(
            AuditLog.__table__.c.action == AuditAction.EXTERNAL_PAYMENT_RECORDED
        )
    ).one()
    assert entry.actor == "human:test-operator"
    assert entry.detail["verification"] == "operator_asserted"
    assert entry.detail["reference"] == "UTR123456"


# ---------------------------------------------------------------------------
# Duplicate protection. Entering the same transfer twice is the ordinary way a
# balance gets double-counted.
# ---------------------------------------------------------------------------


def test_the_same_reference_cannot_be_recorded_twice_against_one_invoice(session, invoice):
    record(session, invoice, amount=100_000, reference="UTR-DUPE")

    with pytest.raises(ManualPaymentError, match="already recorded"):
        record(session, invoice, amount=100_000, reference="UTR-DUPE")

    session.refresh(invoice)
    assert invoice.external_paid_paise == 100_000


def test_one_transfer_may_settle_two_different_invoices(session, merchant, customer, invoice):
    other = Invoice(
        merchant_id=merchant.id,
        customer_id=customer.id,
        invoice_number="INV-SECOND",
        amount_paise=100_000,
        issued_at=datetime(2026, 7, 1, tzinfo=UTC),
        due_at=datetime(2026, 8, 1, tzinfo=UTC),
    )
    session.add(other)
    session.commit()

    record(session, invoice, amount=100_000, reference="UTR-SHARED")
    record(session, other, amount=100_000, reference="UTR-SHARED")

    session.refresh(invoice)
    session.refresh(other)
    assert invoice.external_paid_paise == 100_000
    assert other.external_paid_paise == 100_000


def test_a_blank_reference_is_refused(session, invoice):
    with pytest.raises(ManualPaymentError, match="reference is required"):
        record(session, invoice, amount=100_000, reference="   ")


def test_an_unknown_method_is_refused(session, invoice):
    with pytest.raises(ManualPaymentError, match="Unknown payment method"):
        record(session, invoice, amount=100_000, method="bitcoin")


# ---------------------------------------------------------------------------
# The column split. This is the bug that would otherwise eat real money.
# ---------------------------------------------------------------------------


def test_a_manual_payment_does_not_make_later_link_payments_look_stale(
    session, invoice, link, monkeypatch
):
    """The reason link and external totals live in separate columns.

    Reconciliation applies Razorpay's running total with max(). If a hand-recorded
    ₹30,000 shared that column, a genuine ₹12,000 link payment arriving afterwards
    would look like a smaller, staler total and be discarded — real money silently
    lost from the balance.
    """
    monkeypatch.setattr("app.services.reconciliation.close_link_for_invoice", lambda *a, **k: True)

    record(session, invoice, amount=3_000_000, reference="UTR-FIRST")
    session.refresh(invoice)
    assert invoice.amount_paid_paise == 3_000_000

    process_event(session, make_event(session, amount_paid=1_200_000, event_id="evt_ext_1"))

    session.refresh(invoice)
    assert invoice.link_paid_paise == 1_200_000
    assert invoice.external_paid_paise == 3_000_000
    assert invoice.amount_paid_paise == 4_200_000
    assert invoice.is_fully_paid


def test_a_redelivered_webhook_still_cannot_double_count(session, invoice, link, monkeypatch):
    monkeypatch.setattr("app.services.reconciliation.close_link_for_invoice", lambda *a, **k: True)

    for n in range(2):
        process_event(session, make_event(session, amount_paid=1_000_000, event_id=f"evt_dup_{n}"))

    session.refresh(invoice)
    assert invoice.link_paid_paise == 1_000_000
    assert invoice.amount_paid_paise == 1_000_000


# ---------------------------------------------------------------------------
# Reversal. An invoice that was paid can go back to being owed.
# ---------------------------------------------------------------------------


def test_reversing_a_payment_reopens_a_recovered_invoice(session, invoice):
    payment = record(session, invoice, amount=invoice.amount_paise)
    session.refresh(invoice)
    assert invoice.status == InvoiceStatus.RECOVERED

    reverse_external_payment(
        session,
        payment_id=payment.id,
        reason="Cheque bounced",
        actor="human:test-operator",
    )

    session.refresh(invoice)
    assert invoice.status == InvoiceStatus.CHASING
    assert invoice.recovered_at is None
    assert invoice.external_paid_paise == 0
    assert invoice.outstanding_paise == invoice.amount_paise


def test_a_reversed_row_survives_as_evidence(session, invoice):
    payment = record(session, invoice, amount=100_000)
    reverse_external_payment(
        session, payment_id=payment.id, reason="Wrong invoice", actor="human:test-operator"
    )

    stored = session.get(ExternalPayment, payment.id)
    assert stored is not None
    assert not stored.is_active
    assert stored.reversal_reason == "Wrong invoice"
    assert stored.amount_paise == 100_000  # the claim itself is not erased


def test_reversing_twice_is_refused(session, invoice):
    payment = record(session, invoice, amount=100_000)
    reverse_external_payment(
        session, payment_id=payment.id, reason="mistake", actor="human:test-operator"
    )
    with pytest.raises(ManualPaymentError, match="already been reversed"):
        reverse_external_payment(
            session, payment_id=payment.id, reason="again", actor="human:test-operator"
        )


def test_a_reversal_needs_a_reason(session, invoice):
    payment = record(session, invoice, amount=100_000)
    with pytest.raises(ManualPaymentError, match="reason is required"):
        reverse_external_payment(
            session, payment_id=payment.id, reason="  ", actor="human:test-operator"
        )


def test_reversing_one_of_two_payments_leaves_the_other_standing(session, invoice):
    first = record(session, invoice, amount=1_000_000, reference="UTR-A")
    record(session, invoice, amount=2_000_000, reference="UTR-B")

    reverse_external_payment(
        session, payment_id=first.id, reason="duplicate entry", actor="human:test-operator"
    )

    session.refresh(invoice)
    assert invoice.external_paid_paise == 2_000_000


# ---------------------------------------------------------------------------
# The API surface.
# ---------------------------------------------------------------------------


def test_the_endpoint_records_a_payment_and_returns_both_halves_of_the_balance(
    api, session, invoice
):
    response = api.post(
        f"/api/dashboard/invoices/{invoice.id}/payments",
        json={
            "amount_paise": 2_000_000,
            "method": PaymentMethod.BANK_TRANSFER,
            "reference": "UTR-API-1",
            "received_on": "2026-08-20",
            "note": "NEFT from ABC Traders",
        },
    )
    assert response.status_code == 201
    balance = response.json()["balance"]
    # Split, not collapsed: an operator has to be able to see which part a provider
    # verified and which part a colleague typed in.
    assert balance["external_paid_display"] == "₹20,000"
    assert balance["link_paid_display"] == "₹0"
    assert balance["fully_paid"] is False


def test_a_duplicate_reference_gets_a_conflict_not_a_second_payment(api, session, invoice):
    body = {
        "amount_paise": 500_000,
        "method": PaymentMethod.UPI,
        "reference": "UPI-9",
        "received_on": "2026-08-20",
    }
    assert api.post(f"/api/dashboard/invoices/{invoice.id}/payments", json=body).status_code == 201
    assert api.post(f"/api/dashboard/invoices/{invoice.id}/payments", json=body).status_code == 409


def test_listing_payments_includes_reversed_entries(api, session, invoice):
    payment = record(session, invoice, amount=100_000)
    reverse_external_payment(
        session, payment_id=payment.id, reason="wrong invoice", actor="human:test-operator"
    )

    rows = api.get(f"/api/dashboard/invoices/{invoice.id}/payments").json()["payments"]
    assert len(rows) == 1
    assert rows[0]["active"] is False
    assert rows[0]["reversal_reason"] == "wrong invoice"


def test_an_auditor_cannot_record_a_payment(session, invoice, operator_account):
    """Recording a payment changes what the system believes it is owed. Read-only
    means read-only, and this is one of the writes that matters most."""
    from tests.integration.test_auth import TEST_OPERATOR_PASSWORD, TEST_OPERATOR_USERNAME

    operator_account.role = "auditor"
    session.add(operator_account)
    session.commit()

    with TestClient(create_app()) as client:
        client.post(
            "/api/auth/login",
            json={"username": TEST_OPERATOR_USERNAME, "password": TEST_OPERATOR_PASSWORD},
        )
        response = client.post(
            f"/api/dashboard/invoices/{invoice.id}/payments",
            json={
                "amount_paise": 100_000,
                "method": PaymentMethod.CASH,
                "reference": "R-1",
                "received_on": "2026-08-20",
            },
        )
    assert response.status_code == 403


# ---------------------------------------------------------------------------
# Manual matching. The unmatched queue previously offered only "retry", which
# cannot conjure a payment link that was never in our database.
# ---------------------------------------------------------------------------


def unmatched_event(session, *, amount_paid=1_500_000, event_id="evt_unmatched_1"):
    """A signature-verified payment for a link this system has never seen."""
    event = ReconciliationEvent(
        provider_event_id=event_id,
        event_type="payment_link.paid",
        raw_payload=build_payload(link_id="plink_SOMEONE_ELSE", amount_paid=amount_paid),
        signature_verified=True,
    )
    session.add(event)
    session.commit()
    process_event(session, event)
    session.refresh(event)
    return event


def test_an_unmatched_payment_starts_out_failed_and_unassignable(session, invoice):
    event = unmatched_event(session)
    assert event.status == EventStatus.FAILED
    assert event.processing_error == "unmatched_payment"


def test_an_operator_can_assign_an_unmatched_payment_to_an_invoice(api, session, invoice):
    event = unmatched_event(session, amount_paid=1_500_000)

    response = api.post(
        f"/api/dashboard/exceptions/events/{event.provider_event_id}/match",
        json={"invoice_id": str(invoice.id), "note": "Confirmed with the customer"},
    )
    assert response.status_code == 200

    session.refresh(event)
    session.refresh(invoice)
    assert event.status == EventStatus.PROCESSED
    assert event.matched_invoice_id == invoice.id
    assert event.match_strategy == "manual"
    assert invoice.external_paid_paise == 1_500_000
    # Critically NOT counted as a link payment: this event describes some other
    # object's link, and treating its total as this invoice's would corrupt every
    # later webhook for it.
    assert invoice.link_paid_paise == 0


def test_the_matched_amount_comes_from_the_payload_not_from_the_operator(api, session, invoice):
    """The person matching decides WHICH invoice. They do not decide how much
    Razorpay said arrived."""
    event = unmatched_event(session, amount_paid=777_000)
    api.post(
        f"/api/dashboard/exceptions/events/{event.provider_event_id}/match",
        json={"invoice_id": str(invoice.id)},
    )
    session.refresh(invoice)
    assert invoice.external_paid_paise == 777_000


def test_matching_the_same_event_twice_is_refused(api, session, invoice):
    event = unmatched_event(session)
    body = {"invoice_id": str(invoice.id)}
    assert (
        api.post(
            f"/api/dashboard/exceptions/events/{event.provider_event_id}/match", json=body
        ).status_code
        == 200
    )
    assert (
        api.post(
            f"/api/dashboard/exceptions/events/{event.provider_event_id}/match", json=body
        ).status_code
        == 409
    )


def test_matching_records_who_did_it(api, session, invoice):
    event = unmatched_event(session)
    api.post(
        f"/api/dashboard/exceptions/events/{event.provider_event_id}/match",
        json={"invoice_id": str(invoice.id)},
    )
    actions = [row.action for row in session.exec(AuditLog.__table__.select()).all()]
    assert AuditAction.RECONCILIATION_MANUALLY_MATCHED in actions


# ---------------------------------------------------------------------------
# `amount_paid` is a RUNNING TOTAL, not an increment.
#
# One link that is part-paid and then settled emits two events carrying 5,000 and
# 10,000. Both can land unmatched, and an operator working the queue will reasonably
# match both. Recording each at face value credits 15,000 for a customer who paid
# 10,000 — an overpayment the system invented, which then marks the invoice recovered
# and stops chasing a balance that is genuinely still owed.
# ---------------------------------------------------------------------------


def unmatched_link_event(session, *, amount_paid, event_id, event_type, link_id="plink_ORPHAN"):
    """An unmatched event for one specific link, carrying its running total."""
    event = ReconciliationEvent(
        provider_event_id=event_id,
        event_type=event_type,
        raw_payload=build_payload(
            link_id=link_id,
            event=event_type,
            amount_paid=amount_paid,
            status="paid" if event_type == "payment_link.paid" else "partially_paid",
        ),
        signature_verified=True,
    )
    session.add(event)
    session.commit()
    process_event(session, event)
    session.refresh(event)
    return event


def test_matching_a_partial_then_a_full_event_credits_the_total_once(api, session, invoice):
    """The regression. Two events, one link, 4,20,000 paid — not 6,30,000."""
    partial = unmatched_link_event(
        session,
        amount_paid=2_100_000,
        event_id="evt_partial",
        event_type="payment_link.partially_paid",
    )
    settled = unmatched_link_event(
        session, amount_paid=4_200_000, event_id="evt_settled", event_type="payment_link.paid"
    )

    first = api.post(
        f"/api/dashboard/exceptions/events/{partial.provider_event_id}/match",
        json={"invoice_id": str(invoice.id)},
    )
    second = api.post(
        f"/api/dashboard/exceptions/events/{settled.provider_event_id}/match",
        json={"invoice_id": str(invoice.id)},
    )
    assert first.status_code == 200
    assert second.status_code == 200

    session.refresh(invoice)
    assert invoice.external_paid_paise == 4_200_000
    assert invoice.amount_paid_paise == 4_200_000
    assert invoice.outstanding_paise == 0
    # And exactly settled — not overpaid into a balance nobody sent.
    assert invoice.amount_paid_paise == invoice.amount_paise


def test_the_second_match_records_only_the_difference(api, session, invoice):
    unmatched_link_event(
        session,
        amount_paid=2_100_000,
        event_id="evt_p1",
        event_type="payment_link.partially_paid",
    )
    unmatched_link_event(
        session, amount_paid=4_200_000, event_id="evt_p2", event_type="payment_link.paid"
    )

    api.post("/api/dashboard/exceptions/events/evt_p1/match", json={"invoice_id": str(invoice.id)})
    body = api.post(
        "/api/dashboard/exceptions/events/evt_p2/match", json={"invoice_id": str(invoice.id)}
    ).json()

    assert body["payment"]["amount_paise"] == 2_100_000  # the delta, not the total


def test_the_audit_trail_shows_the_arithmetic_not_just_the_result(api, session, invoice):
    """ "We credited 21,000" is not reviewable on its own."""
    unmatched_link_event(
        session, amount_paid=2_100_000, event_id="evt_a1", event_type="payment_link.partially_paid"
    )
    unmatched_link_event(
        session, amount_paid=4_200_000, event_id="evt_a2", event_type="payment_link.paid"
    )
    api.post("/api/dashboard/exceptions/events/evt_a1/match", json={"invoice_id": str(invoice.id)})
    api.post("/api/dashboard/exceptions/events/evt_a2/match", json={"invoice_id": str(invoice.id)})

    entry = [
        row
        for row in session.exec(AuditLog.__table__.select()).all()
        if row.action == AuditAction.RECONCILIATION_MANUALLY_MATCHED
    ][-1]
    assert entry.detail["provider_reported_total_paise"] == 4_200_000
    assert entry.detail["already_credited_paise"] == 2_100_000
    assert entry.detail["amount_paise"] == 2_100_000


def test_a_replayed_event_adding_nothing_new_is_refused(api, session, invoice):
    """Two events reporting the same running total. The second adds no money."""
    unmatched_link_event(
        session, amount_paid=4_200_000, event_id="evt_r1", event_type="payment_link.paid"
    )
    unmatched_link_event(
        session, amount_paid=4_200_000, event_id="evt_r2", event_type="payment_link.paid"
    )

    assert (
        api.post(
            "/api/dashboard/exceptions/events/evt_r1/match", json={"invoice_id": str(invoice.id)}
        ).status_code
        == 200
    )
    refused = api.post(
        "/api/dashboard/exceptions/events/evt_r2/match", json={"invoice_id": str(invoice.id)}
    )
    assert refused.status_code == 409
    assert "all of which is accounted for" in refused.json()["detail"]

    session.refresh(invoice)
    assert invoice.external_paid_paise == 4_200_000


def test_events_from_different_links_are_credited_independently(api, session, invoice):
    """The grouping must be per link. Two customers settling one invoice through two
    different links is ordinary, and collapsing them would swallow real money."""
    unmatched_link_event(
        session,
        amount_paid=1_000_000,
        event_id="evt_l1",
        event_type="payment_link.paid",
        link_id="plink_ONE",
    )
    unmatched_link_event(
        session,
        amount_paid=1_500_000,
        event_id="evt_l2",
        event_type="payment_link.paid",
        link_id="plink_TWO",
    )

    api.post("/api/dashboard/exceptions/events/evt_l1/match", json={"invoice_id": str(invoice.id)})
    api.post("/api/dashboard/exceptions/events/evt_l2/match", json={"invoice_id": str(invoice.id)})

    session.refresh(invoice)
    assert invoice.external_paid_paise == 2_500_000
