"""Merchant-scoped ERP connection and synchronization endpoints."""

import json
import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlmodel import select

from app.core.clock import utcnow
from app.core.db import SessionDep
from app.models import ErpConnection, ErpSyncRun
from app.services.auth import audit
from app.services.authorization import LiveContext, require_live_permission
from app.services.billing import BillingEntitlementError, assert_live_entitled
from app.services.erp import sync_connection
from app.services.payment_connections import encrypt_secret

router = APIRouter(prefix="/api/live/integrations", tags=["live-integrations"])


class ErpConnectionRequest(BaseModel):
    provider: str = Field(pattern=r"^(custom|zoho|tally)$")
    source_tenant: str | None = Field(default=None, max_length=160)
    credentials: dict[str, Any] = Field(default_factory=dict)


class SyncRequest(BaseModel):
    fixture_rows: list[dict[str, Any]] | None = None
    limit: int = Field(default=100, ge=1, le=500)


@router.get("")
def list_integrations(
    session: SessionDep,
    context: Annotated[LiveContext, Depends(require_live_permission("erp.read"))],
) -> list[dict[str, Any]]:
    rows = session.exec(
        select(ErpConnection).where(ErpConnection.merchant_id == context.merchant.id)
    ).all()
    return [
        {
            "id": str(row.id),
            "provider": row.provider,
            "source_tenant": row.source_tenant,
            "status": row.status,
            "cursor": row.cursor,
            "last_sync_at": row.last_sync_at.isoformat() if row.last_sync_at else None,
            "freshness_deadline": row.freshness_deadline.isoformat()
            if row.freshness_deadline
            else None,
        }
        for row in rows
    ]


@router.put("", status_code=status.HTTP_201_CREATED)
def connect_integration(
    payload: ErpConnectionRequest,
    request: Request,
    session: SessionDep,
    context: Annotated[LiveContext, Depends(require_live_permission("erp.configure"))],
) -> dict[str, Any]:
    row = session.exec(
        select(ErpConnection).where(
            ErpConnection.merchant_id == context.merchant.id,
            ErpConnection.provider == payload.provider,
        )
    ).first()
    if row is None:
        row = ErpConnection(merchant_id=context.merchant.id, provider=payload.provider)
    row.source_tenant = payload.source_tenant
    row.credentials_encrypted = encrypt_secret(json.dumps(payload.credentials, sort_keys=True))
    row.status = "connected"
    row.updated_at = utcnow()
    session.add(row)
    session.flush()
    audit(
        session,
        action="erp.connection_updated",
        merchant_id=context.merchant.id,
        actor_user_id=context.user.id,
        object_type="erp_connection",
        object_id=row.id,
        ip_address=request.client.host if request.client else None,
        detail={"provider": row.provider, "source_tenant": row.source_tenant},
    )
    session.commit()
    return {"id": str(row.id), "provider": row.provider, "status": row.status}


@router.post("/{connection_id}/sync")
def sync_integration(
    connection_id: uuid.UUID,
    payload: SyncRequest,
    session: SessionDep,
    context: Annotated[LiveContext, Depends(require_live_permission("erp.sync"))],
) -> dict[str, Any]:
    row = session.exec(
        select(ErpConnection).where(
            ErpConnection.id == connection_id,
            ErpConnection.merchant_id == context.merchant.id,
        )
    ).first()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Integration not found")
    try:
        assert_live_entitled(session, context.merchant.id)
    except BillingEntitlementError as exc:
        raise HTTPException(status.HTTP_402_PAYMENT_REQUIRED, str(exc)) from exc
    run = sync_connection(session, row, fixture_rows=payload.fixture_rows, limit=payload.limit)
    return {
        "id": str(run.id),
        "status": run.status,
        "imported": run.imported_count,
        "failed": run.failed_count,
        "error": run.error,
    }


@router.get("/{connection_id}/runs")
def sync_runs(
    connection_id: uuid.UUID,
    session: SessionDep,
    context: Annotated[LiveContext, Depends(require_live_permission("erp.read"))],
) -> list[dict[str, Any]]:
    rows = session.exec(
        select(ErpSyncRun)
        .where(
            ErpSyncRun.connection_id == connection_id,
            ErpSyncRun.merchant_id == context.merchant.id,
        )
        .order_by(ErpSyncRun.started_at.desc())
    ).all()
    return [
        {
            "id": str(row.id),
            "status": row.status,
            "imported": row.imported_count,
            "failed": row.failed_count,
            "error": row.error,
        }
        for row in rows
    ]
