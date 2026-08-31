"""Batch ingestion of a merchant's receivables ledger. Doc §3 Stage 1.

Idempotent on `invoice_number`: the seed script, the demo reset, and a retried API
call all run the same path, and re-running any of them must not duplicate a ledger.
"""

import uuid

from sqlmodel import Session, select

from app.core.clock import due_date_for_days_overdue, ist_midnight, today_ist
from app.core.constants import InvoiceStatus
from app.core.logging import get_logger
from app.models import AuditAction, AuditActor, AuditLog, Customer, Invoice, Merchant
from app.schemas.invoice import (
    BatchIngestResponse,
    IngestError,
    InvoiceIngestRow,
)

log = get_logger("ingestion")

DEFAULT_MERCHANT_NAME = "Demo Traders"


def get_or_create_merchant(
    session: Session, merchant_id: uuid.UUID | None = None, *, name: str = DEFAULT_MERCHANT_NAME
) -> Merchant:
    """Resolve the owning merchant.

    Vasooli runs single-merchant, but every row carries `merchant_id` so the schema
    does not need a migration if that ever changes.
    """
    if merchant_id is not None:
        merchant = session.get(Merchant, merchant_id)
        if merchant is None:
            raise ValueError(f"merchant {merchant_id} does not exist")
        return merchant

    # The legacy operator/import path is demo-only. Never let it silently select a
    # live tenant merely because that row was created first.
    existing = session.exec(
        select(Merchant)
        .where(Merchant.is_demo.is_(True))  # type: ignore[union-attr]
        .order_by(Merchant.created_at)
    ).first()
    if existing is not None:
        return existing

    merchant = Merchant(name=name, contact_email="ops@example.com")
    session.add(merchant)
    session.flush()
    return merchant


def _upsert_customer(
    session: Session, merchant: Merchant, row: InvoiceIngestRow
) -> tuple[Customer, bool]:
    """Find a customer by email within the merchant, or create one.

    History counters are refreshed from the incoming row on every ingest — the ledger
    export is the system of record for a customer's past behaviour, and a stale local
    copy would quietly skew diagnosis.
    """
    customer = session.exec(
        select(Customer).where(
            Customer.merchant_id == merchant.id,
            Customer.email == row.customer_email,
        )
    ).first()

    created = customer is None
    if customer is None:
        customer = Customer(
            merchant_id=merchant.id,
            name=row.customer_name,
            email=row.customer_email,
        )

    customer.name = row.customer_name
    customer.phone = row.customer_phone or customer.phone
    customer.total_invoices = row.customer_total_invoices
    customer.invoices_paid_late = row.customer_invoices_paid_late
    customer.invoices_defaulted = row.customer_invoices_defaulted
    customer.broken_promises = row.customer_broken_promises
    customer.avg_invoice_paise = row.avg_invoice_paise

    session.add(customer)
    session.flush()
    return customer, created


def _initial_status(invoice: Invoice) -> InvoiceStatus:
    """An invoice enters the queue already overdue, or waits until it is.

    Diagnosis and tier selection are the scheduler's job; ingestion only
    decides whether the invoice is in scope at all.
    """
    return InvoiceStatus.CHASING if invoice.days_overdue > 0 else InvoiceStatus.PENDING


def ingest_batch(
    session: Session,
    rows: list[InvoiceIngestRow],
    *,
    merchant_id: uuid.UUID | None = None,
    rebase_dates: bool = False,
) -> BatchIngestResponse:
    """Ingest a ledger. Existing invoice numbers are skipped, not updated.

    Skipping rather than updating is deliberate: an invoice already in the recovery
    queue has reminder counters, promises, and an audit trail attached, and a re-run
    of the seed file must not silently reset any of that.

    `rebase_dates` recomputes `due_at` so each invoice keeps the number of days
    overdue it was generated with. Without it, a CSV written on Monday produces a
    demo with every tier boundary shifted by Friday.
    """
    merchant = get_or_create_merchant(session, merchant_id)

    ingested = 0
    skipped = 0
    failed = 0
    customers_created = 0
    errors: list[IngestError] = []

    existing_numbers = set(
        session.exec(select(Invoice.invoice_number).where(Invoice.merchant_id == merchant.id)).all()
    )
    #: Numbers seen earlier in THIS batch. A file containing the same invoice twice
    #: would otherwise pass the pre-check and fail on the unique index at commit,
    #: taking the whole batch down with it.
    seen_in_batch: set[str] = set()

    for row in rows:
        if row.invoice_number in existing_numbers or row.invoice_number in seen_in_batch:
            skipped += 1
            continue

        try:
            # A SAVEPOINT per row, not a bare try/except. `session.rollback()` unwinds
            # the whole uncommitted transaction, so a single bad row would discard
            # every invoice ingested before it — which is precisely the outcome a
            # per-row error report is supposed to prevent.
            with session.begin_nested():
                customer, created = _upsert_customer(session, merchant, row)

                # Prefer the generator's stated offset over one recomputed from the
                # CSV's absolute dates. Recomputing ages with the file: a ledger
                # written yesterday would land every invoice a day past the tier
                # boundary it was seeded to sit on, and the "just below threshold"
                # cases the demo needs would quietly disappear.
                #
                # today_ist(), not utcnow().date(), for the fallback: between 00:00
                # and 05:30 IST the UTC date is still yesterday.
                original_days_overdue = (
                    row.gen_days_overdue
                    if row.gen_days_overdue is not None
                    else (today_ist() - row.due_at).days
                )
                if rebase_dates and original_days_overdue > 0:
                    due_at = due_date_for_days_overdue(original_days_overdue)
                    issued_at = due_date_for_days_overdue(
                        original_days_overdue + (row.due_at - row.issued_at).days
                    )
                else:
                    due_at = ist_midnight(row.due_at)
                    issued_at = ist_midnight(row.issued_at)

                invoice = Invoice(
                    merchant_id=merchant.id,
                    customer_id=customer.id,
                    invoice_number=row.invoice_number,
                    amount_paise=row.amount_paise,
                    issued_at=issued_at,
                    due_at=due_at,
                    terms_days=row.terms_days,
                    has_prior_dispute_note=row.has_prior_dispute_note,
                )
                invoice.status = _initial_status(invoice)
                session.add(invoice)
                session.flush()

                session.add(
                    AuditLog(
                        invoice_id=invoice.id,
                        actor=AuditActor.SYSTEM,
                        action=AuditAction.INVOICE_INGESTED,
                        detail={
                            "invoice_number": invoice.invoice_number,
                            "amount_paise": invoice.amount_paise,
                            "days_overdue": invoice.days_overdue,
                            "status": invoice.status,
                            "rebased": rebase_dates,
                        },
                    )
                )

            # Counted only after the savepoint commits — a row that failed halfway
            # rolled its customer back too, so it never really created one.
            if created:
                customers_created += 1
            seen_in_batch.add(row.invoice_number)
            ingested += 1

        except Exception as exc:  # noqa: BLE001 - one bad row must not fail the batch
            failed += 1
            errors.append(IngestError(invoice_number=row.invoice_number, error=str(exc)))
            log.warning("ingestion.row_failed", invoice_number=row.invoice_number, error=str(exc))

    session.commit()
    log.info(
        "ingestion.batch_complete",
        ingested=ingested,
        skipped=skipped,
        failed=failed,
        customers_created=customers_created,
    )
    return BatchIngestResponse(
        merchant_id=merchant.id,
        ingested=ingested,
        skipped_duplicates=skipped,
        failed=failed,
        customers_created=customers_created,
        errors=errors,
    )
