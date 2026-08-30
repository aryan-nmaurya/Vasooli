"""Database fixtures.

Integration tests run against a real Postgres schema built by Alembic — not by
`SQLModel.metadata.create_all`. The CHECK constraints, the partial unique index, and
the append-only trigger are what these tests exist to verify, and only the migration
produces them. A create_all-based fixture would pass while production was broken.
"""

import uuid
from datetime import UTC, datetime

import pytest
from alembic.config import Config
from sqlalchemy import text
from sqlmodel import Session

from alembic import command
from app.core.config import settings
from app.core.db import engine
from app.core.passwords import hash_password
from app.models import Customer, Invoice, Merchant, OperatorAccount

TEST_OPERATOR_USERNAME = "test-operator"
TEST_OPERATOR_PASSWORD = "test-operator-password"


def _ensure_test_database_exists() -> None:
    """Create the test database if it is not there yet.

    Tries the target database first and only reaches for the `postgres` maintenance
    database when that fails. In CI the database is created by the service container,
    where the connecting role may not be allowed to CREATE DATABASE at all — so the
    happy path must not depend on that privilege.
    """
    import psycopg
    from sqlalchemy.engine import make_url

    url = make_url(settings.database_url)
    dsn = url.render_as_string(hide_password=False).replace(
        "postgresql+psycopg://", "postgresql://"
    )

    try:
        with psycopg.connect(dsn, connect_timeout=5):
            return  # already there
    except psycopg.OperationalError as exc:
        if "does not exist" not in str(exc):
            raise RuntimeError(
                f"Cannot reach the test database at {url.render_as_string()}.\n"
                f"  {exc}\n"
                "  Is Postgres running? Check DATABASE_URL / VASOOLI_TEST_DATABASE_URL."
            ) from exc

    admin_dsn = (
        url.set(database="postgres")
        .render_as_string(hide_password=False)
        .replace("postgresql+psycopg://", "postgresql://")
    )
    try:
        with psycopg.connect(admin_dsn, autocommit=True, connect_timeout=5) as conn:
            conn.execute(f'CREATE DATABASE "{url.database}"')
    except psycopg.Error as exc:
        raise RuntimeError(
            f"Test database {url.database!r} does not exist and could not be created.\n"
            f"  {exc}\n"
            f"  Create it manually: createdb {url.database}"
        ) from exc


@pytest.fixture(scope="session", autouse=True)
def migrated_database():
    """Bring the test schema to head once per session."""
    assert "test" in settings.database_url, (
        f"Refusing to run integration tests against {settings.database_url!r}. "
        "These tests truncate every table; see tests/conftest.py."
    )
    _ensure_test_database_exists()

    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", settings.database_url)
    command.upgrade(cfg, "head")
    yield


#: Truncated between tests, children first. audit_logs is included deliberately:
#: TRUNCATE does not fire row-level triggers, so the append-only guard blocks a
#: stray DELETE in application code while still allowing test isolation. A
#: row-by-row cleanup cannot clear this table at all — which is the point.
_TABLES = (
    "demo_settings",
    "audit_logs",
    "inbound_messages",
    "email_events",
    "job_runs",
    "reconciliation_events",
    "external_payments",
    "promises",
    "reminders",
    "payment_links",
    "invoices",
    "customers",
    "merchants",
    "operator_accounts",
)


def _truncate_all() -> None:
    with Session(engine) as s:
        s.exec(text(f"TRUNCATE {', '.join(_TABLES)} RESTART IDENTITY CASCADE"))
        s.commit()


@pytest.fixture
def session(migrated_database):
    """A real session, truncated after each test.

    Not a savepoint-wrapped session: several tests deliberately provoke an
    IntegrityError, which aborts the surrounding transaction and would take a
    savepoint-based rollback strategy down with it.
    """
    _truncate_all()
    with Session(engine) as s:
        yield s
        s.rollback()
    _truncate_all()


@pytest.fixture(autouse=True)
def operator_account(session) -> OperatorAccount:
    account = OperatorAccount(
        username=TEST_OPERATOR_USERNAME,
        display_name="Test Operator",
        role="admin",
        password_hash=hash_password(TEST_OPERATOR_PASSWORD),
    )
    session.add(account)
    session.commit()
    session.refresh(account)
    return account


@pytest.fixture
def merchant(session) -> Merchant:
    m = Merchant(name="Demo Traders", contact_email="ops@example.com")
    session.add(m)
    session.commit()
    session.refresh(m)
    return m


@pytest.fixture
def customer(session, merchant) -> Customer:
    c = Customer(
        merchant_id=merchant.id,
        name="ABC Traders",
        email="abc@example.com",
        phone="+919876543210",
        total_invoices=10,
        invoices_paid_late=3,
    )
    session.add(c)
    session.commit()
    session.refresh(c)
    return c


@pytest.fixture
def invoice(session, merchant, customer) -> Invoice:
    inv = Invoice(
        merchant_id=merchant.id,
        customer_id=customer.id,
        invoice_number=f"INV-{uuid.uuid4().hex[:8]}",
        amount_paise=4_200_000,  # ₹42,000
        issued_at=datetime(2026, 7, 1, tzinfo=UTC),
        due_at=datetime(2026, 8, 1, tzinfo=UTC),
    )
    session.add(inv)
    session.commit()
    session.refresh(inv)
    return inv
