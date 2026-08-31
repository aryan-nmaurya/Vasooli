"""Merchant-scoped ERP connection and synchronization endpoints."""

import hashlib
import hmac
import json
import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field
from sqlmodel import select

from app.core.clock import utcnow
from app.core.config import settings
from app.core.db import SessionDep
from app.core.middleware import client_ip
from app.models import ErpConnection, ErpSyncRun, ErpWebhookEvent
from app.services.auth import audit
from app.services.authorization import LiveContext, require_live_permission, require_live_reauth
from app.services.billing import BillingEntitlementError, assert_live_entitled
from app.services.erp import sync_connection, validate_connection_credentials
from app.services.oauth import (
    OAuthConfigurationError,
    OAuthExchangeError,
    consume_state,
    create_state,
    exchange_zoho_code,
    refresh_zoho_token,
    zoho_authorization_url,
)
from app.services.payment_connections import decrypt_secret, encrypt_secret

router = APIRouter(prefix="/api/live/integrations", tags=["live-integrations"])


class ErpConnectionRequest(BaseModel):
    provider: str = Field(pattern=r"^(custom|zoho|tally)$")
    source_tenant: str | None = Field(default=None, max_length=160)
    credentials: dict[str, Any] = Field(default_factory=dict)


class SyncRequest(BaseModel):
    fixture_rows: list[dict[str, Any]] | None = None
    limit: int = Field(default=100, ge=1, le=500)


@router.get("/zoho/oauth/start")
def zoho_oauth_start(
    request: Request,
    session: SessionDep,
    context: Annotated[LiveContext, Depends(require_live_permission("erp.configure"))],
) -> dict[str, str]:
    redirect_uri = settings.zoho_oauth_redirect_uri or str(request.url_for("zoho_oauth_callback"))
    try:
        state = create_state(
            session,
            merchant_id=context.merchant.id,
            user_id=context.user.id,
            provider="zoho",
            redirect_uri=redirect_uri,
        )
        url = zoho_authorization_url(state, redirect_uri)
    except OAuthConfigurationError as exc:
        session.rollback()
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from exc
    session.commit()
    return {"authorization_url": url, "provider": "zoho"}


@router.get("/zoho/oauth/callback", name="zoho_oauth_callback")
def zoho_oauth_callback(
    session: SessionDep,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
):
    if error:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Zoho authorization failed: {error}")
    if not code or not state:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "OAuth code and state are required")
    try:
        state_row = consume_state(session, provider="zoho", raw_state=state)
        from app.services.authorization import set_merchant_context

        set_merchant_context(session, state_row.merchant_id)
        tokens = exchange_zoho_code(code, state_row.redirect_uri)
        existing = session.exec(
            select(ErpConnection).where(
                ErpConnection.merchant_id == state_row.merchant_id,
                ErpConnection.provider == "zoho",
            )
        ).first()
        if existing is None:
            existing = ErpConnection(merchant_id=state_row.merchant_id, provider="zoho")
        existing.credentials_encrypted = encrypt_secret(
            json.dumps(
                {
                    "access_token": tokens.access_token,
                    "refresh_token": tokens.refresh_token,
                    "organization_id": tokens.account_id,
                    "api_domain": tokens.api_domain,
                    "scope": tokens.scopes,
                },
                sort_keys=True,
            )
        )
        existing.status = "connected"
        existing.updated_at = utcnow()
        session.add(existing)
        session.commit()
    except (OAuthConfigurationError, OAuthExchangeError, ValueError) as exc:
        session.rollback()
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    if settings.environment in {"local", "test"}:
        return {"status": "connected", "provider": "zoho"}
    return RedirectResponse(
        url=f"{settings.frontend_live_integrations_url}?connected=zoho",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.post("/{connection_id}/refresh")
def refresh_integration_token(
    connection_id: uuid.UUID,
    session: SessionDep,
    context: Annotated[LiveContext, Depends(require_live_reauth("erp.configure"))],
) -> dict[str, str | None]:
    row = session.exec(
        select(ErpConnection).where(
            ErpConnection.id == connection_id,
            ErpConnection.merchant_id == context.merchant.id,
        )
    ).first()
    if row is None or row.provider != "zoho" or not row.credentials_encrypted:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Integration not found")
    try:
        credentials = json.loads(decrypt_secret(row.credentials_encrypted))
        refresh_token = credentials.get("refresh_token")
        if not refresh_token:
            raise OAuthExchangeError("Zoho connection has no refresh token")
        tokens = refresh_zoho_token(refresh_token)
        credentials["access_token"] = tokens.access_token
        if tokens.api_domain:
            credentials["api_domain"] = tokens.api_domain
        row.credentials_encrypted = encrypt_secret(json.dumps(credentials, sort_keys=True))
        row.updated_at = utcnow()
        session.add(row)
        session.commit()
    except (OAuthConfigurationError, OAuthExchangeError, ValueError) as exc:
        session.rollback()
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc
    return {"status": row.status, "provider": row.provider}


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
    context: Annotated[LiveContext, Depends(require_live_reauth("erp.configure"))],
) -> dict[str, Any]:
    row = session.exec(
        select(ErpConnection).where(
            ErpConnection.merchant_id == context.merchant.id,
            ErpConnection.provider == payload.provider,
        )
    ).first()
    if row is None:
        row = ErpConnection(merchant_id=context.merchant.id, provider=payload.provider)
    # Validate before storing. A connection saved with an internal endpoint is an SSRF
    # the scheduler will re-trigger every half hour, entitlement checks included or not.
    try:
        validate_connection_credentials(payload.provider, payload.credentials)
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc

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
        ip_address=client_ip(request),
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


@router.post("/custom/{connection_id}/webhook", status_code=status.HTTP_202_ACCEPTED)
async def custom_webhook(
    connection_id: uuid.UUID,
    request: Request,
    session: SessionDep,
    x_merchant_id: Annotated[str | None, Header(alias="X-Merchant-ID")] = None,
    x_erp_signature: Annotated[str | None, Header(alias="X-ERP-Signature")] = None,
    x_erp_event_id: Annotated[str | None, Header(alias="X-ERP-Event-ID")] = None,
) -> dict[str, str]:
    """Accept a signed custom-ERP invoice feed with deterministic replay handling.

    The merchant header is part of the signed integration configuration and is needed
    to establish the Postgres RLS context before loading the connection. It is not an
    authorization shortcut: the HMAC secret remains the trust boundary.
    """
    if not x_merchant_id or not x_erp_signature:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, "Merchant and signature headers are required"
        )
    try:
        merchant_id = uuid.UUID(x_merchant_id)
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid merchant context") from exc
    from app.services.authorization import set_merchant_context

    set_merchant_context(session, merchant_id)
    connection = session.exec(
        select(ErpConnection).where(
            ErpConnection.id == connection_id,
            ErpConnection.merchant_id == merchant_id,
            ErpConnection.provider == "custom",
        )
    ).first()
    if connection is None or not connection.credentials_encrypted:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Integration not found")
    try:
        credentials = json.loads(decrypt_secret(connection.credentials_encrypted))
    except (ValueError, json.JSONDecodeError) as exc:
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR, "Invalid integration credentials"
        ) from exc
    secret = str(credentials.get("signing_secret") or "")
    raw = await request.body()
    expected = hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
    if not secret or not hmac.compare_digest(expected, x_erp_signature):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid ERP signature")
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid JSON payload") from exc
    event_id = x_erp_event_id or str(payload.get("event_id") or payload.get("id") or "")
    if not event_id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "ERP event id is required")
    existing = session.exec(
        select(ErpWebhookEvent).where(
            ErpWebhookEvent.connection_id == connection_id,
            ErpWebhookEvent.provider_event_id == event_id,
        )
    ).first()
    if existing is not None:
        return {"status": "duplicate"}
    event = ErpWebhookEvent(
        merchant_id=merchant_id,
        connection_id=connection_id,
        provider_event_id=event_id,
        payload_hash=hashlib.sha256(raw).hexdigest(),
        raw_payload=payload,
        signature_verified=True,
    )
    session.add(event)
    session.commit()
    rows = payload.get("invoices") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        rows = [payload] if isinstance(payload, dict) and payload.get("invoice_number") else []
    run = sync_connection(session, connection, fixture_rows=rows, limit=min(len(rows) or 1, 500))
    event.status = "processed" if run.status == "completed" else "failed"
    event.processing_error = run.error
    event.processed_at = utcnow()
    session.add(event)
    session.commit()
    return {"status": event.status, "run_id": str(run.id)}
