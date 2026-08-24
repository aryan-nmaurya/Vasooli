"""Request-level protections. P1 security hardening.

The gap these close: a single shared dashboard password with unlimited guesses, an
unbounded request body, and responses a browser had to make its own decisions about.
"""

import json

import pytest
from fastapi.testclient import TestClient

from app.core.config import settings
from app.core.middleware import MAX_BODY_BYTES, RATE_LIMITS
from app.main import create_app


@pytest.fixture
def api(session):
    """A fresh app per test, so rate-limit counters do not leak between them."""
    with TestClient(create_app()) as c:
        yield c


# ===========================================================================
# Rate limiting.
# ===========================================================================


def test_login_is_not_brute_forceable(api):
    """The one endpoint where guessing IS the attack.

    A single shared password with unlimited attempts is the weakest point in this
    design; the limit is what makes it survivable.
    """
    limit, _ = RATE_LIMITS["/api/auth/login"]

    codes = [
        api.post("/api/auth/login", json={"password": f"guess-{i}"}).status_code
        for i in range(limit + 5)
    ]

    assert 401 in codes, "early attempts are rejected on merit"
    assert 429 in codes, "later attempts are refused outright"
    assert codes[-1] == 429


def test_a_rate_limited_response_says_when_to_come_back(api):
    limit, _ = RATE_LIMITS["/api/auth/login"]
    for i in range(limit + 2):
        resp = api.post("/api/auth/login", json={"password": f"x{i}"})
    assert resp.status_code == 429
    assert int(resp.headers["Retry-After"]) > 0


def test_the_limit_does_not_lock_out_a_correct_password_immediately(api):
    """A human mistyping twice must still be able to sign in."""
    api.post("/api/auth/login", json={"password": "wrong"})
    api.post("/api/auth/login", json={"password": "wrong-again"})
    resp = api.post("/api/auth/login", json={"password": settings.dashboard_password})
    assert resp.status_code == 200


def test_webhooks_are_not_throttled_like_logins(api):
    """Razorpay bursts on redelivery. Throttling that path would drop real payments.

    The webhook endpoint has its own protection — an HMAC signature — so a tight rate
    limit buys nothing and costs reconciliation.
    """
    webhook_limit, _ = RATE_LIMITS["/api/webhooks/"]
    login_limit, _ = RATE_LIMITS["/api/auth/login"]
    assert webhook_limit > login_limit * 10


def test_read_endpoints_have_a_working_limit(api):
    """Generous enough for a 3-second dashboard poll, finite all the same."""
    default_limit, window = RATE_LIMITS["default"]
    polls_per_window = window / 3
    assert default_limit > polls_per_window * 4


# ===========================================================================
# Request body size.
# ===========================================================================


def test_an_oversized_body_is_refused_before_parsing(api):
    resp = api.post(
        "/api/auth/login",
        content=b"x" * 16,
        headers={
            "Content-Type": "application/json",
            # The cap is checked against the declared length, so nothing large is
            # buffered in order to discover it is large.
            "Content-Length": str(MAX_BODY_BYTES + 1),
        },
    )
    assert resp.status_code == 413


def test_a_normal_body_passes(api):
    resp = api.post("/api/auth/login", json={"password": "anything"})
    assert resp.status_code in (200, 401), "rejected on credentials, not on size"


def test_the_cap_is_well_above_a_real_batch_ingest(api):
    """A few hundred invoices is the largest legitimate request."""
    row = {
        "invoice_number": "INV-1",
        "customer_name": "ABC Traders Private Limited",
        "customer_email": "accounts@abc-traders.example.com",
        "amount_inr": "42000",
        "issued_at": "2026-07-01",
        "due_at": "2026-08-01",
    }
    payload = json.dumps({"invoices": [row] * 500}).encode()
    assert len(payload) < MAX_BODY_BYTES


# ===========================================================================
# Security headers.
# ===========================================================================


@pytest.mark.parametrize(
    ("header", "expected"),
    [
        ("X-Content-Type-Options", "nosniff"),
        ("X-Frame-Options", "DENY"),
        ("Referrer-Policy", "no-referrer"),
    ],
)
def test_responses_carry_security_headers(api, header, expected):
    assert api.get("/health").headers[header] == expected


def test_the_api_forbids_being_framed_or_loading_anything(api):
    """This API serves JSON to a separate frontend. It never needs scripts or frames."""
    csp = api.get("/health").headers["Content-Security-Policy"]
    assert "default-src 'none'" in csp
    assert "frame-ancestors 'none'" in csp


def test_hsts_is_production_only(api, monkeypatch):
    """Sending HSTS from a local http server would pin the browser to https for
    localhost, which breaks every other project on the machine."""
    assert "Strict-Transport-Security" not in api.get("/health").headers


def test_headers_are_present_on_errors_too(api):
    """A 401 is still a response a browser renders."""
    resp = api.get("/api/dashboard/overview")
    assert resp.status_code == 401
    assert resp.headers["X-Content-Type-Options"] == "nosniff"


# ===========================================================================
# CORS is not authorization.
# ===========================================================================


def test_cors_is_an_allowlist_not_a_wildcard(api):
    assert "*" not in settings.cors_origins


def test_a_disallowed_origin_still_cannot_read_data(api):
    """CORS is a browser convenience. A non-browser client ignores it entirely, so
    every endpoint is gated independently — that is what actually protects the data."""
    resp = api.get("/api/dashboard/overview", headers={"Origin": "https://evil.example"})
    assert resp.status_code == 401


def test_an_allowed_origin_without_credentials_is_still_refused(api):
    resp = api.get("/api/dashboard/overview", headers={"Origin": settings.cors_origins[0]})
    assert resp.status_code == 401


# ===========================================================================
# Error responses must not leak internals.
# ===========================================================================


def test_a_failed_login_does_not_reveal_the_password(api):
    body = api.post("/api/auth/login", json={"password": "hunter2"}).text
    assert "hunter2" not in body
    assert settings.dashboard_password not in body


def test_an_invalid_signature_does_not_reveal_the_secret(api):
    resp = api.post(
        "/api/webhooks/razorpay",
        content=b'{"event":"payment_link.paid"}',
        headers={"X-Razorpay-Signature": "wrong"},
    )
    assert resp.status_code == 400
    assert settings.razorpay_webhook_secret not in resp.text


def test_a_401_does_not_name_the_expected_credential(api):
    body = api.get("/api/dashboard/overview").text
    assert settings.admin_api_key not in body
    assert settings.session_secret not in body


# ===========================================================================
# The bypass the header check alone would miss.
# ===========================================================================


def test_a_body_larger_than_its_declared_length_is_still_refused(api):
    """Content-Length is a claim, not a fact.

    A client can understate it, and a chunked request omits it entirely. Before the
    stream was counted, either one walked straight past the limit.
    """
    oversized = b"x" * (MAX_BODY_BYTES + 4096)
    resp = api.post(
        "/api/auth/login",
        content=oversized,
        headers={"Content-Type": "application/json", "Content-Length": "10"},
    )
    assert resp.status_code == 413


def test_a_chunked_oversized_body_is_refused(api):
    """No Content-Length at all — the header check has nothing to look at."""

    def chunks():
        for _ in range((MAX_BODY_BYTES // 65536) + 2):
            yield b"x" * 65536

    resp = api.post(
        "/api/auth/login",
        content=chunks(),
        headers={"Content-Type": "application/json"},
    )
    assert resp.status_code == 413


def test_a_chunked_body_under_the_limit_passes(api):
    """The limit must not break legitimate streaming clients."""

    def chunks():
        yield b'{"password":'
        yield b'"streamed"}'

    resp = api.post(
        "/api/auth/login",
        content=chunks(),
        headers={"Content-Type": "application/json"},
    )
    assert resp.status_code in (200, 401), "rejected on credentials, not on size"


def test_a_large_but_legitimate_webhook_still_reconciles(api, session, merchant, customer):
    """Razorpay payloads carry the full entity. The cap must sit far above a real one,
    and the raw bytes must reach the signature check unchanged."""
    import json

    from app.core.config import settings
    from app.integrations.razorpay_signature import compute_signature

    payload = {
        "entity": "event",
        "event": "payment_link.paid",
        "payload": {
            "payment_link": {
                "entity": {
                    "id": "plink_BIG",
                    "reference_id": "r",
                    "amount": 100,
                    "amount_paid": 100,
                    "status": "paid",
                    "notes": {f"field_{i}": "x" * 200 for i in range(50)},
                }
            }
        },
    }
    raw = json.dumps(payload).encode()
    assert len(raw) < MAX_BODY_BYTES

    resp = api.post(
        "/api/webhooks/razorpay",
        content=raw,
        headers={
            "X-Razorpay-Signature": compute_signature(raw, settings.razorpay_webhook_secret),
            "X-Razorpay-Event-Id": "evt_big",
            "Content-Type": "application/json",
        },
    )
    # Reaches processing rather than being refused for size or a mangled signature.
    assert resp.status_code == 200
