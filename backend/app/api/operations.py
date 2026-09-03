"""Operational readiness and auditable support/data workflows."""

from datetime import timedelta
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request, status
from pydantic import BaseModel, Field
from sqlmodel import select

from app.core.clock import utcnow
from app.core.db import SessionDep, check_database
from app.core.middleware import client_ip
from app.models import DataRequest, JobRun
from app.services.auth import audit
from app.services.authorization import (
    LiveContext,
    merchant_scope,
    require_live_permission,
    require_live_reauth,
)

router = APIRouter(prefix="/api/live/operations", tags=["live-operations"])


class DataRequestPayload(BaseModel):
    reason: str | None = Field(default=None, max_length=500)


@router.get("/readiness")
def readiness(
    session: SessionDep,
    context: Annotated[LiveContext, Depends(require_live_permission("merchant.read"))],
) -> dict[str, Any]:
    db_ok, detail = check_database()
    now = utcnow()
    jobs: dict[str, Any] = {}
    for job_id in (
        "recovery_cycle",
        "payment_link_sync",
        "retry_operations",
        "service_heartbeat",
        "billing_reconciliation",
    ):
        row = session.exec(
            select(JobRun).where(JobRun.job_id == job_id).order_by(JobRun.started_at.desc())
        ).first()
        jobs[job_id] = {
            "last_started_at": row.started_at.isoformat() if row else None,
            "status": row.status if row else "never_run",
            "stale": row is None or now - row.started_at > timedelta(hours=36),
        }
    # "Ready" used to mean only "the database answered", so a workspace whose recovery
    # cycle had never run once — the single thing the product exists to do — reported
    # itself ready with `recovery_cycle: never_run, stale: true` sitting in the same
    # response. A readiness signal that stays green while the automation is dead is
    # worse than no signal, because someone acts on it.
    stale_jobs = sorted(job_id for job_id, state in jobs.items() if state["stale"])
    failed_jobs = sorted(job_id for job_id, state in jobs.items() if state["status"] == "failed")
    # A failed job or a cycle that has not run is degraded, not merely noteworthy:
    # in both cases nobody's invoices are being chased right now.
    if not db_ok or failed_jobs or "recovery_cycle" in stale_jobs:
        status_value = "degraded"
    elif stale_jobs:
        status_value = "attention"
    else:
        status_value = "ready"

    return {
        "status": status_value,
        "database": db_ok,
        "detail": detail,
        "jobs": jobs,
        # Named explicitly so the UI does not have to re-derive the reason from the
        # job map, and so the reason survives into logs and screenshots.
        "stale_jobs": stale_jobs,
        "failed_jobs": failed_jobs,
        "summary": (
            detail
            if not db_ok
            else "Automation is running."
            if status_value == "ready"
            else "The daily recovery cycle has not run recently — no invoice is being chased."
            if "recovery_cycle" in stale_jobs
            else f"Background job(s) need attention: {', '.join(failed_jobs or stale_jobs)}."
        ),
    }


@router.get("/data-requests")
def list_data_requests(
    session: SessionDep,
    context: Annotated[LiveContext, Depends(require_live_permission("merchant.read"))],
) -> list[dict[str, Any]]:
    rows = session.exec(
        select(DataRequest)
        .where(DataRequest.merchant_id == context.merchant.id)
        .order_by(DataRequest.created_at.desc())
    ).all()
    return [
        {
            "id": str(row.id),
            "type": row.request_type,
            "status": row.status,
            "reason": row.reason,
            "created_at": row.created_at.isoformat(),
        }
        for row in rows
    ]


@router.post("/data-requests/export", status_code=status.HTTP_202_ACCEPTED)
def request_export(
    payload: DataRequestPayload,
    request: Request,
    session: SessionDep,
    context: Annotated[LiveContext, Depends(require_live_reauth("audit.export"))],
) -> dict[str, str]:
    return _create_request(session, request, context, "export", payload.reason)


@router.post("/data-requests/deletion", status_code=status.HTTP_202_ACCEPTED)
def request_deletion(
    payload: DataRequestPayload,
    request: Request,
    session: SessionDep,
    context: Annotated[LiveContext, Depends(require_live_reauth("merchant.write"))],
) -> dict[str, str]:
    return _create_request(session, request, context, "deletion", payload.reason)


def _create_request(
    session, request, context, request_type: str, reason: str | None
) -> dict[str, str]:
    row = DataRequest(
        merchant_id=context.merchant.id,
        requested_by_user_id=context.user.id,
        request_type=request_type,
        reason=reason,
        detail={"requires_operator_review": request_type == "deletion"},
    )
    session.add(row)
    session.flush()
    audit(
        session,
        action=f"data_request.{request_type}",
        merchant_id=context.merchant.id,
        actor_user_id=context.user.id,
        object_type="data_request",
        object_id=row.id,
        ip_address=client_ip(request),
    )
    # Held across the commit: the transaction-local tenant dies at commit, and these
    # attribute reads would then re-SELECT with no tenant under the production role.
    with merchant_scope(session, context.merchant.id):
        session.commit()
        return {"id": str(row.id), "status": row.status, "type": row.request_type}
