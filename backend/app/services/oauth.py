"""Provider OAuth lifecycle with one-time state and encrypted token exchange."""

from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass
from datetime import timedelta
from typing import Any
from urllib.parse import urlencode

import httpx
from sqlalchemy import text
from sqlmodel import Session, select

from app.core.clock import utcnow
from app.core.config import settings
from app.models import OAuthState
from app.services.payment_connections import save_connection


class OAuthConfigurationError(RuntimeError):
    """The provider application has not been configured for this environment."""


class OAuthExchangeError(RuntimeError):
    """The provider rejected an authorization code or refresh token."""


@dataclass(frozen=True)
class OAuthTokens:
    access_token: str
    refresh_token: str | None
    expires_in: int | None
    account_id: str | None
    scopes: list[str]


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def create_state(
    session: Session,
    *,
    merchant_id,
    user_id,
    provider: str,
    redirect_uri: str,
    metadata: dict[str, Any] | None = None,
    ttl_seconds: int = 600,
) -> str:
    raw = secrets.token_urlsafe(32)
    session.add(
        OAuthState(
            merchant_id=merchant_id,
            user_id=user_id,
            provider=provider,
            state_hash=_hash(raw),
            redirect_uri=redirect_uri,
            state_metadata=metadata or {},
            expires_at=utcnow() + timedelta(seconds=ttl_seconds),
        )
    )
    session.flush()
    return raw


def consume_state(session: Session, *, provider: str, raw_state: str) -> OAuthState:
    """Spend a one-time OAuth state and return the row it belonged to.

    `oauth_states` is RLS-isolated by merchant, but the whole point of this lookup is
    that the merchant is not known yet — the provider has just redirected a browser
    back to us. Publishing the hash we are about to look up lets the row's own policy
    admit exactly that one row and nothing else, the same way an invitation token
    resolves a tenant before there is a tenant context.
    """
    session.exec(
        text("SELECT set_config('app.oauth_state', :state_hash, true)").bindparams(
            state_hash=_hash(raw_state)
        )
    )
    row = session.exec(
        select(OAuthState).where(
            OAuthState.provider == provider,
            OAuthState.state_hash == _hash(raw_state),
        )
    ).first()
    if row is None or row.used_at is not None or row.expires_at <= utcnow():
        raise OAuthExchangeError("OAuth state is invalid or expired")
    row.used_at = utcnow()
    session.add(row)
    return row


def razorpay_authorization_url(state: str, redirect_uri: str) -> str:
    client_id = settings.razorpay_oauth_client_id
    if not client_id:
        raise OAuthConfigurationError("RAZORPAY_OAUTH_CLIENT_ID is not configured")
    return "https://auth.razorpay.com/authorize?" + urlencode(
        {
            "client_id": client_id,
            "response_type": "code",
            "redirect_uri": redirect_uri,
            "scope": settings.razorpay_oauth_scope,
            "state": state,
        }
    )


def _provider_error(response: httpx.Response) -> OAuthExchangeError:
    try:
        detail = response.json()
    except ValueError:
        detail = response.text[:300]
    return OAuthExchangeError(f"OAuth provider rejected request ({response.status_code}): {detail}")


def exchange_razorpay_code(code: str, redirect_uri: str) -> OAuthTokens:
    client_id = settings.razorpay_oauth_client_id
    client_secret = settings.razorpay_oauth_client_secret
    if not client_id or not client_secret:
        raise OAuthConfigurationError("Razorpay OAuth client credentials are not configured")
    response = httpx.post(
        settings.razorpay_oauth_token_url,
        json={
            "client_id": client_id,
            "client_secret": client_secret,
            "grant_type": "authorization_code",
            "redirect_uri": redirect_uri,
            "code": code,
            "mode": settings.razorpay_oauth_mode,
        },
        timeout=settings.razorpay_timeout_seconds,
    )
    if response.is_error:
        raise _provider_error(response)
    payload = response.json()
    access_token = payload.get("access_token")
    if not access_token:
        raise OAuthExchangeError("Razorpay OAuth response did not include an access token")
    return OAuthTokens(
        access_token=access_token,
        refresh_token=payload.get("refresh_token"),
        expires_in=int(payload["expires_in"]) if payload.get("expires_in") else None,
        account_id=payload.get("razorpay_account_id"),
        scopes=[str(payload.get("scope"))] if payload.get("scope") else [],
    )


def refresh_razorpay_token(refresh_token: str) -> OAuthTokens:
    client_id = settings.razorpay_oauth_client_id
    client_secret = settings.razorpay_oauth_client_secret
    if not client_id or not client_secret:
        raise OAuthConfigurationError("Razorpay OAuth client credentials are not configured")
    response = httpx.post(
        settings.razorpay_oauth_token_url,
        json={
            "client_id": client_id,
            "client_secret": client_secret,
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "mode": settings.razorpay_oauth_mode,
        },
        timeout=settings.razorpay_timeout_seconds,
    )
    if response.is_error:
        raise _provider_error(response)
    payload = response.json()
    access_token = payload.get("access_token")
    if not access_token:
        raise OAuthExchangeError("Razorpay refresh response did not include an access token")
    return OAuthTokens(
        access_token=access_token,
        refresh_token=payload.get("refresh_token") or refresh_token,
        expires_in=int(payload["expires_in"]) if payload.get("expires_in") else None,
        account_id=payload.get("razorpay_account_id"),
        scopes=[str(payload.get("scope"))] if payload.get("scope") else [],
    )


def store_razorpay_tokens(session: Session, state: OAuthState, tokens: OAuthTokens):
    if not tokens.account_id:
        raise OAuthExchangeError("Razorpay OAuth response did not include account identity")
    return save_connection(
        session,
        state.merchant_id,
        mode="oauth",
        provider_account_id=tokens.account_id,
        access_token=tokens.access_token,
        refresh_token=tokens.refresh_token,
        scopes=tokens.scopes,
        token_expires_in=tokens.expires_in,
    )


def zoho_authorization_url(state: str, redirect_uri: str) -> str:
    client_id = settings.zoho_oauth_client_id
    if not client_id:
        raise OAuthConfigurationError("ZOHO_OAUTH_CLIENT_ID is not configured")
    return f"{settings.zoho_accounts_url.rstrip('/')}/oauth/v2/auth?" + urlencode(
        {
            "client_id": client_id,
            "response_type": "code",
            "redirect_uri": redirect_uri,
            "scope": settings.zoho_oauth_scope,
            "access_type": "offline",
            "prompt": "consent",
            "state": state,
        }
    )


def exchange_zoho_code(code: str, redirect_uri: str) -> OAuthTokens:
    client_id = settings.zoho_oauth_client_id
    client_secret = settings.zoho_oauth_client_secret
    if not client_id or not client_secret:
        raise OAuthConfigurationError("Zoho OAuth client credentials are not configured")
    response = httpx.post(
        f"{settings.zoho_accounts_url.rstrip('/')}/oauth/v2/token",
        params={
            "code": code,
            "client_id": client_id,
            "client_secret": client_secret,
            "redirect_uri": redirect_uri,
            "grant_type": "authorization_code",
        },
        timeout=settings.razorpay_timeout_seconds,
    )
    if response.is_error:
        raise _provider_error(response)
    payload = response.json()
    access_token = payload.get("access_token")
    if not access_token:
        raise OAuthExchangeError("Zoho OAuth response did not include an access token")
    return OAuthTokens(
        access_token=access_token,
        refresh_token=payload.get("refresh_token"),
        expires_in=int(payload["expires_in"]) if payload.get("expires_in") else None,
        account_id=None,
        scopes=settings.zoho_oauth_scope.split(","),
    )


def refresh_zoho_token(refresh_token: str) -> OAuthTokens:
    client_id = settings.zoho_oauth_client_id
    client_secret = settings.zoho_oauth_client_secret
    if not client_id or not client_secret:
        raise OAuthConfigurationError("Zoho OAuth client credentials are not configured")
    response = httpx.post(
        f"{settings.zoho_accounts_url.rstrip('/')}/oauth/v2/token",
        params={
            "refresh_token": refresh_token,
            "client_id": client_id,
            "client_secret": client_secret,
            "grant_type": "refresh_token",
        },
        timeout=settings.razorpay_timeout_seconds,
    )
    if response.is_error:
        raise _provider_error(response)
    payload = response.json()
    access_token = payload.get("access_token")
    if not access_token:
        raise OAuthExchangeError("Zoho refresh response did not include an access token")
    return OAuthTokens(
        access_token=access_token,
        refresh_token=refresh_token,
        expires_in=int(payload["expires_in"]) if payload.get("expires_in") else None,
        account_id=None,
        scopes=settings.zoho_oauth_scope.split(","),
    )
