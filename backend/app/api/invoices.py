"""Invoice ingestion and lookup. Doc §3 Stage 1."""

import uuid

from fastapi import APIRouter, File, Form, HTTPException, Response, UploadFile, status
from sqlmodel import select

from app.api.deps import OperatorRequired
from app.core.db import SessionDep
from app.models import Customer, Invoice, PaymentLink
from app.schemas.invoice import BatchIngestRequest, BatchIngestResponse, InvoiceRead
from app.services.csv_import import LedgerFileError, parse_ledger, template_csv
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


#: Enough for a large receivables book, small enough that a mis-uploaded video is
#: refused before it is read into memory.
MAX_UPLOAD_BYTES = 5 * 1024 * 1024


@router.get("/import/template")
def import_template() -> Response:
    """A blank CSV with exactly the columns the importer accepts.

    Generated from the ingest schema itself, so it cannot drift from what the parser
    will take.
    """
    return Response(
        content=template_csv(),
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": 'attachment; filename="vasooli-import-template.csv"',
            "Cache-Control": "no-store",
        },
    )


@router.post("/import")
async def import_ledger(
    session: SessionDep,
    file: UploadFile = File(...),
    # Default true: a preview that could accidentally write is not a preview. Writing
    # has to be the deliberate second step.
    dry_run: bool = Form(default=True),
    rebase_dates: bool = Form(default=False),
) -> dict:
    """Import a ledger CSV. Previews by default; writes only when asked.

    The two-step shape is the point. Importing four hundred rows blind and finding out
    afterwards that row 47 was malformed is the version that wastes an afternoon, so
    the first call parses and reports and the second commits.

    Parsing is all this adds — the write itself is `ingest_batch`, unchanged, with its
    per-row SAVEPOINT isolation and duplicate skipping.
    """
    payload = await file.read()
    if len(payload) > MAX_UPLOAD_BYTES:
        raise HTTPException(
            status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            f"File is larger than {MAX_UPLOAD_BYTES // (1024 * 1024)} MB.",
        )

    try:
        parsed = parse_ledger(payload)
    except LedgerFileError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc

    preview = {
        "filename": file.filename,
        "parsed": len(parsed.rows),
        "problems": [
            {"line": p.line, "invoice_number": p.invoice_number, "message": p.message}
            for p in parsed.problems
        ],
        "unknown_columns": parsed.unknown_columns,
        # Named so the merchant recognises the rows, not just the count.
        "sample": [r.invoice_number for r in parsed.rows[:10]],
    }

    if dry_run:
        # Which of the parsed rows the ledger already has. Reported now rather than
        # discovered after the write, so "12 imported, 8 skipped" is never a surprise.
        existing = set(
            session.exec(
                select(Invoice.invoice_number).where(
                    Invoice.invoice_number.in_([r.invoice_number for r in parsed.rows])  # type: ignore[attr-defined]
                )
            ).all()
        )
        return {
            **preview,
            "dry_run": True,
            "duplicates": sorted(existing),
            "would_import": len([r for r in parsed.rows if r.invoice_number not in existing]),
        }

    if not parsed.rows:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY, "Nothing importable in that file."
        )

    result = ingest_batch(session, parsed.rows, rebase_dates=rebase_dates)
    return {**preview, "dry_run": False, "result": result.model_dump(mode="json")}
