"""Tenant-scoped live invoice reads, imports and payment-link provisioning."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlmodel import select

from app.core.db import SessionDep
from app.models import Customer, Invoice, PaymentConnection, PaymentLink
from app.schemas.invoice import BatchIngestRequest, BatchIngestResponse, InvoiceRead
from app.services.authorization import LiveContext, get_scoped_object, require_live_permission
from app.services.billing import BillingEntitlementError, assert_live_entitled
from app.services.ingestion import ingest_batch
from app.services.payment_connections import client_for_connection
from app.services.provisioning import ProvisioningError, provision_for_invoice

router = APIRouter(prefix="/api/live/invoices", tags=["live-invoices"])


@router.get("", response_model=list[InvoiceRead])
def list_live_invoices(
    session: SessionDep,
    context: Annotated[LiveContext, Depends(require_live_permission("invoice.read"))],
    limit: int = 50,
    offset: int = 0,
) -> list[InvoiceRead]:
    invoices = session.exec(
        select(Invoice)
        .where(Invoice.merchant_id == context.merchant.id)
        .order_by(Invoice.due_at)
        .offset(max(0, offset))
        .limit(min(max(1, limit), 200))
    ).all()
    customer_ids = [invoice.customer_id for invoice in invoices]
    names = {
        customer.id: customer.name
        for customer in session.exec(select(Customer).where(Customer.id.in_(customer_ids))).all()  # type: ignore[union-attr]
    }
    links = {
        link.invoice_id: link
        for link in session.exec(
            select(PaymentLink).where(PaymentLink.invoice_id.in_([i.id for i in invoices]))  # type: ignore[union-attr]
        ).all()
    }
    return [
        InvoiceRead.from_invoice(
            invoice,
            customer_name=names.get(invoice.customer_id),
            payment_link=links.get(invoice.id),
        )
        for invoice in invoices
    ]


@router.get("/{invoice_id}", response_model=InvoiceRead)
def get_live_invoice(
    invoice_id: uuid.UUID,
    session: SessionDep,
    context: Annotated[LiveContext, Depends(require_live_permission("invoice.read"))],
) -> InvoiceRead:
    invoice = get_scoped_object(session, Invoice, invoice_id, context.merchant.id)
    if invoice is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Invoice not found")
    customer = session.get(Customer, invoice.customer_id)
    link = session.exec(select(PaymentLink).where(PaymentLink.invoice_id == invoice.id)).first()
    return InvoiceRead.from_invoice(
        invoice,
        customer_name=customer.name if customer else None,
        payment_link=link,
    )


@router.post("/batch", response_model=BatchIngestResponse, status_code=status.HTTP_202_ACCEPTED)
def import_live_invoices(
    payload: BatchIngestRequest,
    session: SessionDep,
    context: Annotated[LiveContext, Depends(require_live_permission("invoice.import"))],
) -> BatchIngestResponse:
    existing_numbers = set(
        session.exec(
            select(Invoice.invoice_number).where(Invoice.merchant_id == context.merchant.id)
        ).all()
    )
    new_numbers = {
        row.invoice_number for row in payload.invoices if row.invoice_number not in existing_numbers
    }
    try:
        assert_live_entitled(session, context.merchant.id, additional_invoices=len(new_numbers))
    except BillingEntitlementError as exc:
        raise HTTPException(status.HTTP_402_PAYMENT_REQUIRED, str(exc)) from exc
    return ingest_batch(
        session,
        payload.invoices,
        merchant_id=context.merchant.id,
        rebase_dates=payload.rebase_dates,
    )


@router.post("/{invoice_id}/provision")
def provision_live_invoice(
    invoice_id: uuid.UUID,
    session: SessionDep,
    context: Annotated[LiveContext, Depends(require_live_permission("payment_link.create"))],
) -> dict[str, str]:
    invoice = get_scoped_object(session, Invoice, invoice_id, context.merchant.id)
    if invoice is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Invoice not found")
    try:
        assert_live_entitled(session, context.merchant.id)
    except BillingEntitlementError as exc:
        raise HTTPException(status.HTTP_402_PAYMENT_REQUIRED, str(exc)) from exc
    connection = session.exec(
        select(PaymentConnection).where(
            PaymentConnection.merchant_id == context.merchant.id,
            PaymentConnection.status == "connected",
            PaymentConnection.revoked_at.is_(None),  # type: ignore[union-attr]
        )
    ).first()
    if connection is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "Connect a Razorpay collection account first")
    try:
        client = client_for_connection(connection)
    except Exception as exc:
        raise HTTPException(status.HTTP_501_NOT_IMPLEMENTED, str(exc)) from exc
    try:
        link = provision_for_invoice(session, invoice.id, client=client)
    except ProvisioningError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc
    return {
        "invoice_id": str(invoice.id),
        "payment_link_id": link.razorpay_payment_link_id,
        "short_url": link.short_url,
        "status": link.status,
    }
