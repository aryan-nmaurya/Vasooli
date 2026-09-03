"""Permission-backed live merchant team and invitation endpoints."""

import re
import uuid
from datetime import timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import text
from sqlmodel import select

from app.core.clock import utcnow
from app.core.config import settings
from app.core.db import SessionDep
from app.core.middleware import client_ip
from app.core.passwords import hash_live_password
from app.models import (
    MerchantInvitation,
    MerchantMembership,
    Role,
    User,
)
from app.services.auth import audit, new_opaque_token, normalize_email, token_hash
from app.services.authorization import (
    LiveContext,
    merchant_scope,
    require_live_permission,
    set_merchant_context,
)
from app.services.billing import BillingEntitlementError, assert_seat_entitled

router = APIRouter(prefix="/api/live", tags=["live-team"])
PASSWORD_RE = re.compile(r"^(?=.*[a-z])(?=.*[A-Z])(?=.*\d).{12,512}$")


class InviteRequest(BaseModel):
    email: EmailStr
    role_id: str


class AcceptInviteRequest(BaseModel):
    token: str = Field(min_length=20, max_length=200)
    password: str | None = Field(default=None, min_length=12, max_length=512)
    display_name: str | None = Field(default=None, max_length=160)


@router.get("/team")
def team(
    session: SessionDep,
    context: Annotated[LiveContext, Depends(require_live_permission("team.read"))],
) -> dict:
    memberships = session.exec(
        select(MerchantMembership).where(MerchantMembership.merchant_id == context.merchant.id)
    ).all()
    member_user_ids = [row.user_id for row in memberships]
    users = {
        user.id: user
        for user in session.exec(select(User).where(User.id.in_(member_user_ids))).all()  # type: ignore[union-attr]
    }
    roles = {
        r.id: r
        for r in session.exec(select(Role).where(Role.merchant_id == context.merchant.id)).all()
    }
    invitations = session.exec(
        select(MerchantInvitation).where(
            MerchantInvitation.merchant_id == context.merchant.id,
            MerchantInvitation.accepted_at.is_(None),  # type: ignore[union-attr]
            MerchantInvitation.revoked_at.is_(None),  # type: ignore[union-attr]
        )
    ).all()
    return {
        "roles": [
            {
                "id": str(role.id),
                "slug": role.slug,
                "name": role.name,
                "description": role.description,
            }
            for role in sorted(roles.values(), key=lambda item: item.slug)
        ],
        "members": [
            {
                "id": str(row.id),
                "email": users[row.user_id].email,
                "display_name": users[row.user_id].display_name,
                "role": roles[row.role_id].slug,
                "active": row.is_active,
                "joined_at": row.joined_at.isoformat(),
            }
            for row in memberships
            if row.user_id in users and row.role_id in roles
        ],
        "invitations": [
            {
                "id": str(invite.id),
                "email": invite.email,
                "role": roles[invite.role_id].slug if invite.role_id in roles else None,
                "expires_at": invite.expires_at.isoformat(),
            }
            for invite in invitations
        ],
    }


@router.post("/team/invitations", status_code=status.HTTP_201_CREATED)
def invite(
    payload: InviteRequest,
    request: Request,
    session: SessionDep,
    context: Annotated[LiveContext, Depends(require_live_permission("team.invite"))],
) -> dict:
    try:
        assert_seat_entitled(session, context.merchant.id)
    except BillingEntitlementError as exc:
        raise HTTPException(status.HTTP_402_PAYMENT_REQUIRED, str(exc)) from exc
    try:
        role_id = uuid.UUID(payload.role_id)
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid role") from exc
    role = session.exec(
        select(Role).where(Role.id == role_id, Role.merchant_id == context.merchant.id)
    ).first()
    if role is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Role not found")

    email = normalize_email(str(payload.email))
    existing_user = session.exec(select(User).where(User.email == email)).first()
    if existing_user is not None:
        membership = session.exec(
            select(MerchantMembership).where(
                MerchantMembership.user_id == existing_user.id,
                MerchantMembership.merchant_id == context.merchant.id,
            )
        ).first()
        if membership is not None:
            raise HTTPException(status.HTTP_409_CONFLICT, "User is already a member")

    now = utcnow()
    for old in session.exec(
        select(MerchantInvitation).where(
            MerchantInvitation.merchant_id == context.merchant.id,
            MerchantInvitation.email == email,
            MerchantInvitation.accepted_at.is_(None),  # type: ignore[union-attr]
            MerchantInvitation.revoked_at.is_(None),  # type: ignore[union-attr]
        )
    ).all():
        old.revoked_at = now
        session.add(old)

    raw = new_opaque_token()
    row = MerchantInvitation(
        merchant_id=context.merchant.id,
        email=email,
        role_id=role.id,
        token_hash=token_hash(raw),
        invited_by_user_id=context.user.id,
        expires_at=now + timedelta(days=7),
    )
    session.add(row)
    session.flush()
    audit(
        session,
        action="team.invitation_created",
        merchant_id=context.merchant.id,
        actor_user_id=context.user.id,
        object_type="merchant_invitation",
        object_id=row.id,
        ip_address=client_ip(request),
        detail={"email": email, "role": role.slug},
    )
    with merchant_scope(session, context.merchant.id):
        session.commit()
        return {
            "id": str(row.id),
            "status": "pending",
            "invitation_token": raw if settings.environment in {"local", "test"} else None,
        }


@router.delete("/team/invitations/{invitation_id}")
def revoke_invitation(
    invitation_id: uuid.UUID,
    request: Request,
    session: SessionDep,
    context: Annotated[LiveContext, Depends(require_live_permission("team.manage"))],
) -> dict[str, str]:
    invitation = session.exec(
        select(MerchantInvitation).where(
            MerchantInvitation.id == invitation_id,
            MerchantInvitation.merchant_id == context.merchant.id,
            MerchantInvitation.accepted_at.is_(None),  # type: ignore[union-attr]
            MerchantInvitation.revoked_at.is_(None),  # type: ignore[union-attr]
        )
    ).first()
    if invitation is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Invitation not found")
    invitation.revoked_at = utcnow()
    session.add(invitation)
    audit(
        session,
        action="team.invitation_revoked",
        merchant_id=context.merchant.id,
        actor_user_id=context.user.id,
        object_type="merchant_invitation",
        object_id=invitation.id,
        ip_address=client_ip(request),
    )
    session.commit()
    return {"status": "revoked"}


@router.delete("/team/members/{membership_id}")
def revoke_membership(
    membership_id: uuid.UUID,
    request: Request,
    session: SessionDep,
    context: Annotated[LiveContext, Depends(require_live_permission("team.manage"))],
) -> dict[str, str]:
    membership = session.exec(
        select(MerchantMembership).where(
            MerchantMembership.id == membership_id,
            MerchantMembership.merchant_id == context.merchant.id,
            MerchantMembership.is_active.is_(True),  # type: ignore[union-attr]
        )
    ).first()
    if membership is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Membership not found")
    if membership.user_id == context.user.id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Owners cannot revoke their own access")
    membership.is_active = False
    membership.revoked_at = utcnow()
    session.add(membership)
    audit(
        session,
        action="team.membership_revoked",
        merchant_id=context.merchant.id,
        actor_user_id=context.user.id,
        object_type="merchant_membership",
        object_id=membership.id,
        ip_address=client_ip(request),
    )
    session.commit()
    return {"status": "revoked"}


@router.post("/auth/accept-invite")
def accept_invite(payload: AcceptInviteRequest, request: Request, session: SessionDep) -> dict:
    now = utcnow()
    invitation_token_hash = token_hash(payload.token)
    # RLS cannot infer a merchant until the opaque invitation has been resolved. The
    # policy permits only this exact hashed token lookup; no invitation data is
    # exposed by a broad tenant-less query.
    session.exec(
        text("SELECT set_config('app.invitation_token', :token_hash, true)").bindparams(
            token_hash=invitation_token_hash
        )
    )
    invitation = session.exec(
        select(MerchantInvitation).where(MerchantInvitation.token_hash == invitation_token_hash)
    ).first()
    if (
        invitation is None
        or invitation.accepted_at is not None
        or invitation.revoked_at is not None
        or invitation.expires_at <= now
    ):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invitation is invalid or expired")

    set_merchant_context(session, invitation.merchant_id)

    user = session.exec(select(User).where(User.email == invitation.email)).first()
    if user is None:
        if payload.password is None:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Password is required")
        if not PASSWORD_RE.fullmatch(payload.password):
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                "Password must include upper, lower, and numeric characters",
            )
        user = User(
            email=invitation.email,
            display_name=payload.display_name,
            password_hash=hash_live_password(payload.password),
            status="active",
            is_email_verified=True,
            email_verified_at=now,
        )
        session.add(user)
        session.flush()
    elif user.status != "active" or not user.is_email_verified:
        raise HTTPException(status.HTTP_409_CONFLICT, "Existing account is not active")

    existing = session.exec(
        select(MerchantMembership).where(
            MerchantMembership.user_id == user.id,
            MerchantMembership.merchant_id == invitation.merchant_id,
        )
    ).first()
    if existing is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "User is already a member")
    membership = MerchantMembership(
        user_id=user.id,
        merchant_id=invitation.merchant_id,
        role_id=invitation.role_id,
        invitation_id=invitation.id,
    )
    session.add(membership)
    session.flush()
    invitation.accepted_at = now
    session.add(invitation)
    audit(
        session,
        action="team.invitation_accepted",
        merchant_id=invitation.merchant_id,
        actor_user_id=user.id,
        object_type="merchant_membership",
        object_id=membership.id,
        ip_address=client_ip(request),
    )
    # Read before the commit rather than holding a merchant scope: this route runs for
    # someone who is not yet a member of the merchant, so there is no LiveContext to
    # scope to. The value is a plain UUID once copied out, so the post-commit re-SELECT
    # that would otherwise fail under RLS never happens.
    accepted_merchant_id = str(invitation.merchant_id)
    session.commit()
    return {"status": "accepted", "merchant_id": accepted_merchant_id}
