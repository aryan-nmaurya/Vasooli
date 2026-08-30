"""Live registration and email-first authentication.

Cookies are deliberately distinct from the frozen demo's ``vasooli_session`` cookie.
"""

import re
from datetime import timedelta
from typing import Annotated
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, Cookie, HTTPException, Request, Response, status
from pydantic import BaseModel, EmailStr, Field, model_validator
from sqlmodel import select

from app.core.clock import utcnow
from app.core.config import settings
from app.core.db import SessionDep
from app.core.passwords import (
    hash_live_password,
    live_password_needs_rehash,
    verify_live_password,
)
from app.core.security import create_session_token
from app.models import AuthEvent, Merchant, MerchantMembership, User, UserSession
from app.services.auth import (
    ACCESS_TTL_SECONDS,
    LiveAuthError,
    audit,
    bootstrap_roles,
    consume_auth_token,
    create_auth_token,
    create_live_user,
    issue_session,
    normalize_email,
    revoke_user_sessions,
    rotate_refresh_token,
)
from app.services.authorization import (
    LIVE_ACCESS_COOKIE,
    LIVE_REFRESH_COOKIE,
    live_access_subject,
    parse_live_access,
    set_merchant_context,
)

router = APIRouter(prefix="/api/live/auth", tags=["live-auth"])

MAX_FAILURES = 5
LOCK_MINUTES = 15
PASSWORD_RE = re.compile(r"^(?=.*[a-z])(?=.*[A-Z])(?=.*\d).{12,512}$")


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=12, max_length=512)
    legal_business_name: str = Field(min_length=2, max_length=240)
    display_name: str | None = Field(default=None, max_length=160)
    country: str = Field(default="IN", pattern=r"^[A-Z]{2}$")
    timezone: str = Field(default="Asia/Kolkata", min_length=1, max_length=80)
    accept_terms: bool
    accept_privacy: bool

    @model_validator(mode="after")
    def validate_live_registration(self):
        if not self.accept_terms or not self.accept_privacy:
            raise ValueError("Terms and privacy acceptance are required")
        if not PASSWORD_RE.fullmatch(self.password):
            raise ValueError(
                "Password must be at least 12 characters with upper, lower, and numeric characters"
            )
        try:
            ZoneInfo(self.timezone)
        except ZoneInfoNotFoundError as exc:
            raise ValueError("Unknown timezone") from exc
        return self


class TokenRequest(BaseModel):
    token: str = Field(min_length=20, max_length=200)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=512)


class ForgotRequest(BaseModel):
    email: EmailStr


class ResetRequest(TokenRequest):
    password: str = Field(min_length=12, max_length=512)

    @model_validator(mode="after")
    def validate_password(self):
        if not PASSWORD_RE.fullmatch(self.password):
            raise ValueError(
                "Password must be at least 12 characters with upper, lower, and numeric characters"
            )
        return self


def _client_ip(request: Request) -> str | None:
    return request.client.host if request.client else None


def _public_token(raw: str) -> str | None:
    # Production tokens belong in email only. Local/test returning the token makes the
    # lifecycle testable before the live sending provider is connected in Phase 5.
    return raw if settings.environment in {"local", "test"} else None


def _set_session_cookies(response: Response, user: User, issued) -> None:
    access = create_session_token(
        live_access_subject(user.id, issued.session.id), ttl_seconds=ACCESS_TTL_SECONDS
    )
    response.set_cookie(
        LIVE_ACCESS_COOKIE,
        access,
        max_age=ACCESS_TTL_SECONDS,
        httponly=True,
        secure=settings.is_production,
        samesite="lax",
        path="/",
    )
    response.set_cookie(
        LIVE_REFRESH_COOKIE,
        issued.refresh_token,
        max_age=30 * 24 * 60 * 60,
        httponly=True,
        secure=settings.is_production,
        samesite="strict",
        path="/api/live/auth",
    )


def _clear_session_cookies(response: Response) -> None:
    response.delete_cookie(LIVE_ACCESS_COOKIE, path="/")
    response.delete_cookie(LIVE_REFRESH_COOKIE, path="/api/live/auth")


@router.post("/register", status_code=status.HTTP_201_CREATED)
def register(payload: RegisterRequest, request: Request, session: SessionDep) -> dict:
    if not settings.live_registration_enabled:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found")
    email = normalize_email(str(payload.email))
    if session.exec(select(User.id).where(User.email == email)).first() is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "An account already exists for this email")

    now = utcnow()
    user = create_live_user(email, payload.password, payload.display_name)
    session.add(user)
    session.flush()
    merchant = Merchant(
        name=payload.legal_business_name,
        legal_name=payload.legal_business_name,
        contact_email=email,
        country=payload.country,
        timezone=payload.timezone,
        mode="live",
        status="onboarding",
        is_demo=False,
        onboarding_state={"identity": "pending_verification"},
        terms_accepted_at=now,
        privacy_accepted_at=now,
    )
    session.add(merchant)
    session.flush()
    set_merchant_context(session, merchant.id)
    roles = bootstrap_roles(session, merchant)
    session.add(
        MerchantMembership(
            user_id=user.id,
            merchant_id=merchant.id,
            role_id=roles["owner"].id,
        )
    )
    raw_token = create_auth_token(session, user, "verify_email")
    session.add(
        AuthEvent(
            user_id=user.id,
            email=email,
            event_type="registered",
            success=True,
            ip_address=_client_ip(request),
        )
    )
    audit(
        session,
        action="merchant.registered",
        merchant_id=merchant.id,
        actor_user_id=user.id,
        object_type="merchant",
        object_id=merchant.id,
        ip_address=_client_ip(request),
    )
    session.commit()
    return {
        "status": "verification_required",
        "merchant_id": str(merchant.id),
        "verification_token": _public_token(raw_token),
    }


@router.post("/verify-email")
def verify_email(payload: TokenRequest, request: Request, session: SessionDep) -> dict:
    try:
        user = consume_auth_token(session, payload.token, "verify_email")
    except LiveAuthError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    now = utcnow()
    user.is_email_verified = True
    user.email_verified_at = now
    user.status = "active"
    session.add(user)
    memberships = session.exec(
        select(MerchantMembership).where(MerchantMembership.user_id == user.id)
    ).all()
    for membership in memberships:
        merchant = session.get(Merchant, membership.merchant_id)
        if merchant is not None and merchant.onboarding_state.get("identity") != "verified":
            merchant.onboarding_state = {**merchant.onboarding_state, "identity": "verified"}
            session.add(merchant)
    session.add(
        AuthEvent(
            user_id=user.id,
            email=user.email,
            event_type="email_verified",
            success=True,
            ip_address=_client_ip(request),
        )
    )
    session.commit()
    return {"status": "verified"}


@router.post("/login")
def login(payload: LoginRequest, request: Request, response: Response, session: SessionDep) -> dict:
    email = normalize_email(str(payload.email))
    user = session.exec(select(User).where(User.email == email)).first()
    now = utcnow()
    valid = user is not None and verify_live_password(payload.password, user.password_hash)
    locked = user is not None and user.locked_until is not None and user.locked_until > now
    if user is None or not valid or locked or user.status != "active" or not user.is_email_verified:
        if user is not None and not locked and not valid:
            user.failed_login_attempts += 1
            if user.failed_login_attempts >= MAX_FAILURES:
                user.locked_until = now + timedelta(minutes=LOCK_MINUTES)
            session.add(user)
        session.add(
            AuthEvent(
                user_id=user.id if user else None,
                email=email,
                event_type="login_failed",
                success=False,
                ip_address=_client_ip(request),
            )
        )
        session.commit()
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid credentials")

    if live_password_needs_rehash(user.password_hash):
        user.password_hash = hash_live_password(payload.password)
        user.password_changed_at = now
    user.failed_login_attempts = 0
    user.locked_until = None
    user.last_login_at = now
    user.last_login_ip = _client_ip(request)
    session.add(user)
    issued = issue_session(
        session,
        user,
        ip_address=_client_ip(request),
        user_agent=request.headers.get("user-agent"),
    )
    session.add(
        AuthEvent(
            user_id=user.id,
            email=user.email,
            event_type="login_succeeded",
            success=True,
            ip_address=_client_ip(request),
        )
    )
    session.commit()
    _set_session_cookies(response, user, issued)
    memberships = session.exec(
        select(MerchantMembership).where(
            MerchantMembership.user_id == user.id,
            MerchantMembership.is_active.is_(True),  # type: ignore[union-attr]
        )
    ).all()
    return {
        "status": "ok",
        "user_id": str(user.id),
        "merchants": [str(row.merchant_id) for row in memberships],
    }


@router.post("/refresh")
def refresh(
    request: Request,
    response: Response,
    session: SessionDep,
    refresh_token: Annotated[str | None, Cookie(alias=LIVE_REFRESH_COOKIE)] = None,
) -> dict:
    if not refresh_token:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Refresh token required")
    try:
        user, issued = rotate_refresh_token(
            session,
            refresh_token,
            ip_address=_client_ip(request),
            user_agent=request.headers.get("user-agent"),
        )
    except LiveAuthError as exc:
        _clear_session_cookies(response)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, str(exc)) from exc
    session.commit()
    _set_session_cookies(response, user, issued)
    return {"status": "rotated"}


def _current_session(
    session: SessionDep, access_token: str | None
) -> tuple[User, UserSession] | None:
    identity = parse_live_access(access_token)
    if identity is None:
        return None
    user = session.get(User, identity[0])
    row = session.get(UserSession, identity[1])
    if (
        user is None
        or row is None
        or row.user_id != user.id
        or row.revoked_at is not None
        or row.expires_at <= utcnow()
    ):
        return None
    return user, row


@router.post("/logout")
def logout(
    response: Response,
    session: SessionDep,
    access_token: Annotated[str | None, Cookie(alias=LIVE_ACCESS_COOKIE)] = None,
) -> dict:
    current = _current_session(session, access_token)
    if current is not None:
        current[1].revoked_at = utcnow()
        current[1].revoke_reason = "logout"
        session.add(current[1])
        session.commit()
    _clear_session_cookies(response)
    return {"status": "ok"}


@router.get("/sessions")
def list_sessions(
    session: SessionDep,
    access_token: Annotated[str | None, Cookie(alias=LIVE_ACCESS_COOKIE)] = None,
) -> list[dict]:
    current = _current_session(session, access_token)
    if current is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Live authentication required")
    user, active = current
    rows = session.exec(
        select(UserSession)
        .where(UserSession.user_id == user.id)
        .order_by(UserSession.created_at.desc())  # type: ignore[attr-defined]
    ).all()
    return [
        {
            "id": str(row.id),
            "current": row.id == active.id,
            "created_at": row.created_at.isoformat(),
            "last_used_at": row.last_used_at.isoformat(),
            "expires_at": row.expires_at.isoformat(),
            "revoked": row.revoked_at is not None,
            "ip_address": row.ip_address,
            "user_agent": row.user_agent,
        }
        for row in rows
    ]


@router.post("/logout-all")
def logout_all(
    response: Response,
    session: SessionDep,
    access_token: Annotated[str | None, Cookie(alias=LIVE_ACCESS_COOKIE)] = None,
) -> dict:
    current = _current_session(session, access_token)
    if current is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Live authentication required")
    count = revoke_user_sessions(session, current[0].id, "logout_all")
    session.commit()
    _clear_session_cookies(response)
    return {"status": "ok", "revoked": count}


@router.post("/forgot-password")
def forgot_password(payload: ForgotRequest, request: Request, session: SessionDep) -> dict:
    email = normalize_email(str(payload.email))
    user = session.exec(select(User).where(User.email == email)).first()
    raw_token = None
    if user is not None and user.status not in {"deleted", "suspended"}:
        raw_token = create_auth_token(session, user, "password_reset")
    session.add(
        AuthEvent(
            user_id=user.id if user else None,
            email=email,
            event_type="password_reset_requested",
            success=True,
            ip_address=_client_ip(request),
        )
    )
    session.commit()
    return {
        "status": "accepted",
        "message": "If the account exists, reset instructions have been sent.",
        "reset_token": _public_token(raw_token) if raw_token else None,
    }


@router.post("/reset-password")
def reset_password(payload: ResetRequest, request: Request, session: SessionDep) -> dict:
    try:
        user = consume_auth_token(session, payload.token, "password_reset")
    except LiveAuthError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    user.password_hash = hash_live_password(payload.password)
    user.password_changed_at = utcnow()
    user.failed_login_attempts = 0
    user.locked_until = None
    session.add(user)
    revoke_user_sessions(session, user.id, "password_reset")
    session.add(
        AuthEvent(
            user_id=user.id,
            email=user.email,
            event_type="password_reset_completed",
            success=True,
            ip_address=_client_ip(request),
        )
    )
    session.commit()
    return {"status": "password_reset"}
