"""Phase 1 live identity, tenant scoping and per-tenant invoice identities."""

import uuid
from datetime import UTC, date, datetime

import pytest
from fastapi.testclient import TestClient
from sqlmodel import select

from app.core.config import settings
from app.main import create_app
from app.models import Invoice, Merchant, Role, UserSession
from app.services.messaging import reply_address_for
from app.services.provisioning import reference_id_for


@pytest.fixture
def api(session, monkeypatch):
    monkeypatch.setattr(settings, "live_registration_enabled", True)
    with TestClient(create_app()) as client:
        yield client


def _live_user(api: TestClient, email: str) -> tuple[str, str]:
    response = api.post(
        "/api/live/auth/register",
        json={
            "email": email,
            "password": "CorrectHorse9Battery",
            "legal_business_name": f"{email} Traders",
            "country": "IN",
            "timezone": "Asia/Kolkata",
            "accept_terms": True,
            "accept_privacy": True,
        },
    )
    assert response.status_code == 201
    merchant_id = response.json()["merchant_id"]
    token = response.json()["verification_token"]
    assert token
    assert api.post("/api/live/auth/verify-email", json={"token": token}).status_code == 200
    login = api.post(
        "/api/live/auth/login",
        json={"email": email, "password": "CorrectHorse9Battery"},
    )
    assert login.status_code == 200
    return merchant_id, api.cookies.get("vasooli_live_refresh")


def _row(number: str, email: str) -> dict:
    return {
        "invoice_number": number,
        "customer_name": "Shared Buyer",
        "customer_email": email,
        "amount_inr": "1000",
        "issued_at": date(2026, 7, 1).isoformat(),
        "due_at": date(2026, 8, 1).isoformat(),
    }


def test_two_live_merchants_can_import_same_invoice_number_without_crossover(api, session):
    first, _ = _live_user(api, "owner-one@example.com")
    first_import = api.post(
        "/api/live/invoices/batch",
        headers={"X-Merchant-ID": first},
        json={"invoices": [_row("INV-001", "buyer-one@example.com")]},
    )
    assert first_import.status_code == 202

    second, _ = _live_user(api, "owner-two@example.com")
    second_import = api.post(
        "/api/live/invoices/batch",
        headers={"X-Merchant-ID": second},
        json={"invoices": [_row("INV-001", "buyer-two@example.com")]},
    )
    assert second_import.status_code == 202

    rows = session.exec(select(Invoice).where(Invoice.invoice_number == "INV-001")).all()
    assert {str(row.merchant_id) for row in rows} == {first, second}
    # The second user's cookie cannot read the first tenant even with its object id.
    first_row = next(row for row in rows if str(row.merchant_id) == first)
    assert (
        api.get(
            f"/api/live/invoices/{first_row.id}",
            headers={"X-Merchant-ID": second},
        ).status_code
        == 404
    )


def test_live_invoice_identities_are_tenant_safe(session):
    merchants = [
        Merchant(name="One", contact_email="one@example.com", mode="live", is_demo=False),
        Merchant(name="Two", contact_email="two@example.com", mode="live", is_demo=False),
    ]
    session.add_all(merchants)
    session.commit()
    invoices = [
        Invoice(
            id=uuid.uuid4(),
            merchant_id=merchant.id,
            customer_id=uuid.uuid4(),
            invoice_number="INV-001",
            amount_paise=1000,
            issued_at=datetime(2026, 7, 1, tzinfo=UTC),
            due_at=datetime(2026, 8, 1, tzinfo=UTC),
        )
        for merchant in merchants
    ]
    assert reference_id_for(invoices[0], is_demo=False) != reference_id_for(
        invoices[1], is_demo=False
    )
    first_alias = reply_address_for("INV-001", reply_token=invoices[0].reply_token, is_demo=False)
    second_alias = reply_address_for("INV-001", reply_token=invoices[1].reply_token, is_demo=False)
    assert first_alias != second_alias


def test_refresh_rotation_detects_reuse_and_revokes_family(api, session):
    _merchant, old_refresh = _live_user(api, "rotate@example.com")
    assert old_refresh
    assert api.post("/api/live/auth/refresh").status_code == 200
    api.cookies.set("vasooli_live_refresh", old_refresh, path="/api/live/auth")
    replay = api.post("/api/live/auth/refresh")
    assert replay.status_code == 401
    assert all(row.revoked_at is not None for row in session.exec(select(UserSession)).all())


def test_invitation_enrollment_gets_the_intended_least_privilege_role(api, session):
    merchant_id, _ = _live_user(api, "owner-inviter@example.com")
    analyst = session.exec(
        select(Role).where(Role.merchant_id == uuid.UUID(merchant_id), Role.slug == "analyst")
    ).one()
    invitation = api.post(
        "/api/live/team/invitations",
        headers={"X-Merchant-ID": merchant_id},
        json={"email": "analyst@example.com", "role_id": str(analyst.id)},
    )
    assert invitation.status_code == 201
    token = invitation.json()["invitation_token"]
    assert token

    accepted = api.post(
        "/api/live/auth/accept-invite",
        json={"token": token, "password": "AnalystPassword9"},
    )
    assert accepted.status_code == 200

    login = api.post(
        "/api/live/auth/login",
        json={"email": "analyst@example.com", "password": "AnalystPassword9"},
    )
    assert login.status_code == 200
    denied = api.post(
        "/api/live/invoices/batch",
        headers={"X-Merchant-ID": merchant_id},
        json={"invoices": [_row("INV-DENIED", "buyer@example.com")]},
    )
    assert denied.status_code == 403
