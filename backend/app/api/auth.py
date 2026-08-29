"""Database-backed operator login. Doc §12."""

from datetime import timedelta

from fastapi import APIRouter, HTTPException, Response, status
from pydantic import BaseModel, Field
from sqlmodel import select

from app.core.clock import utcnow
from app.core.config import settings
from app.core.db import SessionDep
from app.core.logging import get_logger
from app.core.passwords import perform_dummy_password_check, verify_password
from app.core.security import (
    DEFAULT_TTL_SECONDS,
    SESSION_COOKIE,
    create_session_token,
)
from app.models import OperatorAccount

router = APIRouter(prefix="/api/auth", tags=["auth"])
log = get_logger("auth")
MAX_ACCOUNT_FAILURES = 5
ACCOUNT_LOCK_MINUTES = 15


class LoginRequest(BaseModel):
    username: str = Field(pattern=r"^[A-Za-z0-9_-]{2,64}$")
    password: str = Field(min_length=1, max_length=512)


@router.post("/login")
def login(payload: LoginRequest, response: Response, session: SessionDep) -> dict[str, str]:
    username = payload.username.casefold()
    account = session.exec(
        select(OperatorAccount).where(OperatorAccount.username == username)
    ).first()
    now = utcnow()

    if account is None:
        perform_dummy_password_check(payload.password)
        log.warning("auth.login_failed", username=username)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid credentials")

    locked = account.locked_until is not None and account.locked_until > now
    valid_password = verify_password(payload.password, account.password_hash)
    if not account.is_active or locked or not valid_password:
        if account.is_active and not locked and not valid_password:
            account.failed_login_attempts += 1
            if account.failed_login_attempts >= MAX_ACCOUNT_FAILURES:
                account.locked_until = now + timedelta(minutes=ACCOUNT_LOCK_MINUTES)
            account.updated_at = now
            session.add(account)
            session.commit()
        # Deliberately identical for unknown, disabled, locked, and wrong-password
        # accounts so the endpoint does not become a username/role oracle.
        log.warning("auth.login_failed", username=username)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid credentials")

    account.failed_login_attempts = 0
    account.locked_until = None
    account.last_login_at = now
    account.updated_at = now
    session.add(account)
    session.commit()

    token = create_session_token(f"{account.username}~{account.session_version}")
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=DEFAULT_TTL_SECONDS,
        # httponly: JavaScript cannot read it, so an XSS bug cannot exfiltrate the
        # session. samesite=lax: the cookie is not attached to cross-site POSTs, which
        # is the CSRF protection for the state-changing endpoints.
        httponly=True,
        samesite="lax",
        secure=settings.is_production,
        path="/",
    )
    log.info("auth.login_succeeded", operator=account.username, role=account.role)
    return {
        "status": "ok",
        "username": account.username,
        "role": account.role,
    }


@router.post("/logout")
def logout(response: Response) -> dict[str, str]:
    response.delete_cookie(SESSION_COOKIE, path="/")
    return {"status": "ok"}
