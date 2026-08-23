"""Dashboard read API. Doc §7, §9."""

from datetime import UTC, date, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from app.core.config import settings
from app.core.constants import InvoiceStatus, PromiseStatus, ReasonCategory
from app.main import create_app
from app.models import AuditLog, Invoice, Promise, Reminder
from app.services.metrics import compute_metrics


@pytest.fixture
def api(session):
    """An authenticated client.

    Every dashboard endpoint is gated now, so these tests carry a credential. The
    unauthenticated cases live in test_auth.py rather than being duplicated here.
    """
    with TestClient(create_app()) as c:
        c.headers.update({"X-Admin-Key": settings.admin_api_key})
        yield c


def add_invoice(session, merchant, customer, *, number, amount, paid=0, status, **kw) -> Invoice:
    due = datetime.now(UTC) - timedelta(days=15)
    inv = Invoice(
        merchant_id=merchant.id,
        customer_id=customer.id,
        invoice_number=number,
        amount_paise=amount,
        amount_paid_paise=paid,
        issued_at=due - timedelta(days=30),
        due_at=due,
        status=status,
        **kw,
    )
    session.add(inv)
    session.commit()
    session.refresh(inv)
    return inv


# ===========================================================================
# Metrics. Doc §9 — shared with the eval harness, so definitions must be exact.
# ===========================================================================


def test_recovery_rate_is_measured_by_value_not_by_count(session, merchant, customer):
    """Forty small wins and one big miss is not a 97% success rate."""
    for i in range(4):
        add_invoice(
            session,
            merchant,
            customer,
            number=f"S{i}",
            amount=200_000,
            paid=200_000,
            status=InvoiceStatus.RECOVERED,
            recovered_at=datetime.now(UTC),
        )
    add_invoice(
        session, merchant, customer, number="BIG", amount=8_000_000, status=InvoiceStatus.CHASING
    )

    m = compute_metrics(session)
    assert m.recovered_paise == 800_000
    assert m.total_overdue_paise == 8_000_000
    assert m.recovery_rate == pytest.approx(800_000 / 8_800_000)
    assert m.recovery_rate < 0.10  # by count it would be 80%


def test_written_off_invoices_leave_the_outstanding_total(session, merchant, customer):
    add_invoice(
        session, merchant, customer, number="W1", amount=500_000, status=InvoiceStatus.WRITTEN_OFF
    )
    assert compute_metrics(session).total_overdue_paise == 0


def test_partial_payments_reduce_but_do_not_clear_the_balance(session, merchant, customer):
    add_invoice(
        session,
        merchant,
        customer,
        number="P1",
        amount=1_000_000,
        paid=400_000,
        status=InvoiceStatus.PARTIALLY_PAID,
    )
    assert compute_metrics(session).total_overdue_paise == 600_000


def test_automation_rate_counts_only_untouched_recoveries(session, merchant, customer):
    add_invoice(
        session,
        merchant,
        customer,
        number="A1",
        amount=100_000,
        paid=100_000,
        status=InvoiceStatus.RECOVERED,
        recovered_at=datetime.now(UTC),
    )
    add_invoice(
        session,
        merchant,
        customer,
        number="A2",
        amount=100_000,
        paid=100_000,
        status=InvoiceStatus.RECOVERED,
        recovered_at=datetime.now(UTC),
        escalated_to_human_at=datetime.now(UTC),
    )
    assert compute_metrics(session).automation_rate == pytest.approx(0.5)


def test_metrics_on_an_empty_ledger_do_not_divide_by_zero(session):
    m = compute_metrics(session)
    assert m.recovery_rate == 0.0
    assert m.avg_days_to_recovery is None
    assert m.automation_rate is None


# ===========================================================================
# Overview and queue.
# ===========================================================================


def test_overview_renders_money_in_indian_format(api, session, merchant, customer):
    add_invoice(
        session, merchant, customer, number="Q1", amount=6_40_000_00, status=InvoiceStatus.CHASING
    )
    body = api.get("/api/dashboard/overview").json()
    assert body["total_overdue_display"] == "₹6,40,000"


def test_queue_is_ordered_by_outstanding_value(api, session, merchant, customer):
    add_invoice(
        session, merchant, customer, number="SMALL", amount=100_000, status=InvoiceStatus.CHASING
    )
    add_invoice(
        session, merchant, customer, number="LARGE", amount=900_000, status=InvoiceStatus.CHASING
    )
    rows = api.get("/api/dashboard/queue").json()
    assert [r["invoice_number"] for r in rows] == ["LARGE", "SMALL"]


def test_queue_filters_by_reason(api, session, merchant, customer):
    add_invoice(
        session,
        merchant,
        customer,
        number="D1",
        amount=100_000,
        status=InvoiceStatus.HUMAN_REVIEW,
        reason_category=ReasonCategory.DISPUTE_LIKELY,
    )
    add_invoice(
        session,
        merchant,
        customer,
        number="O1",
        amount=100_000,
        status=InvoiceStatus.CHASING,
        reason_category=ReasonCategory.OVERSIGHT,
    )
    rows = api.get("/api/dashboard/queue?reason=dispute_likely").json()
    assert [r["invoice_number"] for r in rows] == ["D1"]


def test_the_queue_says_what_happens_next(api, session, merchant, customer):
    add_invoice(
        session,
        merchant,
        customer,
        number="H1",
        amount=100_000,
        status=InvoiceStatus.HUMAN_REVIEW,
        escalation_reason="dispute_likely",
    )
    row = api.get("/api/dashboard/queue").json()[0]
    assert "human" in row["next_action"].lower()
    assert row["tier_label"] == "Human"


# ===========================================================================
# Invoice detail and provenance.
# ===========================================================================


def test_detail_returns_the_full_timeline_in_order(api, session, merchant, customer):
    inv = add_invoice(
        session, merchant, customer, number="T1", amount=100_000, status=InvoiceStatus.CHASING
    )
    for actor, action in [
        ("system", "invoice_ingested"),
        ("ai", "diagnosed"),
        ("policy", "policy_evaluated"),
        ("razorpay", "payment_reconciled"),
    ]:
        session.add(AuditLog(invoice_id=inv.id, actor=actor, action=action, detail={}))
    session.commit()

    body = api.get(f"/api/dashboard/invoices/{inv.id}").json()
    assert [e["provenance"] for e in body["timeline"]] == ["system", "ai", "policy", "razorpay"]


def test_the_policy_decision_is_shown_with_the_reminder(api, session, merchant, customer):
    """Doc §5 — the check list is the most convincing artifact in the demo."""
    inv = add_invoice(
        session, merchant, customer, number="T2", amount=100_000, status=InvoiceStatus.CHASING
    )
    session.add(
        Reminder(
            invoice_id=inv.id,
            tier=1,
            tone="polite",
            subject="s",
            body="b",
            policy_decision={"approved": True, "rendered": "Result: APPROVED", "checks": []},
        )
    )
    session.commit()

    reminder = api.get(f"/api/dashboard/invoices/{inv.id}").json()["reminders"][0]
    assert "APPROVED" in reminder["policy_rendered"]


def test_a_missing_invoice_is_404(api):
    import uuid

    assert api.get(f"/api/dashboard/invoices/{uuid.uuid4()}").status_code == 404


# ===========================================================================
# Promises and audit.
# ===========================================================================


def test_promise_tracker_filters_by_status(api, session, merchant, customer):
    inv = add_invoice(
        session,
        merchant,
        customer,
        number="PR1",
        amount=100_000,
        status=InvoiceStatus.PROMISE_ACTIVE,
    )
    session.add(
        Promise(
            invoice_id=inv.id,
            promised_date=date(2026, 9, 1),
            source_message_excerpt="x",
            extraction_confidence=0.9,
            status=PromiseStatus.ACTIVE,
            tier_at_pause=2,
        )
    )
    session.add(
        Promise(
            invoice_id=inv.id,
            promised_date=date(2026, 8, 1),
            source_message_excerpt="y",
            extraction_confidence=0.8,
            status=PromiseStatus.BROKEN,
            tier_at_pause=1,
        )
    )
    session.commit()

    assert len(api.get("/api/dashboard/promises").json()) == 2
    broken = api.get("/api/dashboard/promises?status=broken").json()
    assert len(broken) == 1
    assert broken[0]["tier_at_pause"] == 1


def test_audit_is_newest_first_and_filterable(api, session, merchant, customer):
    inv = add_invoice(
        session, merchant, customer, number="AU1", amount=100_000, status=InvoiceStatus.CHASING
    )
    for action in ("invoice_ingested", "diagnosed", "reminder_sent"):
        session.add(AuditLog(invoice_id=inv.id, actor="system", action=action, detail={}))
    session.commit()

    entries = api.get("/api/dashboard/audit").json()
    assert entries[0]["action"] == "reminder_sent"
    assert len(api.get("/api/dashboard/audit?action=diagnosed").json()) == 1


# ===========================================================================
# Manual actions require the admin key.
# ===========================================================================


def test_manual_escalate_needs_a_credential(session, merchant, customer):
    """Uses a deliberately anonymous client: the shared `api` fixture is logged in."""
    inv = add_invoice(
        session, merchant, customer, number="E1", amount=100_000, status=InvoiceStatus.CHASING
    )
    with TestClient(create_app()) as anon:
        assert anon.post(f"/api/dashboard/invoices/{inv.id}/escalate").status_code == 401


def test_manual_escalate_works_when_authenticated(api, session, merchant, customer):
    inv = add_invoice(
        session, merchant, customer, number="E2", amount=100_000, status=InvoiceStatus.CHASING
    )
    resp = api.post(f"/api/dashboard/invoices/{inv.id}/escalate")
    assert resp.status_code == 200
    assert resp.json()["status"] == InvoiceStatus.HUMAN_REVIEW


def test_write_off_removes_the_invoice_from_the_outstanding_total(api, session, merchant, customer):
    inv = add_invoice(
        session, merchant, customer, number="WO1", amount=500_000, status=InvoiceStatus.CHASING
    )
    resp = api.post(
        f"/api/dashboard/invoices/{inv.id}/write-off",
        headers={"X-Admin-Key": settings.admin_api_key},
    )
    assert resp.status_code == 200

    # The endpoint commits through its own session, so this one still holds the
    # pre-write-off copy of the row.
    session.expire_all()
    assert compute_metrics(session).total_overdue_paise == 0
