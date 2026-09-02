"""Cursor-based, idempotent ERP synchronization orchestration."""

import json
from datetime import timedelta
from decimal import Decimal

from sqlmodel import Session, select

from app.core.clock import utcnow
from app.core.constants import InvoiceStatus
from app.core.logging import get_logger
from app.integrations.erp import (
    CanonicalInvoice,
    ErpAuthExpiredError,
    SyncPage,
    adapter_for,
    adapter_for_credentials,
    json_safe,
    payload_hash,
)
from app.integrations.outbound_url import assert_safe_outbound_url
from app.models import (
    AuditAction,
    AuditActor,
    AuditLog,
    Customer,
    ErpConnection,
    ErpRecord,
    ErpSyncRun,
    IntegrationFailure,
    Invoice,
)
from app.schemas.invoice import InvoiceIngestRow
from app.services.billing import assert_live_entitled
from app.services.ingestion import ingest_batch
from app.services.manual_payments import sync_erp_adjustment
from app.services.oauth import refresh_zoho_token

log = get_logger("erp")


def sync_connection(
    session: Session,
    connection: ErpConnection,
    *,
    fixture_rows: list[dict] | None = None,
    limit: int = 100,
) -> ErpSyncRun:
    """Run one bounded page and persist every result before advancing the cursor."""
    run = ErpSyncRun(
        merchant_id=connection.merchant_id,
        connection_id=connection.id,
        cursor_before=connection.cursor,
    )
    session.add(run)
    session.flush()
    try:
        assert_live_entitled(session, connection.merchant_id)
        bounded_limit = min(max(limit, 1), 500)
        if fixture_rows is not None:
            adapter = adapter_for(connection.provider, fixture_rows)
            page = adapter.fetch_invoices(cursor=connection.cursor, limit=bounded_limit)
        else:
            page = _fetch_with_refresh(session, connection, limit=bounded_limit)
        ledger_rows = [
            InvoiceIngestRow(
                invoice_number=record.invoice_number,
                customer_name=record.customer_name,
                customer_email=record.customer_email,
                amount_inr=Decimal(record.amount_paise) / 100,
                issued_at=record.issued_at.date(),
                due_at=record.due_at.date(),
            )
            for record in page.records
            if not record.tombstoned
        ]
        if ledger_rows:
            ingest_batch(session, ledger_rows, merchant_id=connection.merchant_id)
        for record in page.records:
            _apply_invoice_record(session, connection, record)
            _upsert_record(session, connection, run, record)
            run.imported_count += 1
        connection.cursor = page.next_cursor
        connection.last_sync_at = utcnow()
        connection.last_success_at = utcnow()
        connection.freshness_deadline = utcnow() + timedelta(hours=24)
        connection.status = "healthy"
        run.cursor_after = connection.cursor
        run.status = "completed"
        run.finished_at = utcnow()
    except Exception as exc:
        run.status = "failed"
        # Some exceptions carry no message at all — cryptography's InvalidToken is
        # the one that bit here, raised when a connection has no stored credentials.
        # An empty `error` tells the merchant nothing and tells whoever debugs it
        # less, so fall back to the type name.
        run.error = (str(exc) or exc.__class__.__name__)[:1000]
        run.finished_at = utcnow()
        connection.status = "error"
        session.add(
            IntegrationFailure(
                merchant_id=connection.merchant_id,
                connection_id=connection.id,
                sync_run_id=run.id,
                category="sync",
                payload={},
                error=(str(exc) or exc.__class__.__name__)[:1000],
                next_retry_at=utcnow() + timedelta(minutes=15),
            )
        )
    session.add(connection)
    session.add(run)
    session.commit()
    session.refresh(run)
    return run


def _fetch_with_refresh(session: Session, connection: ErpConnection, *, limit: int) -> SyncPage:
    """Fetch one page, refreshing the OAuth token once if it has expired.

    Zoho access tokens last about an hour, so a scheduled sync running every half
    hour spends most of its life holding a token that is about to die. Without this
    the first expiry turned the connection to `error` and stayed there until somebody
    noticed and pressed Refresh by hand — automation that silently stops being
    automatic. The refresh token is long-lived and is what makes the recovery
    possible without the merchant re-authorising.

    Exactly one retry. If a freshly minted token is also rejected the problem is not
    expiry — the grant was revoked in Zoho, or the scopes changed — and retrying is
    just a slower way to fail.
    """
    from app.services.payment_connections import decrypt_secret, encrypt_secret

    credentials = json.loads(decrypt_secret(connection.credentials_encrypted or ""))
    adapter = adapter_for_credentials(
        connection.provider, credentials, source_tenant=connection.source_tenant
    )
    try:
        return adapter.fetch_invoices(cursor=connection.cursor, limit=limit)
    except ErpAuthExpiredError:
        refresh_token = credentials.get("refresh_token")
        if not refresh_token:
            # Nothing to refresh with. Surfaced as-is so the merchant is told to
            # reconnect rather than left watching a connection retry forever.
            raise
        log.info(
            "erp.token_refresh",
            provider=connection.provider,
            merchant_id=str(connection.merchant_id),
        )
        tokens = refresh_zoho_token(refresh_token)
        credentials["access_token"] = tokens.access_token
        # Zoho does not reissue the refresh token on every refresh; keep the existing
        # one when it does not, or the connection becomes unrefreshable next time.
        if tokens.refresh_token:
            credentials["refresh_token"] = tokens.refresh_token
        if tokens.api_domain:
            credentials["api_domain"] = tokens.api_domain
        connection.credentials_encrypted = encrypt_secret(json.dumps(credentials, sort_keys=True))
        session.add(connection)
        # Committed before the retry: a crash mid-retry must not lose the new token
        # and leave the stored one already invalidated at Zoho.
        session.commit()
        adapter = adapter_for_credentials(
            connection.provider, credentials, source_tenant=connection.source_tenant
        )
        return adapter.fetch_invoices(cursor=connection.cursor, limit=limit)


def _apply_invoice_record(
    session: Session, connection: ErpConnection, record: CanonicalInvoice
) -> None:
    """Apply source changes to the operational ledger, not only the ERP archive."""
    invoice = session.exec(
        select(Invoice).where(
            Invoice.merchant_id == connection.merchant_id,
            Invoice.invoice_number == record.invoice_number,
        )
    ).first()
    if invoice is None:
        return

    changed: dict[str, object] = {}
    for field, value in (
        ("amount_paise", record.amount_paise),
        ("issued_at", record.issued_at),
        ("due_at", record.due_at),
    ):
        previous = getattr(invoice, field)
        if previous != value:
            changed[field] = {"before": str(previous), "after": str(value)}
            setattr(invoice, field, value)

    customer = session.get(Customer, invoice.customer_id)
    if customer is not None:
        if record.customer_name and customer.name != record.customer_name:
            changed["customer_name"] = {
                "before": customer.name,
                "after": record.customer_name,
            }
            customer.name = record.customer_name
        if record.customer_email and customer.email != record.customer_email:
            changed["customer_email"] = {
                "before": customer.email,
                "after": record.customer_email,
            }
            customer.email = record.customer_email
        session.add(customer)

    if record.tombstoned and invoice.status not in {
        InvoiceStatus.RECOVERED,
        InvoiceStatus.WRITTEN_OFF,
    }:
        invoice.status = InvoiceStatus.WRITTEN_OFF
        session.add(
            AuditLog(
                invoice_id=invoice.id,
                actor=AuditActor.SYSTEM,
                action=AuditAction.ERP_INVOICE_CANCELLED,
                detail={
                    "provider": record.source_system,
                    "source_id": record.source_id,
                    "source_version": record.source_version,
                },
            )
        )
    elif changed:
        session.add(
            AuditLog(
                invoice_id=invoice.id,
                actor=AuditActor.SYSTEM,
                action=AuditAction.ERP_INVOICE_UPDATED,
                detail={
                    "provider": record.source_system,
                    "source_id": record.source_id,
                    "source_version": record.source_version,
                    "changes": changed,
                },
            )
        )
    session.add(invoice)
    session.flush()

    effective_date = (record.updated_at or record.issued_at).date()
    sync_erp_adjustment(
        session,
        invoice=invoice,
        provider=record.source_system,
        source_id=f"{record.source_id}:aggregate",
        amount_paise=record.paid_paise,
        received_on=effective_date,
    )
    sync_erp_adjustment(
        session,
        invoice=invoice,
        provider=record.source_system,
        source_id=f"{record.source_id}:aggregate",
        amount_paise=record.credited_paise,
        received_on=effective_date,
        is_credit=True,
    )


def _upsert_record(
    session: Session, connection: ErpConnection, run: ErpSyncRun, record: CanonicalInvoice
) -> ErpRecord:
    # Coerced before both the hash and the JSON column: a source row legitimately
    # carries dates, and neither will accept a datetime.
    raw = json_safe(record.raw_payload or {})
    row = session.exec(
        select(ErpRecord).where(
            ErpRecord.merchant_id == connection.merchant_id,
            ErpRecord.provider == record.source_system,
            ErpRecord.source_tenant == record.source_tenant,
            ErpRecord.record_type == "invoice",
            ErpRecord.source_record_id == record.source_id,
        )
    ).first()
    if row is None:
        row = ErpRecord(
            merchant_id=connection.merchant_id,
            connection_id=connection.id,
            provider=record.source_system,
            source_tenant=record.source_tenant,
            record_type="invoice",
            source_record_id=record.source_id,
            payload_hash=payload_hash(raw),
            raw_payload=raw,
        )
    else:
        row.connection_id = connection.id
        row.payload_hash = payload_hash(raw)
        row.raw_payload = raw
    row.source_version = record.source_version
    row.source_updated_at = record.updated_at
    row.tombstoned = record.tombstoned
    session.add(row)
    return row


def validate_connection_credentials(provider: str, credentials: dict) -> None:
    """Refuse a connection whose endpoint points back inside our own network.

    Lives here rather than in the API module because `app.api` reaches external
    systems through `app.services` — and this is a question about an external system.
    Applied when the connection is saved so the merchant gets an immediate, specific
    error; the adapters re-check before every fetch, because DNS can be repointed
    after a connection is stored.
    """
    for field in ("endpoint", "api_domain", "webhook_url"):
        value = credentials.get(field)
        if isinstance(value, str) and value.strip():
            assert_safe_outbound_url(value, what=f"{provider} {field}")
