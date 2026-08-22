import os

# Settings are read at import time, so the test environment must be established before
# anything under app.* is imported.
os.environ.setdefault("ENVIRONMENT", "test")

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.main import create_app  # noqa: E402


@pytest.fixture(scope="session")
def client() -> TestClient:
    with TestClient(create_app()) as c:
        yield c
