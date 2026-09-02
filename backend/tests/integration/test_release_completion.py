"""Acceptance tests for the product and financial gaps from the final release audit."""

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
from app.models import DisputeCase, ErpConnection, ExternalPayment, Invoice, PaymentLink
from app.services.billing import subscription_is_active
from app.services.erp import sync_connection
from tests.integration.test_erp_sync import _row
from tests.integration.test_live_identity import _live_user


@pytest.fixture
def api(session, monkeypatch):
    monkeypatch.setattr(settings, "live_registration_enabled", True)
    with TestClient(create_app()) as client:
        yield client


@pytest.fixture
def connection(session, merchant) -> ErpConnection:
    row = ErpConnection(merchant_id=merchant.id, provider="zoho", status="connected")
    session.add(row)
    session.commit()
    return row


def _import_one(api: TestClient, merchant_id: str) -> str:
    csv = (
        "invoice_number,customer_name,customer_email,amount_inr,issued_at,due_at\n"
        "LIVE-1001,Buyer Ltd,ap@buyer.example.com,1000,2026-07-01,2026-08-01"
    )
    preview = api.post(
        "/api/live/invoices/csv/import",
        headers={"X-Merchant-ID": merchant_id},
        files={"file": ("ledger.csv", csv.encode(), "text/csv")},
        data={"dry_run": "true"},
    )
    assert preview.status_code == 200
    assert preview.json()["would_import"] == 1
    committed = api.post(
        "/api/live/invoices/csv/import",
        headers={"X-Merchant-ID": merchant_id},
        files={"file": ("ledger.csv", csv.encode(), "text/csv")},
        data={"dry_run": "false"},
    )
    assert committed.status_code == 200
    return api.get("/api/live/workspace/queue", headers={"X-Merchant-ID": merchant_id}).json()[0][
        "id"
    ]


def test_live_workspace_exposes_queue_detail_metrics_and_audit(api):
    merchant_id, _ = _live_user(api, "workspace-owner@example.com")
    invoice_id = _import_one(api, merchant_id)
    headers = {"X-Merchant-ID": merchant_id}

    assert api.get("/api/live/workspace/overview", headers=headers).json()["invoices_total"] == 1
    detail = api.get(f"/api/live/workspace/invoices/{invoice_id}", headers=headers)
    assert detail.status_code == 200
    assert detail.json()["invoice_number"] == "LIVE-1001"
    audit = api.get("/api/live/workspace/audit", headers=headers)
    assert audit.status_code == 200
    assert any(row["action"] == "invoice_ingested" for row in audit.json())


def test_registration_does_not_enumerate_an_existing_email(api):
    _live_user(api, "private-owner@example.com")
    repeated = api.post(
        "/api/live/auth/register",
        json={
            "email": "private-owner@example.com",
            "password": "CorrectHorse9Battery",
            "legal_business_name": "Private Owner",
            "country": "IN",
            "timezone": "Asia/Kolkata",
            "accept_terms": True,
            "accept_privacy": True,
        },
    )
    assert repeated.status_code == 201
    assert repeated.json()["status"] == "verification_required"


def test_an_expired_trial_is_not_entitled(session, merchant):
    merchant.mode = "live"
    merchant.is_demo = False
    merchant.onboarding_state = {"trial_ends_at": (utcnow() - timedelta(seconds=1)).isoformat()}
    session.add(merchant)
    session.commit()
    assert subscription_is_active(session, merchant.id) is False


def test_erp_updates_amount_and_applies_payment_and_credit(session, merchant, connection):
    sync_connection(session, connection, fixture_rows=[_row("erp-1", "ERP-1")])
    connection.cursor = None
    session.add(connection)
    session.commit()
    sync_connection(
        session,
        connection,
        fixture_rows=[
            _row(
                "erp-1",
                "ERP-1",
                source_version="v2",
                amount_paise=600_000,
                paid_paise=200_000,
                credited_paise=50_000,
            )
        ],
    )
    invoice = session.exec(select(Invoice).where(Invoice.invoice_number == "ERP-1")).one()
    assert invoice.amount_paise == 600_000
    assert invoice.amount_paid_paise == 250_000
    assert invoice.status == InvoiceStatus.PARTIALLY_PAID
    assert len(session.exec(select(ExternalPayment)).all()) == 2


def test_erp_cancellation_stops_recovery(session, merchant, connection):
    sync_connection(session, connection, fixture_rows=[_row("erp-1", "ERP-CANCEL")])
    connection.cursor = None
    session.add(connection)
    session.commit()
    sync_connection(
        session,
        connection,
        fixture_rows=[_row("erp-1", "ERP-CANCEL", source_version="v2", tombstoned=True)],
    )
    invoice = session.exec(select(Invoice).where(Invoice.invoice_number == "ERP-CANCEL")).one()
    assert invoice.status == InvoiceStatus.WRITTEN_OFF


def _signed_post(api: TestClient, payload: dict, event_id: str):
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


def _paid_invoice(session, invoice) -> PaymentLink:
    link = PaymentLink(
        invoice_id=invoice.id,
        razorpay_payment_link_id="plink_RELEASE",
        reference_id="vsl-release",
        short_url="https://rzp.io/release",
        amount_expected_paise=invoice.amount_paise,
        amount_paid_paise=invoice.amount_paise,
        status="paid",
    )
    invoice.link_paid_paise = invoice.amount_paise
    invoice.amount_paid_paise = invoice.amount_paise
    invoice.status = InvoiceStatus.RECOVERED
    invoice.recovered_at = utcnow()
    session.add_all([invoice, link])
    session.commit()
    return link


def test_refund_reopens_a_recovered_invoice(api, session, invoice):
    _paid_invoice(session, invoice)
    payload = {
        "event": "refund.processed",
        "payload": {
            "refund": {
                "entity": {
                    "id": "rfnd_RELEASE",
                    "amount": 500_000,
                    "notes": {"invoice_id": str(invoice.id)},
                }
            }
        },
    }
    assert _signed_post(api, payload, "evt_refund_release").json()["status"] == "processed"
    session.refresh(invoice)
    assert invoice.refunded_paise == 500_000
    assert invoice.amount_paid_paise == invoice.amount_paise - 500_000
    assert invoice.status == InvoiceStatus.PARTIALLY_PAID


def test_chargeback_pauses_recovery_and_debits_a_lost_case(api, session, invoice):
    _paid_invoice(session, invoice)
    payload = {
        "event": "payment.dispute.lost",
        "payload": {
            "dispute": {
                "entity": {
                    "id": "disp_RELEASE",
                    "payment_id": "pay_RELEASE",
                    "amount": 700_000,
                    "status": "lost",
                    "reason_code": "fraudulent",
                    "notes": {"invoice_id": str(invoice.id)},
                }
            }
        },
    }
    assert _signed_post(api, payload, "evt_dispute_release").json()["status"] == "processed"
    session.refresh(invoice)
    assert invoice.chargeback_paise == 700_000
    assert invoice.status == InvoiceStatus.HUMAN_REVIEW
    assert session.exec(select(DisputeCase).where(DisputeCase.invoice_id == invoice.id)).one()
