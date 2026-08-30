"""Fail-closed live tenant and permission resolution."""

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Annotated

from fastapi import Cookie, Depends, Header, HTTPException, Request, status
from sqlalchemy import text
from sqlmodel import select

from app.core.clock import utcnow
from app.core.db import SessionDep
from app.core.security import verify_session_token
from app.models import (
    Merchant,
    MerchantMembership,
    Permission,
    RolePermission,
    User,
    UserSession,
)
from app.services.reauth import consume_challenge

LIVE_ACCESS_COOKIE = "vasooli_live_access"
LIVE_REFRESH_COOKIE = "vasooli_live_refresh"


@dataclass(frozen=True)
class LiveContext:
    user: User
    merchant: Merchant
    membership: MerchantMembership
    session: UserSession
    permission: str


def set_merchant_context(session, merchant_id: uuid.UUID) -> None:
    """Set the transaction-local RLS tenant after an authorization decision."""
    session.exec(
        text("SELECT set_config('app.merchant_id', :merchant_id, true)").bindparams(
            merchant_id=str(merchant_id)
        )
    )


def live_access_subject(user_id: uuid.UUID, session_id: uuid.UUID) -> str:
    return f"live~{user_id}~{session_id}"


def parse_live_access(token: str | None) -> tuple[uuid.UUID, uuid.UUID] | None:
    subject = verify_session_token(token)
    if not subject:
        return None
    parts = subject.split("~")
    if len(parts) != 3 or parts[0] != "live":
        return None
    try:
        return uuid.UUID(parts[1]), uuid.UUID(parts[2])
    except ValueError:
        return None


def _has_permission(session, membership: MerchantMembership, codename: str) -> bool:
    return (
        session.exec(
            select(Permission.id)
            .join(RolePermission, RolePermission.permission_id == Permission.id)
            .where(
                RolePermission.role_id == membership.role_id,
                Permission.codename == codename,
            )
        ).first()
        is not None
    )


def require_live_permission(codename: str):
    def dependency(
        request: Request,
        session: SessionDep,
        x_merchant_id: Annotated[str | None, Header(alias="X-Merchant-ID")] = None,
        access_token: Annotated[str | None, Cookie(alias=LIVE_ACCESS_COOKIE)] = None,
    ) -> LiveContext:
        identity = parse_live_access(access_token)
        if identity is None:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Live authentication required")
        if not x_merchant_id:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "X-Merchant-ID is required")
        try:
            merchant_id = uuid.UUID(x_merchant_id)
        except ValueError as exc:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid merchant context") from exc

        user_id, session_id = identity
        user = session.get(User, user_id)
        live_session = session.get(UserSession, session_id)
        now: datetime = utcnow()
        if (
            user is None
            or user.status != "active"
            or not user.is_email_verified
            or live_session is None
            or live_session.user_id != user.id
            or live_session.revoked_at is not None
            or live_session.expires_at <= now
        ):
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Live session is not active")

        membership = session.exec(
            select(MerchantMembership).where(
                MerchantMembership.user_id == user.id,
                MerchantMembership.merchant_id == merchant_id,
                MerchantMembership.is_active.is_(True),  # type: ignore[union-attr]
            )
        ).first()
        merchant = session.get(Merchant, merchant_id)
        # The same 404 covers an unknown merchant and another tenant's merchant.
        if membership is None or merchant is None or merchant.is_demo or merchant.mode != "live":
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Merchant not found")
        if merchant.status in {"suspended", "closed"}:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Merchant is not active")
        if not _has_permission(session, membership, codename):
            raise HTTPException(status.HTTP_403_FORBIDDEN, f"Missing permission: {codename}")

        # PostgreSQL RLS policies read this transaction-local value. It cannot leak
        # through the connection pool after commit/rollback.
        set_merchant_context(session, merchant.id)
        request.state.live_user_id = str(user.id)
        request.state.merchant_id = str(merchant.id)
        return LiveContext(user, merchant, membership, live_session, codename)

    return dependency


def require_live_reauth(codename: str):
    """Require a fresh password/MFA-backed re-auth proof for sensitive actions.

    The challenge is single-use and bound to the authenticated user. Keeping this as
    a dependency makes it difficult for a new sensitive endpoint to accidentally omit
    the second factor while retaining the normal merchant permission checks.
    """
    permission_dependency = require_live_permission(codename)

    def dependency(
        session: SessionDep,
        context: LiveContext = Depends(permission_dependency),
        x_reauth_token: Annotated[str | None, Header(alias="X-Reauth-Token")] = None,
    ) -> LiveContext:
        if not x_reauth_token or not consume_challenge(session, context.user, x_reauth_token):
            raise HTTPException(
                status.HTTP_428_PRECONDITION_REQUIRED,
                "Recent re-authentication is required for this action",
            )
        session.commit()
        return context

    return dependency


def get_scoped_object(session, model, object_id: uuid.UUID, merchant_id: uuid.UUID):
    """Load tenant-owned objects by both keys so foreign IDs do not become an IDOR."""

    return session.exec(
        select(model).where(model.id == object_id, model.merchant_id == merchant_id)
    ).first()
