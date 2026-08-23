"""Invoice ingestion and lookup. Doc §3 Stage 1."""

import uuid

from fastapi import APIRouter, HTTPException, status
from sqlmodel import select

from app.api.deps import OperatorRequired
from app.core.db import SessionDep
from app.models import Customer, Invoice, PaymentLink
from app.schemas.invoice import BatchIngestRequest, BatchIngestResponse, InvoiceRead
from app.services.ingestion import ingest_batch
from app.services.provisioning import ProvisioningError, provision_batch, provision_for_invoice

router = APIRouter(prefix="/api/invoices", tags=["invoices"], dependencies=[OperatorRequired])


@router.post("/batch", response_model=BatchIngestResponse, status_code=status.HTTP_202_ACCEPTED)
def ingest_invoices(payload: BatchIngestRequest, session: SessionDep) -> BatchIngestResponse:
    """Ingest a batch of overdue invoices.

    Idempotent on `invoice_number` — re-posting the same file reports the rows as
    duplicates and changes nothing.
    """
    try:
        return ingest_batch(
            session,
            payload.invoices,
            merchant_id=payload.merchant_id,
            rebase_dates=payload.rebase_dates,
        )
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc


@router.get("/{invoice_id}", response_model=InvoiceRead)
def get_invoice(invoice_id: uuid.UUID, session: SessionDep) -> InvoiceRead:
    invoice = session.get(Invoice, invoice_id)
    if invoice is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Invoice not found")
    customer = session.get(Customer, invoice.customer_id)
    link = session.exec(select(PaymentLink).where(PaymentLink.invoice_id == invoice.id)).first()
    return InvoiceRead.from_invoice(
        invoice, customer_name=customer.name if customer else None, payment_link=link
    )


@router.get("", response_model=list[InvoiceRead])
def list_invoices(session: SessionDep, limit: int = 50, offset: int = 0) -> list[InvoiceRead]:
    """Minimal listing so Phase 2 is verifiable. Filtering lands in Phase 9."""
    invoices = session.exec(
        select(Invoice).order_by(Invoice.due_at).offset(offset).limit(min(limit, 200))
    ).all()
    names = {c.id: c.name for c in session.exec(select(Customer)).all()}
    links = {pl.invoice_id: pl for pl in session.exec(select(PaymentLink)).all()}
    return [
        InvoiceRead.from_invoice(
            i, customer_name=names.get(i.customer_id), payment_link=links.get(i.id)
        )
        for i in invoices
    ]


@router.post("/{invoice_id}/provision")
def provision_invoice(invoice_id: uuid.UUID, session: SessionDep) -> dict[str, str]:
    """Create this invoice's payment link, or return the existing one.

    Idempotent, so the dashboard's retry button is safe to press twice.
    """
    try:
        link = provision_for_invoice(session, invoice_id)
    except ProvisioningError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc
    return {
        "invoice_id": str(invoice_id),
        "payment_link_id": link.razorpay_payment_link_id,
        "short_url": link.short_url,
        "status": link.status,
    }


@router.post("/provision-batch")
def provision_all(session: SessionDep, limit: int | None = None) -> dict:
    """Provision every invoice that has no payment link yet."""
    return provision_batch(session, limit=limit)
