"""Authentication and authorization. P0 security.

Before this gate existed, every dashboard read was public: customer names, email
addresses, amounts owed, and the full audit trail were served to anyone who knew the
URL. These tests exist so that cannot silently return.
"""

import time

import pytest
from fastapi.testclient import TestClient

from app.core.config import settings
from app.core.security import (
    SESSION_COOKIE,
    create_session_token,
    verify_session_token,
)
from app.main import create_app

#: Endpoints that expose merchant or customer data, or change state.
PROTECTED_READS = [
    "/api/dashboard/overview",
    "/api/dashboard/queue",
    "/api/dashboard/promises",
    "/api/dashboard/audit",
    "/api/invoices",
]

PROTECTED_ACTIONS = [
    ("POST", "/api/admin/run-cycle"),
    ("POST", "/api/invoices/provision-batch"),
]


@pytest.fixture
def api(session):
    with TestClient(create_app()) as c:
        yield c


# ===========================================================================
# Unauthenticated access.
# ===========================================================================


@pytest.mark.parametrize("path", PROTECTED_READS)
def test_reads_reject_anonymous_callers(api, path):
    """An invoice ledger is customer PII. Public read is a breach on its own."""
    assert api.get(path).status_code == 401


@pytest.mark.parametrize(("method", "path"), PROTECTED_ACTIONS)
def test_actions_reject_anonymous_callers(api, method, path):
    assert api.request(method, path).status_code == 401


def test_no_customer_data_leaks_in_the_401_body(api, merchant, customer, invoice):
    body = api.get("/api/dashboard/queue").text
    assert customer.email not in body
    assert invoice.invoice_number not in body


# ===========================================================================
# The admin key.
# ===========================================================================


@pytest.mark.parametrize("path", PROTECTED_READS)
def test_the_admin_key_grants_access(api, path):
    resp = api.get(path, headers={"X-Admin-Key": settings.admin_api_key})
    assert resp.status_code == 200


def test_a_wrong_admin_key_is_refused(api):
    resp = api.get("/api/dashboard/overview", headers={"X-Admin-Key": "not-the-key"})
    assert resp.status_code == 401


def test_an_empty_admin_key_is_refused(api):
    assert api.get("/api/dashboard/overview", headers={"X-Admin-Key": ""}).status_code == 401


def test_a_key_that_is_a_prefix_of_the_real_one_is_refused(api):
    """compare_digest, not startswith."""
    resp = api.get(
        "/api/dashboard/overview",
        headers={"X-Admin-Key": settings.admin_api_key[:-1]},
    )
    assert resp.status_code == 401


# ===========================================================================
# Password login and session cookies.
# ===========================================================================


def test_login_with_the_right_password_issues_a_session(api):
    resp = api.post("/api/auth/login", json={"password": settings.dashboard_password})
    assert resp.status_code == 200
    assert SESSION_COOKIE in resp.cookies


def test_the_session_cookie_is_not_readable_by_javascript(api):
    """httponly is what stops an XSS bug from exfiltrating the session."""
    resp = api.post("/api/auth/login", json={"password": settings.dashboard_password})
    header = resp.headers["set-cookie"].lower()
    assert "httponly" in header
    assert "samesite=lax" in header


def test_login_with_a_wrong_password_is_refused(api):
    assert api.post("/api/auth/login", json={"password": "wrong"}).status_code == 401


def test_login_with_an_empty_password_is_refused(api):
    assert api.post("/api/auth/login", json={"password": ""}).status_code == 422


def test_a_session_cookie_grants_read_access(api):
    api.post("/api/auth/login", json={"password": settings.dashboard_password})
    assert api.get("/api/dashboard/overview").status_code == 200


def test_logout_ends_the_session(api):
    api.post("/api/auth/login", json={"password": settings.dashboard_password})
    assert api.get("/api/dashboard/overview").status_code == 200

    api.post("/api/auth/logout")
    api.cookies.clear()
    assert api.get("/api/dashboard/overview").status_code == 401


# ===========================================================================
# Token integrity.
# ===========================================================================


def test_a_valid_token_verifies():
    assert verify_session_token(create_session_token()) == "operator"


def test_an_expired_token_is_rejected():
    assert verify_session_token(create_session_token(ttl_seconds=-1)) is None


def test_a_token_expiring_now_is_rejected():
    token = create_session_token(ttl_seconds=0)
    time.sleep(0.01)
    assert verify_session_token(token) is None


@pytest.mark.parametrize(
    "tampered",
    [
        None,
        "",
        "garbage",
        "v1.operator.9999999999",  # no signature
        "v1.operator.9999999999.wrongsignature",
        "v2.operator.9999999999.sig",  # unknown version
        "v1.operator.notanumber.sig",
    ],
)
def test_malformed_tokens_fail_closed(tampered):
    assert verify_session_token(tampered) is None


def test_extending_the_expiry_invalidates_the_signature():
    """The expiry is signed, so it cannot be edited to grant a longer session."""
    token = create_session_token(ttl_seconds=60)
    version, subject, expires, signature = token.split(".")
    forged = f"{version}.{subject}.{int(expires) + 100_000}.{signature}"
    assert verify_session_token(forged) is None


def test_a_token_signed_with_another_secret_is_rejected(monkeypatch):
    token = create_session_token()
    monkeypatch.setattr(settings, "session_secret", "a-completely-different-secret")
    assert verify_session_token(token) is None


def test_a_forged_cookie_does_not_grant_access(api):
    api.cookies.set(SESSION_COOKIE, "v1.operator.9999999999.forged")
    assert api.get("/api/dashboard/overview").status_code == 401


# ===========================================================================
# Endpoints that must stay open.
# ===========================================================================


def test_health_stays_public(api):
    """Deployment platforms probe this before any credential exists."""
    assert api.get("/health").status_code == 200
    assert api.get("/live").status_code == 200


def test_the_webhook_endpoint_uses_signatures_not_sessions(api):
    """Razorpay cannot log in. It proves itself with an HMAC over the body."""
    resp = api.post("/api/webhooks/razorpay", content=b"{}")
    assert resp.status_code == 400  # rejected for signature, not 401
