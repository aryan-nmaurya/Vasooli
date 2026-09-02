"""Phase 1 live identity, tenant scoping and per-tenant invoice identities."""

import uuid
from datetime import UTC, date, datetime

import pytest
from fastapi.testclient import TestClient
from sqlmodel import select

from app.core.config import settings
from app.main import create_app
from app.models import Invoice, Merchant, Role, UserSession
from app.services.auth_email import AuthEmailError
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
    assert (
        api.post(
            "/api/live/auth/verify-email-code",
            json={"email": email, "code": token},
        ).status_code
        == 200
    )
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


def test_pending_registration_can_request_a_fresh_verification_code(api):
    payload = {
        "email": "retry-verification@example.com",
        "password": "CorrectHorse9Battery",
        "legal_business_name": "Retry Verification Traders",
        "country": "IN",
        "timezone": "Asia/Kolkata",
        "accept_terms": True,
        "accept_privacy": True,
    }
    first = api.post("/api/live/auth/register", json=payload)
    retry_payload = {**payload, "password": "UpdatedHorse9Battery"}
    second = api.post("/api/live/auth/register", json=retry_payload)

    assert first.status_code == second.status_code == 201
    first_code = first.json()["verification_token"]
    second_code = second.json()["verification_token"]
    assert first_code and second_code and first_code != second_code
    assert (
        api.post(
            "/api/live/auth/verify-email-code",
            json={"email": payload["email"], "code": first_code},
        ).status_code
        == 400
    )
    assert (
        api.post(
            "/api/live/auth/verify-email-code",
            json={"email": payload["email"], "code": second_code},
        ).status_code
        == 200
    )
    assert (
        api.post(
            "/api/live/auth/login",
            json={"email": payload["email"], "password": payload["password"]},
        ).status_code
        == 401
    )
    assert (
        api.post(
            "/api/live/auth/login",
            json={"email": payload["email"], "password": retry_payload["password"]},
        ).status_code
        == 200
    )


def test_failed_retry_rolls_back_password_and_code_changes(api, monkeypatch):
    from app.api import live_auth as live_auth_api

    payload = {
        "email": "retry-rollback@example.com",
        "password": "OriginalHorse9Battery",
        "legal_business_name": "Retry Rollback Traders",
        "country": "IN",
        "timezone": "Asia/Kolkata",
        "accept_terms": True,
        "accept_privacy": True,
    }
    first = api.post("/api/live/auth/register", json=payload)
    assert first.status_code == 201
    original_code = first.json()["verification_token"]
    original_sender = live_auth_api.send_auth_email

    def fail_delivery(**_kwargs):
        raise AuthEmailError("provider unavailable")

    monkeypatch.setattr(live_auth_api, "send_auth_email", fail_delivery)
    failed_retry = api.post(
        "/api/live/auth/register",
        json={**payload, "password": "UncommittedHorse8Battery"},
    )
    assert failed_retry.status_code == 503
    monkeypatch.setattr(live_auth_api, "send_auth_email", original_sender)

    assert (
        api.post(
            "/api/live/auth/verify-email-code",
            json={"email": payload["email"], "code": original_code},
        ).status_code
        == 200
    )
    assert (
        api.post(
            "/api/live/auth/login",
            json={"email": payload["email"], "password": payload["password"]},
        ).status_code
        == 200
    )


def test_password_reset_revokes_sessions_and_replaces_the_password(api):
    email = "password-reset@example.com"
    _merchant_id, refresh_token = _live_user(api, email)

    forgot = api.post("/api/live/auth/forgot-password", json={"email": email})
    assert forgot.status_code == 200
    reset_token = forgot.json()["reset_token"]
    assert reset_token

    reset = api.post(
        "/api/live/auth/reset-password",
        json={"token": reset_token, "password": "ReplacementHorse8Battery"},
    )
    assert reset.status_code == 200

    api.cookies.set("vasooli_live_refresh", refresh_token, path="/api/live/auth")
    assert api.post("/api/live/auth/refresh").status_code == 401
    assert (
        api.post(
            "/api/live/auth/login",
            json={"email": email, "password": "CorrectHorse9Battery"},
        ).status_code
        == 401
    )
    assert (
        api.post(
            "/api/live/auth/login",
            json={"email": email, "password": "ReplacementHorse8Battery"},
        ).status_code
        == 200
    )


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


def _subscribe(session, merchant_id: str, slug: str) -> None:
    """Put a merchant on a paid plan.

    Starter includes a single seat, which the owner occupies, so any test that
    invites a second person needs a plan with room. Without this the invite is
    refused on seats and the test stops exercising what it is named for.
    """
    from app.models import BillingSubscription
    from app.services.billing import ensure_plans

    plan = next(p for p in ensure_plans(session) if p.slug == slug)
    session.add(
        BillingSubscription(merchant_id=uuid.UUID(merchant_id), plan_id=plan.id, status="active")
    )
    session.commit()


def test_invitation_enrollment_gets_the_intended_least_privilege_role(api, session):
    merchant_id, _ = _live_user(api, "owner-inviter@example.com")
    _subscribe(session, merchant_id, "growth")
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
