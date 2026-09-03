"""Merchant-scoped ERP connection and synchronization endpoints."""

import json
import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field
from sqlmodel import select

from app.core.clock import utcnow
from app.core.config import settings
from app.core.db import SessionDep
from app.core.middleware import client_ip
from app.models import ErpConnection, ErpSyncRun
from app.services.auth import audit
from app.services.authorization import (
    LiveContext,
    merchant_scope,
    require_live_permission,
    require_live_reauth,
)
from app.services.billing import (
    BillingEntitlementError,
    assert_feature_entitled,
    assert_live_entitled,
)
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
from app.services.plans import Feature

router = APIRouter(prefix="/api/live/integrations", tags=["live-integrations"])


class ErpConnectionRequest(BaseModel):
    provider: str = Field(pattern=r"^zoho$")
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
    # Tier gate, checked before the OAuth round trip rather than after: sending a
    # Starter merchant to Zoho only to refuse them on the way back wastes their time
    # and leaves an authorised app they did not end up using.
    try:
        assert_feature_entitled(session, context.merchant.id, Feature.ZOHO_INTEGRATION)
    except BillingEntitlementError as exc:
        raise HTTPException(status.HTTP_402_PAYMENT_REQUIRED, str(exc)) from exc

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
        # Copied out before the commit. The Zoho refresh token has already been
        # exchanged by this point, so a post-commit 500 leaves the merchant with a
        # spent token and an error.
        refreshed_status = row.status
        refreshed_provider = row.provider
        session.commit()
    except (OAuthConfigurationError, OAuthExchangeError, ValueError) as exc:
        session.rollback()
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc
    return {"status": refreshed_status, "provider": refreshed_provider}


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
    try:
        assert_feature_entitled(session, context.merchant.id, Feature.ZOHO_INTEGRATION)
    except BillingEntitlementError as exc:
        raise HTTPException(status.HTTP_402_PAYMENT_REQUIRED, str(exc)) from exc

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
    with merchant_scope(session, context.merchant.id):
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
    # `sync_connection` commits internally — once for the refreshed OAuth token and
    # again per batch — so the transaction-local tenant set by the dependency would be
    # gone partway through, and every read after it matches nothing under the
    # NOBYPASSRLS role. The run row read below is on the far side of those commits too.
    with merchant_scope(session, context.merchant.id):
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
