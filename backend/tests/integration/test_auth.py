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
from tests.integration.conftest import TEST_OPERATOR_PASSWORD, TEST_OPERATOR_USERNAME

#: Endpoints that are public on purpose, each with the reason it is safe.
#:
#: This is the ONLY hand-maintained list in this file, and it is deliberately an
#: allowlist of exceptions rather than a list of what is protected. A list of
#: protected routes goes stale the moment someone adds an endpoint and forgets to
#: append to it — which is exactly the mistake these tests exist to catch. Every
#: other path is discovered from the OpenAPI schema at runtime.
PUBLIC_BY_DESIGN = {
    "/health": "deployment probe; exposes no customer data",
    "/live": "deployment probe",
    "/ready": "deployment probe",
    "/api/auth/login": "how a credential is obtained; rate limited",
    "/api/auth/logout": "clears a cookie",
    "/api/auth/modes": (
        "says which sign-in routes exist and nothing else. The login page is "
        "unauthenticated by definition, so it cannot ask a gated endpoint whether to "
        "render the reviewer button — and a button that can only 404 is worse than none"
    ),
    "/api/auth/reviewer": (
        "the second way a credential is obtained, alongside login. Issues a session "
        "only for an account whose role is auditor, and read-only is then enforced by "
        "app.api.deps rejecting every non-GET — so this hands out no write access. "
        "404s entirely unless REVIEWER_ACCESS_ENABLED is set"
    ),
    "/api/live/auth/register": "public live account enrollment; feature-flagged",
    "/api/live/auth/verify-email": "public email verification token exchange",
    "/api/live/auth/login": "public live credential exchange",
    "/api/live/auth/refresh": "public live refresh-token exchange",
    "/api/live/auth/logout": "public live cookie clearing",
    "/api/live/billing/plans": (
        "the published price list — slug, name, amount, and included caps, and nothing "
        "merchant- or customer-owned. The pricing page is unauthenticated by "
        "definition, and prices a prospect cannot read are not prices"
    ),
    "/api/live/auth/forgot-password": "public non-enumerating reset request",
    "/api/live/auth/reset-password": "public password-reset token exchange",
    "/api/live/auth/accept-invite": "public invitation enrollment token exchange",
}

#: Razorpay cannot log in. Proven by an HMAC over the raw body instead, so these
#: reject with 400 (bad signature) rather than 401 (no session).
#: Routes authenticated by a provider signature over the raw body rather than by a
#: session. A provider cannot log in, so 401 would be wrong; these must answer 400
#: to an unsigned payload. Billing lives outside /api/webhooks/ because it is the
#: platform's own Razorpay account rather than a merchant's, and the two use
#: different secrets — but it is verified the same way.
SIGNATURE_GATED_PREFIX = ("/api/webhooks/", "/api/live/billing/webhook")

#: The multi-tenant surface. These require a live session and an explicit
#: X-Merchant-ID; the tenancy-free admin key is refused by design.
LIVE_PREFIX = "/api/live/"


def _discover(app_client, *, methods: tuple[str, ...]) -> list[tuple[str, str]]:
    """Every routed (method, path) from the live OpenAPI schema."""
    spec = app_client.get("/openapi.json").json()
    found = []
    for path, operations in spec["paths"].items():
        for method in operations:
            verb = method.upper()
            if verb in methods:
                found.append((verb, path))
    return sorted(found)


def _concrete(path: str) -> str:
    """Fill path parameters so the route matches without needing real records."""
    import uuid as _uuid

    for segment in path.split("/"):
        if segment.startswith("{"):
            path = path.replace(segment, str(_uuid.uuid4()))
    return path


@pytest.fixture
def api(session):
    with TestClient(create_app()) as c:
        yield c


# ===========================================================================
# Unauthenticated access.
# ===========================================================================


def test_every_read_rejects_anonymous_callers(api):
    """An invoice ledger is customer PII. Public read is a breach on its own.

    Discovered from the schema, so a new GET endpoint is covered the moment it exists.
    """
    unprotected = [
        f"GET {path}"
        for _, path in _discover(api, methods=("GET",))
        if path not in PUBLIC_BY_DESIGN
        and not path.startswith(SIGNATURE_GATED_PREFIX)
        and path not in ("/openapi.json", "/docs", "/redoc", "/docs/oauth2-redirect")
        and api.get(_concrete(path)).status_code != 401
    ]
    assert not unprotected, "readable without a credential:\n  " + "\n  ".join(unprotected)


def test_every_action_rejects_anonymous_callers(api):
    unprotected = [
        f"{verb} {path}"
        for verb, path in _discover(api, methods=("POST", "PUT", "PATCH", "DELETE"))
        if path not in PUBLIC_BY_DESIGN
        and not path.startswith(SIGNATURE_GATED_PREFIX)
        and api.request(verb, _concrete(path), json={}).status_code != 401
    ]
    assert not unprotected, "state-changing without a credential:\n  " + "\n  ".join(unprotected)


def test_no_customer_data_leaks_in_the_401_body(api, merchant, customer, invoice):
    body = api.get("/api/dashboard/queue").text
    assert customer.email not in body
    assert invoice.invoice_number not in body


# ===========================================================================
# The admin key.
# ===========================================================================


def test_the_admin_key_grants_access_to_every_legacy_read(api):
    """The gate must not merely reject — the right credential has to get through.

    Scoped to the demo/legacy surface. `/api/live/**` is deliberately excluded and is
    covered by the test below, which asserts the opposite for those routes.
    """
    refused = []
    for _, path in _discover(api, methods=("GET",)):
        if path in PUBLIC_BY_DESIGN or path.startswith(SIGNATURE_GATED_PREFIX):
            continue
        if path.startswith(LIVE_PREFIX):
            continue
        if path in ("/openapi.json", "/docs", "/redoc", "/docs/oauth2-redirect"):
            continue
        resp = api.get(_concrete(path), headers={"X-Admin-Key": settings.admin_api_key})
        # 404 is fine: a random uuid matches no record. 401 would mean the credential
        # was not accepted, which is the failure worth catching.
        if resp.status_code == 401:
            refused.append(f"GET {path}")
    assert not refused, "admin key refused on:\n  " + "\n  ".join(refused)


def test_the_admin_key_does_not_reach_live_tenant_data(api):
    """A global service key must not open a tenant's data.

    `X-Admin-Key` predates tenancy and carries no merchant context, so there is no
    answer to "whose rows?". Accepting it on a live route would mean either serving
    an arbitrary tenant's data or picking one — both worse than refusing. Live routes
    require a session plus an explicit X-Merchant-ID, and a membership backs it.

    The inverse of the test above, and the reason that one had to be narrowed: this
    behaviour is the point, not a regression to be allowlisted away.
    """
    accepted = []
    for _, path in _discover(api, methods=("GET",)):
        if not path.startswith(LIVE_PREFIX):
            continue
        if path in PUBLIC_BY_DESIGN or path.startswith(SIGNATURE_GATED_PREFIX):
            continue
        resp = api.get(_concrete(path), headers={"X-Admin-Key": settings.admin_api_key})
        if resp.status_code != 401:
            accepted.append(f"GET {path} -> {resp.status_code}")
    assert not accepted, (
        "the global admin key reached live tenant routes, which carry no merchant "
        "context:\n  " + "\n  ".join(accepted)
    )


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
    resp = api.post(
        "/api/auth/login",
        json={"username": TEST_OPERATOR_USERNAME, "password": TEST_OPERATOR_PASSWORD},
    )
    assert resp.status_code == 200
    assert SESSION_COOKIE in resp.cookies
    assert resp.json()["username"] == TEST_OPERATOR_USERNAME
    assert "session_token" not in resp.json()


def test_the_session_cookie_is_not_readable_by_javascript(api):
    """httponly is what stops an XSS bug from exfiltrating the session."""
    resp = api.post(
        "/api/auth/login",
        json={"username": TEST_OPERATOR_USERNAME, "password": TEST_OPERATOR_PASSWORD},
    )
    header = resp.headers["set-cookie"].lower()
    assert "httponly" in header
    assert "samesite=lax" in header


def test_login_with_a_wrong_password_is_refused(api):
    assert (
        api.post(
            "/api/auth/login", json={"username": TEST_OPERATOR_USERNAME, "password": "wrong"}
        ).status_code
        == 401
    )


def test_unknown_username_has_the_same_public_failure(api):
    known = api.post(
        "/api/auth/login", json={"username": TEST_OPERATOR_USERNAME, "password": "wrong"}
    )
    unknown = api.post("/api/auth/login", json={"username": "does-not-exist", "password": "wrong"})
    assert (known.status_code, known.json()) == (unknown.status_code, unknown.json())


def test_account_locks_after_repeated_failures(api):
    for _ in range(5):
        assert (
            api.post(
                "/api/auth/login",
                json={"username": TEST_OPERATOR_USERNAME, "password": "wrong"},
            ).status_code
            == 401
        )
    assert (
        api.post(
            "/api/auth/login",
            json={"username": TEST_OPERATOR_USERNAME, "password": TEST_OPERATOR_PASSWORD},
        ).status_code
        == 401
    )


def test_disabled_account_is_immediately_revoked(api, session, operator_account):
    api.post(
        "/api/auth/login",
        json={"username": TEST_OPERATOR_USERNAME, "password": TEST_OPERATOR_PASSWORD},
    )
    assert api.get("/api/dashboard/overview").status_code == 200
    operator_account.is_active = False
    session.add(operator_account)
    session.commit()
    assert api.get("/api/dashboard/overview").status_code == 401


def test_session_generation_change_immediately_revokes_existing_session(
    api, session, operator_account
):
    """Password reset/disable increments this generation in the management CLI."""
    api.post(
        "/api/auth/login",
        json={"username": TEST_OPERATOR_USERNAME, "password": TEST_OPERATOR_PASSWORD},
    )
    assert api.get("/api/dashboard/overview").status_code == 200
    operator_account.session_version += 1
    session.add(operator_account)
    session.commit()
    assert api.get("/api/dashboard/overview").status_code == 401


def test_auditor_account_is_read_only(api, session, operator_account):
    operator_account.role = "auditor"
    session.add(operator_account)
    session.commit()
    api.post(
        "/api/auth/login",
        json={"username": TEST_OPERATOR_USERNAME, "password": TEST_OPERATOR_PASSWORD},
    )
    assert api.get("/api/dashboard/overview").status_code == 200
    assert api.post("/api/admin/run-cycle?dry_run=true").status_code == 403


def test_login_with_an_empty_password_is_refused(api):
    assert (
        api.post(
            "/api/auth/login", json={"username": TEST_OPERATOR_USERNAME, "password": ""}
        ).status_code
        == 422
    )


def test_a_session_cookie_grants_read_access(api):
    api.post(
        "/api/auth/login",
        json={"username": TEST_OPERATOR_USERNAME, "password": TEST_OPERATOR_PASSWORD},
    )
    assert api.get("/api/dashboard/overview").status_code == 200


def test_logout_ends_the_session(api):
    api.post(
        "/api/auth/login",
        json={"username": TEST_OPERATOR_USERNAME, "password": TEST_OPERATOR_PASSWORD},
    )
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


# ===========================================================================
# The guard that outlives this review.
# ===========================================================================


def test_every_endpoint_is_gated_or_deliberately_public(api):
    """Walks the whole OpenAPI schema and calls each endpoint with no credential.

    This is the test that matters six months from now. Any new route that serves
    merchant data and forgets the gate fails here, rather than being discovered by
    whoever finds the URL. Adding an endpoint to the exemption list is a deliberate,
    reviewable act; forgetting a dependency is not.
    """
    import uuid as _uuid

    #: Public by design, each for a stated reason.
    public_by_design = PUBLIC_BY_DESIGN or {
        "/health": "deployment probe, exposes no data",
        "/live": "deployment probe",
        "/ready": "deployment probe",
        "/api/auth/login": "how a credential is obtained; rate limited",
        "/api/auth/logout": "clears a cookie",
    }
    #: Razorpay cannot log in. Proven by HMAC over the raw body instead.
    signature_gated = SIGNATURE_GATED_PREFIX

    spec = api.get("/openapi.json").json()
    unprotected = []

    for path, operations in spec["paths"].items():
        for method in operations:
            verb = method.upper()
            if verb not in ("GET", "POST", "PUT", "PATCH", "DELETE"):
                continue

            concrete = path
            for segment in path.split("/"):
                if segment.startswith("{"):
                    concrete = concrete.replace(segment, str(_uuid.uuid4()))

            response = api.request(verb, concrete, json={} if verb != "GET" else None)

            if path in public_by_design:
                assert response.status_code < 500, f"{verb} {path} is broken"
            elif path.startswith(signature_gated):
                # 400 = rejected on signature. A 401 would mean it wrongly expects a
                # session; a 200 would mean it accepts unsigned payloads.
                assert response.status_code == 400, f"{verb} {path} -> {response.status_code}"
            elif response.status_code != 401:
                unprotected.append(f"{verb} {path} -> {response.status_code}")

    assert not unprotected, "endpoints served without a credential:\n  " + "\n  ".join(unprotected)
