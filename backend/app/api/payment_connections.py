"""Merchant-owned Razorpay collection account connections."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field
from sqlmodel import select

from app.core.clock import utcnow
from app.core.config import settings
from app.core.db import SessionDep
from app.core.middleware import client_ip
from app.models import PaymentConnection
from app.services.authorization import (
    LiveContext,
    merchant_scope,
    require_live_permission,
    require_live_reauth,
)
from app.services.oauth import (
    OAuthConfigurationError,
    OAuthExchangeError,
    consume_state,
    create_state,
    exchange_razorpay_code,
    razorpay_authorization_url,
    refresh_razorpay_token,
    store_razorpay_tokens,
)
from app.services.payment_connections import decrypt_secret, save_connection

router = APIRouter(prefix="/api/live/payment-connections", tags=["live-payment-connections"])


class ConnectionRequest(BaseModel):
    mode: str = Field(pattern=r"^(oauth|byok)$")
    provider_account_id: str = Field(min_length=2, max_length=120)
    access_token: str | None = Field(default=None, max_length=2000)
    refresh_token: str | None = Field(default=None, max_length=2000)
    api_key_id: str | None = Field(default=None, max_length=160)
    api_key_secret: str | None = Field(default=None, max_length=500)
    #: The merchant's Razorpay webhook signing secret. Without it their payment
    #: confirmations fail signature verification and only reconcile on the sweep.
    webhook_secret: str | None = Field(default=None, max_length=500)
    scopes: list[str] = Field(default_factory=list, max_length=20)


@router.get("/oauth/start")
def oauth_start(
    request: Request,
    session: SessionDep,
    context: Annotated[LiveContext, Depends(require_live_reauth("erp.configure"))],
) -> dict[str, str]:
    """Create a one-time Razorpay Partner OAuth authorization URL."""
    redirect_uri = settings.razorpay_oauth_redirect_uri or str(
        request.url_for("razorpay_oauth_callback")
    )
    try:
        state = create_state(
            session,
            merchant_id=context.merchant.id,
            user_id=context.user.id,
            provider="razorpay",
            redirect_uri=redirect_uri,
        )
        url = razorpay_authorization_url(state, redirect_uri)
    except OAuthConfigurationError as exc:
        session.rollback()
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from exc
    session.commit()
    return {"authorization_url": url, "provider": "razorpay"}


@router.get("/oauth/callback", name="razorpay_oauth_callback")
def oauth_callback(
    request: Request,
    session: SessionDep,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
):
    """Consume OAuth state and persist encrypted provider credentials.

    No browser redirect is trusted for entitlements or payment state. The callback
    only records the connection; the account is verified by the first signed API
    operation and by webhook account-id routing.
    """
    if error:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Razorpay authorization failed: {error}")
    if not code or not state:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "OAuth code and state are required")
    try:
        state_row = consume_state(session, provider="razorpay", raw_state=state)
        from app.services.authorization import set_merchant_context

        set_merchant_context(session, state_row.merchant_id)
        tokens = exchange_razorpay_code(code, state_row.redirect_uri)
        connection = store_razorpay_tokens(session, state_row, tokens)
        # Copied out before the commit. `set_merchant_context` above is
        # transaction-local and dies here, so a post-commit attribute read would
        # re-SELECT with no tenant and fail under the NOBYPASSRLS role production
        # uses. The OAuth code is already spent by this point, so a 500 here is
        # unrecoverable for the merchant — they cannot retry with the same code.
        connected_account_id = connection.provider_account_id
        session.commit()
    except (OAuthConfigurationError, OAuthExchangeError, ValueError) as exc:
        session.rollback()
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    target = settings.frontend_live_integrations_url
    if settings.environment in {"local", "test"}:
        return {
            "status": "connected",
            "provider": "razorpay",
            "provider_account_id": connected_account_id,
        }
    return RedirectResponse(
        url=f"{target}?connected=razorpay", status_code=status.HTTP_303_SEE_OTHER
    )


@router.post("/oauth/refresh")
def oauth_refresh(
    request: Request,
    session: SessionDep,
    context: Annotated[LiveContext, Depends(require_live_reauth("erp.configure"))],
) -> dict[str, str | None]:
    row = session.exec(
        select(PaymentConnection).where(PaymentConnection.merchant_id == context.merchant.id)
    ).first()
    if row is None or row.mode != "oauth" or not row.refresh_token_encrypted:
        raise HTTPException(status.HTTP_409_CONFLICT, "No refreshable Razorpay OAuth connection")
    try:
        tokens = refresh_razorpay_token(decrypt_secret(row.refresh_token_encrypted))
        refreshed = save_connection(
            session,
            context.merchant.id,
            mode="oauth",
            provider_account_id=tokens.account_id or row.provider_account_id or "unknown",
            access_token=tokens.access_token,
            refresh_token=tokens.refresh_token,
            scopes=tokens.scopes or row.scopes,
            token_expires_in=tokens.expires_in,
        )
        # Same reason as the callback above, and the same cost: the refresh token has
        # already been exchanged with Razorpay, so a 500 after this point leaves the
        # merchant holding a spent token and an error message.
        refreshed_status = refreshed.status
        refreshed_account_id = refreshed.provider_account_id
        refreshed_expires_at = refreshed.token_expires_at
        session.commit()
    except (OAuthConfigurationError, OAuthExchangeError, ValueError) as exc:
        session.rollback()
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc
    return {
        "status": refreshed_status,
        "provider_account_id": refreshed_account_id,
        "expires_at": refreshed_expires_at.isoformat() if refreshed_expires_at else None,
    }


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
        # Presence only — the secret itself is never returned. Without it the
        # merchant's payment confirmations fail verification and only reconcile on
        # the hourly sweep, so the UI has to be able to say so.
        "webhook_secret_present": bool(row.webhook_secret_encrypted),
    }


@router.put("")
def connect(
    payload: ConnectionRequest,
    request: Request,
    session: SessionDep,
    context: Annotated[LiveContext, Depends(require_live_reauth("erp.configure"))],
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
        webhook_secret=payload.webhook_secret,
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
        ip_address=client_ip(request),
        detail={"mode": row.mode, "provider_account_id": row.provider_account_id},
    )
    with merchant_scope(session, context.merchant.id):
        session.commit()
        return {
            "status": row.status,
            "mode": row.mode,
            "provider_account_id": row.provider_account_id,
            "webhook_secret_present": bool(row.webhook_secret_encrypted),
        }


@router.delete("")
def disconnect(
    request: Request,
    session: SessionDep,
    context: Annotated[LiveContext, Depends(require_live_reauth("erp.configure"))],
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
            ip_address=client_ip(request),
        )
        session.commit()
    return {"status": "revoked"}
