"""Merchant-owned Razorpay collection account connections."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlmodel import select

from app.core.clock import utcnow
from app.core.db import SessionDep
from app.models import PaymentConnection
from app.services.authorization import LiveContext, require_live_permission
from app.services.payment_connections import save_connection

router = APIRouter(prefix="/api/live/payment-connections", tags=["live-payment-connections"])


class ConnectionRequest(BaseModel):
    mode: str = Field(pattern=r"^(oauth|byok)$")
    provider_account_id: str = Field(min_length=2, max_length=120)
    access_token: str | None = Field(default=None, max_length=2000)
    refresh_token: str | None = Field(default=None, max_length=2000)
    api_key_id: str | None = Field(default=None, max_length=160)
    api_key_secret: str | None = Field(default=None, max_length=500)
    scopes: list[str] = Field(default_factory=list, max_length=20)


@router.get("")
def get_connection(
    session: SessionDep,
    context: Annotated[LiveContext, Depends(require_live_permission("erp.read"))],
) -> dict | None:
    row = session.exec(
        select(PaymentConnection).where(PaymentConnection.merchant_id == context.merchant.id)
    ).first()
    if row is None:
        return None
    return {
        "id": str(row.id),
        "provider": row.provider,
        "mode": row.mode,
        "provider_account_id": row.provider_account_id,
        "scopes": row.scopes,
        "status": row.status,
        "last_verified_at": row.last_verified_at.isoformat() if row.last_verified_at else None,
        "revoked_at": row.revoked_at.isoformat() if row.revoked_at else None,
        "credentials_present": bool(row.access_token_encrypted or row.api_key_secret_encrypted),
    }


@router.put("")
def connect(
    payload: ConnectionRequest,
    request: Request,
    session: SessionDep,
    context: Annotated[LiveContext, Depends(require_live_permission("erp.configure"))],
) -> dict:
    if payload.mode == "oauth" and not payload.access_token:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "OAuth access token is required")
    if payload.mode == "byok" and (not payload.api_key_id or not payload.api_key_secret):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY, "BYO API credentials are required"
        )
    row = save_connection(
        session,
        context.merchant.id,
        mode=payload.mode,
        provider_account_id=payload.provider_account_id,
        access_token=payload.access_token,
        refresh_token=payload.refresh_token,
        api_key_id=payload.api_key_id,
        api_key_secret=payload.api_key_secret,
        scopes=payload.scopes,
    )
    from app.services.auth import audit

    audit(
        session,
        action="payment_connection.updated",
        merchant_id=context.merchant.id,
        actor_user_id=context.user.id,
        object_type="razorpay_connection",
        object_id=row.id,
        ip_address=request.client.host if request.client else None,
        detail={"mode": row.mode, "provider_account_id": row.provider_account_id},
    )
    session.commit()
    return {"status": row.status, "mode": row.mode, "provider_account_id": row.provider_account_id}


@router.delete("")
def disconnect(
    request: Request,
    session: SessionDep,
    context: Annotated[LiveContext, Depends(require_live_permission("erp.configure"))],
) -> dict[str, str]:
    row = session.exec(
        select(PaymentConnection).where(PaymentConnection.merchant_id == context.merchant.id)
    ).first()
    if row is not None:
        row.status = "revoked"
        row.revoked_at = utcnow()
        session.add(row)
        from app.services.auth import audit

        audit(
            session,
            action="payment_connection.revoked",
            merchant_id=context.merchant.id,
            actor_user_id=context.user.id,
            object_type="razorpay_connection",
            object_id=row.id,
            ip_address=request.client.host if request.client else None,
        )
        session.commit()
    return {"status": "revoked"}
