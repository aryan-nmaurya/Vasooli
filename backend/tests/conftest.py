"""Global test configuration.

Settings are read at import time, so the test environment must be established before
anything under app.* is imported — including DATABASE_URL, which is redirected to a
dedicated test database.

That redirect is not a nicety. Integration tests truncate every table between cases;
pointed at the development database they would silently destroy seeded demo data
between runs, and the first symptom would be a demo that no longer has invoices.
"""

import os

os.environ["ENVIRONMENT"] = "test"
os.environ.setdefault("VASOOLI_TEST_DATABASE_URL", "postgresql://localhost:5432/vasooli_test")
os.environ["DATABASE_URL"] = os.environ["VASOOLI_TEST_DATABASE_URL"]

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.main import create_app  # noqa: E402


@pytest.fixture(scope="session")
def client() -> TestClient:
    with TestClient(create_app()) as c:
        yield c
