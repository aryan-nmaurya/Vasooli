"""Download endpoints for the ledger and the summary. CSV, Excel, PDF.

Separate from the JSON dashboard routes because the response is a file, not data: the
browser needs a Content-Disposition to save it under a sensible name, and the bytes
must not pass through anything that assumes JSON.
"""

from fastapi import APIRouter, HTTPException, Query, Response
from starlette.status import HTTP_404_NOT_FOUND, HTTP_422_UNPROCESSABLE_ENTITY

from app.api.deps import OperatorRequired
from app.core.db import SessionDep
from app.services.exports import (
    overview_summary,
    queue_invoices,
    recovered_invoices,
    render,
)

router = APIRouter(prefix="/api/export", tags=["export"], dependencies=[OperatorRequired])

#: Everything downloadable, by name. An allowlist rather than a lookup into arbitrary
#: query state — an export endpoint that accepts a table name is a data-exfiltration
#: primitive wearing a spreadsheet costume.
DATASETS = {
    "recovered": recovered_invoices,
    "overview": overview_summary,
    "invoices": queue_invoices,
}


@router.get("/{dataset}")
def download(
    dataset: str,
    session: SessionDep,
    fmt: str = Query(default="csv", pattern="^(csv|xlsx|pdf)$", alias="format"),
    # Mirrors the dashboard's own filters so a download matches what is on screen.
    # Constrained rather than free text: these reach a WHERE clause, and an export
    # endpoint is the last place to accept an unbounded string.
    status: str | None = Query(default=None, pattern="^[a-z_]{1,32}$"),
    reason: str | None = Query(default=None, pattern="^[a-z_]{1,32}$"),
) -> Response:
    """One dataset, one format, as a downloadable file."""
    builder = DATASETS.get(dataset)
    if builder is None:
        raise HTTPException(
            HTTP_404_NOT_FOUND,
            f"Unknown export {dataset!r}. Available: {', '.join(sorted(DATASETS))}",
        )

    sheet = builder(session, status=status, reason=reason)
    try:
        payload, content_type, filename = render(sheet, fmt)
    except ValueError as exc:
        raise HTTPException(HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc

    return Response(
        content=payload,
        media_type=content_type,
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            # The row set changes with every cycle; a cached spreadsheet is a wrong one.
            "Cache-Control": "no-store",
        },
    )
