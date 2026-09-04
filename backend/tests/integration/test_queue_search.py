"""Searching the live recovery queue.

The overview holds a few hundred rows. The invoice someone is hunting for is usually
one it did not load, so the search runs against the whole ledger in the database
rather than filtering whatever the browser happens to be holding.
"""

import uuid

import pytest

from app.core.clock import utcnow
from app.models import Customer, Invoice, Merchant


@pytest.fixture
def workspace(session):
    merchant = Merchant(
        name="Searchable Ltd",
        contact_email="ops@searchable.example",
        is_demo=False,
        mode="live",
        onboarding_state={},
    )
    session.add(merchant)
    session.commit()
    session.refresh(merchant)

    for name, number in [("Nova Retail", "INV-9001"), ("Deccan Logistics", "INV-9002")]:
        customer = Customer(
            merchant_id=merchant.id, name=name, email=f"ap-{uuid.uuid4().hex[:6]}@buyer.example"
        )
        session.add(customer)
        session.flush()
        session.add(
            Invoice(
                merchant_id=merchant.id,
                customer_id=customer.id,
                invoice_number=number,
                amount_paise=100000,
                issued_at=utcnow(),
                due_at=utcnow(),
            )
        )
    session.commit()
    return merchant


def _numbers(session, merchant, q=None):
    """Call the endpoint function directly.

    Every parameter is passed explicitly: called as a plain function, FastAPI's
    `Query(None, ...)` defaults arrive as Query objects rather than None, and a
    truthy one turns into a WHERE clause against an unadaptable value.
    """
    from types import SimpleNamespace

    from app.api.live_dashboard import queue

    context = SimpleNamespace(merchant=merchant)
    rows = queue(
        session=session,
        context=context,
        status_filter=None,
        reason=None,
        q=q,
        limit=100,
        offset=0,
    )
    return [row.invoice_number for row in rows]


def test_an_invoice_number_finds_exactly_that_invoice(session, workspace):
    assert _numbers(session, workspace, q="INV-9001") == ["INV-9001"]


def test_a_customer_name_finds_their_invoices(session, workspace):
    assert _numbers(session, workspace, q="deccan") == ["INV-9002"]


def test_search_is_case_insensitive_and_partial(session, workspace):
    assert _numbers(session, workspace, q="nOvA") == ["INV-9001"]
    assert _numbers(session, workspace, q="9002") == ["INV-9002"]


def test_no_search_returns_the_whole_queue(session, workspace):
    assert sorted(_numbers(session, workspace)) == ["INV-9001", "INV-9002"]


def test_a_term_matching_nothing_returns_nothing(session, workspace):
    assert _numbers(session, workspace, q="zzz-no-such-thing") == []


def test_search_cannot_reach_another_merchants_invoices(session, workspace):
    """The customer-name match is a subquery, and it is scoped to the caller.

    Two tenants can easily have a customer of the same name; an unscoped subquery
    would let one merchant's search surface the other's invoice numbers.
    """
    other = Merchant(
        name="Other Ltd",
        contact_email="ops@other.example",
        is_demo=False,
        mode="live",
        onboarding_state={},
    )
    session.add(other)
    session.commit()
    session.refresh(other)
    twin = Customer(merchant_id=other.id, name="Nova Retail", email="ap@other.example")
    session.add(twin)
    session.flush()
    session.add(
        Invoice(
            merchant_id=other.id,
            customer_id=twin.id,
            invoice_number="INV-OTHER",
            amount_paise=100000,
            issued_at=utcnow(),
            due_at=utcnow(),
        )
    )
    session.commit()

    assert _numbers(session, workspace, q="Nova Retail") == ["INV-9001"]
