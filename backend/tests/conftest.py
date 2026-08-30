"""Global test configuration.

Settings are read at import time, so the test environment must be fully established
before anything under app.* is imported.

Every value an external integration depends on is pinned here rather than inherited
from the developer's .env. That is not tidiness — before this was pinned, running
`pytest` with a working .env sent real email through Resend and called the real Gemini
API, because EMAIL_DRY_RUN was false and both keys were live. A test suite that
contacts customers and burns API quota is a hazard, and one whose behaviour depends on
which machine it runs on is not a test suite.

DATABASE_URL is the one deliberate exception: CI supplies its own, with credentials.
See the resolution order below.
"""

import os

# --- External integrations: pinned OFF, with obviously fake credentials -----------
# A test that reaches the network is either slow and flaky or, worse, effective.
os.environ["EMAIL_DRY_RUN"] = "true"
os.environ["EMAIL_REDIRECT_TO"] = "tests@example.invalid"
os.environ["EMAIL_FROM"] = "Vasooli Tests <tests@example.invalid>"
os.environ["RESEND_API_KEY"] = "re_TEST_PLACEHOLDER"
os.environ["SENDGRID_API_KEY"] = ""
# "PLACEHOLDER" is what app.ai.client checks for: with it set, the LLM client reports
# failure immediately instead of attempting a call, so every AI path under test takes
# its deterministic branch unless a fake client is injected.
os.environ["GOOGLE_API_KEY"] = "PLACEHOLDER"
os.environ["RAZORPAY_KEY_ID"] = "rzp_test_PLACEHOLDER"
os.environ["RAZORPAY_KEY_SECRET"] = "PLACEHOLDER"
os.environ["RAZORPAY_WEBHOOK_SECRET"] = "test-webhook-secret"
os.environ["RAZORPAY_MIN_REQUEST_INTERVAL_SECONDS"] = "0"
os.environ["ADMIN_API_KEY"] = "test-admin-key"
os.environ["SESSION_SECRET"] = "test-session-secret-not-for-production"
os.environ["RESEND_INBOUND_WEBHOOK_SECRET"] = "whsec_dGVzdC13ZWJob29rLXNlY3JldA=="
os.environ["RESEND_DELIVERY_WEBHOOK_SECRET"] = "whsec_ZGVsaXZlcnktdGVzdC1zZWNyZXQtZGlmZmVyZW50IQ=="
os.environ["INBOUND_EMAIL_WEBHOOK_SECRET"] = "test-normalizer-secret"
os.environ["SCHEDULER_ENABLED"] = "false"
os.environ["DEMO_TIME_OFFSET_DAYS"] = "0"
os.environ["ENVIRONMENT"] = "test"

# --- Database -------------------------------------------------------------------
# Integration tests truncate every table. Pointed at the development database they
# silently destroy seeded demo data, and the first symptom is a demo with no invoices.
#
# Resolution order:
#   1. VASOOLI_TEST_DATABASE_URL   — explicit override, wins everywhere
#   2. DATABASE_URL already in the environment — CI, or an exported shell variable
#   3. a local default
#
# (2) does not pick up a developer's .env: pydantic-settings reads that file itself,
# so DATABASE_URL is absent from os.environ locally and the default applies. The
# assertion in tests/integration/conftest.py refuses any URL that does not look like a
# test database, covering the case of a real one exported into a shell.
DEFAULT_TEST_DATABASE_URL = "postgresql://localhost:5432/vasooli_test"

os.environ["DATABASE_URL"] = (
    os.environ.get("VASOOLI_TEST_DATABASE_URL")
    or os.environ.get("DATABASE_URL")
    or DEFAULT_TEST_DATABASE_URL
)

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.core.config import settings  # noqa: E402
from app.main import create_app  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def _no_live_integrations() -> None:
    """Fail loudly if the pinning above ever stops working.

    Cheaper than discovering it from a customer's inbox.
    """
    assert settings.email_dry_run is True, "tests must never send live email"
    assert "PLACEHOLDER" in settings.google_api_key, "tests must not call a live LLM"
    assert settings.scheduler_enabled is False, "tests must not run the scheduler"


@pytest.fixture(scope="session")
def client() -> TestClient:
    with TestClient(create_app()) as c:
        yield c


@pytest.fixture
def admin_headers() -> dict[str, str]:
    return {"X-Admin-Key": settings.admin_api_key}
