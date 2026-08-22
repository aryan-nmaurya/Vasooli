"""Global test configuration.

Settings are read at import time, so the test environment must be established before
anything under app.* is imported — including DATABASE_URL, which points at a dedicated
test database.

Why the redirect exists: integration tests truncate every table between cases. Pointed
at the development database they silently destroy seeded demo data, and the first
symptom is a demo with no invoices.

Why it is conditional: CI supplies its own DATABASE_URL, with credentials. Overwriting
it unconditionally replaced `postgres://vasooli:vasooli@...` with a credential-less
local URL, and libpq then fell back to the OS user — which is why CI failed with
`role "root" does not exist` and `fe_sendauth: no password supplied`.

Resolution order:
  1. VASOOLI_TEST_DATABASE_URL   — explicit override, wins everywhere
  2. DATABASE_URL already in the environment — CI, or a developer who exported one
  3. a local default

Note that (2) does not pick up a developer's `.env`: pydantic-settings reads that file
itself, so DATABASE_URL is absent from os.environ locally and the default applies. The
safety assertion in tests/integration/conftest.py refuses to run against any URL that
does not look like a test database, which covers the case of someone exporting a real
one into their shell.
"""

import os

DEFAULT_TEST_DATABASE_URL = "postgresql://localhost:5432/vasooli_test"

os.environ["ENVIRONMENT"] = "test"
os.environ["DATABASE_URL"] = (
    os.environ.get("VASOOLI_TEST_DATABASE_URL")
    or os.environ.get("DATABASE_URL")
    or DEFAULT_TEST_DATABASE_URL
)

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.main import create_app  # noqa: E402


@pytest.fixture(scope="session")
def client() -> TestClient:
    with TestClient(create_app()) as c:
        yield c
