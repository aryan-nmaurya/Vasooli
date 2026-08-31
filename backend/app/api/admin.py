"""Operational endpoints.

The manual trigger calls exactly the same function the scheduler does. A demo button
wired to a separate code path would demonstrate the button, not the system.
"""

import uuid

from fastapi import APIRouter, Query

from app.api.deps import OperatorRequired
from app.core.db import SessionDep
from app.services.recovery import run_recovery_cycle

router = APIRouter(prefix="/api/admin", tags=["admin"], dependencies=[OperatorRequired])


@router.post("/run-cycle")
def run_cycle(
    session: SessionDep,
    dry_run: bool = Query(False, description="Evaluate everything, send nothing."),
    invoice_id: uuid.UUID | None = Query(None, description="Restrict to one invoice."),
    use_llm: bool = Query(True, description="Set false to force template drafting."),
    limit: int | None = Query(None, ge=1, le=500),
) -> dict:
    """Run the recovery cycle now.

    Scoping to a single invoice makes a demo deterministic and fast, without needing a
    different code path to do it.
    """
    report = run_recovery_cycle(
        session,
        dry_run=dry_run,
        invoice_ids=[invoice_id] if invoice_id else None,
        use_llm=use_llm,
        limit=limit,
    )
    return {"dry_run": dry_run, **report.as_dict()}
