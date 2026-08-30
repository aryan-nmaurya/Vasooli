"""ERP synchronisation: idempotency, replay, partial failure, tombstones.

The plan's Phase 4 exit criteria are "replay and partial failure tests; no duplicate
invoices/customers". A ledger feed that double-imports on a retry is worse than one
that fails: the merchant chases the same debt twice and the customer receives two
demands for one invoice.

These drive `sync_connection` through the fixture adapter rather than a live provider,
which is the point — the orchestration is what has to be correct regardless of which
ERP is on the other end.
"""

from datetime import UTC, datetime, timedelta

import pytest
from sqlmodel import select

from app.models import Customer, ErpConnection, ErpRecord, ErpSyncRun, IntegrationFailure, Invoice
from app.services.erp import sync_connection


def _row(source_id: str, number: str, *, email: str = "ap@buyer.example.com", **over):
    """One canonical row in the shape the custom adapter accepts."""
    issued = datetime(2026, 1, 1, tzinfo=UTC)
    base = {
        "source_id": source_id,
        "source_tenant": "default",
        "invoice_number": number,
        "customer_name": "Buyer Ltd",
        "customer_email": email,
        "amount_paise": 5000_00,
        "issued_at": issued,
        "due_at": issued + timedelta(days=30),
    }
    base.update(over)
    return base


@pytest.fixture
def connection(session, merchant) -> ErpConnection:
    row = ErpConnection(merchant_id=merchant.id, provider="custom", status="connected")
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


def _invoices(session, merchant):
    return session.exec(select(Invoice).where(Invoice.merchant_id == merchant.id)).all()


# ===========================================================================
# Idempotency. The exit criterion: no duplicate invoices or customers.
# ===========================================================================


def test_syncing_the_same_page_twice_imports_one_invoice(session, merchant, connection):
    """A retry after a timeout the provider actually processed must not double-import."""
    rows = [_row("erp-1", "INV-8001")]

    sync_connection(session, connection, fixture_rows=rows)
    # Rewind the cursor: exactly what a retry of a page whose response was lost does.
    connection.cursor = None
    session.add(connection)
    session.commit()
    sync_connection(session, connection, fixture_rows=rows)

    assert [i.invoice_number for i in _invoices(session, merchant)] == ["INV-8001"]


def test_replaying_a_page_does_not_duplicate_the_customer(session, merchant, connection):
    rows = [_row("erp-1", "INV-8001"), _row("erp-2", "INV-8002")]

    sync_connection(session, connection, fixture_rows=rows)
    connection.cursor = None
    session.add(connection)
    session.commit()
    sync_connection(session, connection, fixture_rows=rows)

    customers = session.exec(select(Customer).where(Customer.merchant_id == merchant.id)).all()
    assert [c.email for c in customers] == ["ap@buyer.example.com"]


def test_a_replayed_record_updates_in_place_rather_than_inserting(session, merchant, connection):
    """`erp_records` is keyed by (merchant, provider, tenant, type, source id).

    Replay must land on the same row, or the dedup key is decorative.
    """
    sync_connection(session, connection, fixture_rows=[_row("erp-1", "INV-8001")])
    connection.cursor = None
    session.add(connection)
    session.commit()
    sync_connection(
        session, connection, fixture_rows=[_row("erp-1", "INV-8001", source_version="v2")]
    )

    records = session.exec(select(ErpRecord).where(ErpRecord.merchant_id == merchant.id)).all()
    assert len(records) == 1
    assert records[0].source_version == "v2"


# ===========================================================================
# Cursors. Progress has to survive being resumed.
# ===========================================================================


def test_the_cursor_advances_and_resumes_where_it_stopped(session, merchant, connection):
    rows = [_row(f"erp-{n}", f"INV-90{n:02d}") for n in range(5)]

    first = sync_connection(session, connection, fixture_rows=rows, limit=2)
    assert first.status == "completed", first.error
    assert connection.cursor == "2"
    assert len(_invoices(session, merchant)) == 2

    sync_connection(session, connection, fixture_rows=rows, limit=2)
    assert connection.cursor == "4"
    assert len(_invoices(session, merchant)) == 4


def test_the_cursor_clears_when_the_feed_is_exhausted(session, merchant, connection):
    sync_connection(session, connection, fixture_rows=[_row("erp-1", "INV-8001")], limit=50)
    assert connection.cursor is None


# ===========================================================================
# Partial failure. A bad page must not advance the cursor past unread work.
# ===========================================================================


def test_a_failing_provider_records_a_retryable_failure(session, merchant, connection):
    """Zoho and Tally raise until a credentialled worker exists. That path must leave
    a retryable trail rather than a silent no-op."""
    connection.provider = "zoho"
    session.add(connection)
    session.commit()

    run = sync_connection(session, connection, fixture_rows=[])

    assert run.status == "failed"
    assert "zoho" in (run.error or "")
    assert connection.status == "error"

    failures = session.exec(
        select(IntegrationFailure).where(IntegrationFailure.merchant_id == merchant.id)
    ).all()
    assert len(failures) == 1
    assert failures[0].next_retry_at is not None


def test_a_failed_sync_does_not_advance_the_cursor(session, merchant, connection):
    """Otherwise the unread page is skipped forever and those invoices are never chased."""
    sync_connection(session, connection, fixture_rows=[_row("erp-1", "INV-8001")], limit=1)
    cursor_before = connection.cursor

    connection.provider = "tally"
    session.add(connection)
    session.commit()
    sync_connection(session, connection, fixture_rows=[])

    assert connection.cursor == cursor_before


def test_a_failed_sync_imports_nothing(session, merchant, connection):
    connection.provider = "zoho"
    session.add(connection)
    session.commit()

    sync_connection(session, connection, fixture_rows=[])

    assert _invoices(session, merchant) == []


# ===========================================================================
# Tombstones. An invoice cancelled upstream must not be chased.
# ===========================================================================


def test_a_tombstoned_record_is_not_imported_as_an_invoice(session, merchant, connection):
    rows = [_row("erp-1", "INV-8001", tombstoned=True)]

    run = sync_connection(session, connection, fixture_rows=rows)

    assert _invoices(session, merchant) == []
    # Still tracked: the merchant needs to see that the row was seen and skipped.
    assert run.imported_count == 1
    record = session.exec(select(ErpRecord).where(ErpRecord.merchant_id == merchant.id)).one()
    assert record.tombstoned is True


def test_a_live_record_alongside_a_tombstone_still_imports(session, merchant, connection):
    rows = [_row("erp-1", "INV-8001", tombstoned=True), _row("erp-2", "INV-8002")]

    sync_connection(session, connection, fixture_rows=rows)

    assert [i.invoice_number for i in _invoices(session, merchant)] == ["INV-8002"]


# ===========================================================================
# Tenancy. Two merchants may hold the same invoice number and the same source id.
# ===========================================================================


def test_two_merchants_can_hold_the_same_invoice_number(session, merchant, connection):
    """The identity fix under sync: `(merchant_id, invoice_number)`, not the number
    alone. Two suppliers both numbering an invoice INV-0001 is the ordinary case."""
    from app.models import Merchant

    other = Merchant(name="Second Traders", contact_email="ops@second.example.test")
    session.add(other)
    session.commit()
    session.refresh(other)
    other_conn = ErpConnection(merchant_id=other.id, provider="custom", status="connected")
    session.add(other_conn)
    session.commit()
    session.refresh(other_conn)

    rows = [_row("erp-1", "INV-0001")]
    sync_connection(session, connection, fixture_rows=rows)
    sync_connection(session, other_conn, fixture_rows=rows)

    assert len(_invoices(session, merchant)) == 1
    assert len(_invoices(session, other)) == 1


def test_each_sync_run_is_recorded_for_its_own_merchant(session, merchant, connection):
    sync_connection(session, connection, fixture_rows=[_row("erp-1", "INV-8001")])

    runs = session.exec(select(ErpSyncRun).where(ErpSyncRun.merchant_id == merchant.id)).all()
    assert len(runs) == 1
    assert runs[0].status == "completed", runs[0].error
    assert runs[0].finished_at is not None
