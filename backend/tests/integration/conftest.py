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
from app.core.db import engine
from app.models import Customer, Invoice, Merchant


@pytest.fixture(scope="session", autouse=True)
def migrated_database():
    """Bring the schema to head once per session."""
    cfg = Config("alembic.ini")
    command.upgrade(cfg, "head")
    yield


#: Truncated between tests, children first. audit_logs is included deliberately:
#: TRUNCATE does not fire row-level triggers, so the append-only guard blocks a
#: stray DELETE in application code while still allowing test isolation. A
#: row-by-row cleanup cannot clear this table at all — which is the point.
_TABLES = (
    "audit_logs",
    "reconciliation_events",
    "promises",
    "reminders",
    "virtual_accounts",
    "invoices",
    "customers",
    "merchants",
)


@pytest.fixture
def session(migrated_database):
    """A real session, truncated after each test.

    Not a savepoint-wrapped session: several tests deliberately provoke an
    IntegrityError, which aborts the surrounding transaction and would take a
    savepoint-based rollback strategy down with it.
    """
    with Session(engine) as s:
        yield s
        s.rollback()
    with Session(engine) as s:
        s.exec(text(f"TRUNCATE {', '.join(_TABLES)} RESTART IDENTITY CASCADE"))
        s.commit()


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
