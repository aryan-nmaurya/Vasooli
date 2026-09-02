"""Database-backed operator login. Doc §12."""

from datetime import timedelta

from fastapi import APIRouter, HTTPException, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import func
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
from app.services.authorization import service_scope
from app.services.demo_scope import demo_invoice_ids

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


def _demo_invoice_count(session) -> int | None:
    """Size of the seeded demo ledger, or None if it cannot be determined.

    None rather than 0 on failure: the guide renders a neutral sentence for None, and
    reporting "0 seeded invoices" to a reviewer because a query failed is worse than
    saying nothing about the number at all.
    """
    try:
        with service_scope(session):
            return session.exec(
                select(func.count()).select_from(demo_invoice_ids().subquery())
            ).one()
    except Exception as exc:
        log.warning("auth.demo_invoice_count_unavailable", error=str(exc))
        return None


@router.get("/modes")
def auth_modes(session: SessionDep) -> dict[str, bool | int | None]:
    """What sign-in routes exist. Public, and deliberately carries no secret.

    The login page is unauthenticated by definition, so it cannot ask a gated endpoint
    whether the reviewer button should be rendered. Showing a button that can only
    fail is worse than showing none.

    `reviewer_access` therefore answers "will this actually work?", not "is the flag
    on?". The flag alone was not enough: `reviewer_login` also requires the configured
    account to exist, be active, and hold the `auditor` role — so a deployment that
    enabled the flag and forgot the account advertised a button that returned 403 to
    every reviewer who pressed it. That is exactly the dead end the reviewer path was
    built to remove, and the check is cheap.

    `live_registration` answers the same question for the live door. The sign-in page
    offers both a live workspace and the demo, and self-serve registration ships dark —
    so without this the page would advertise a "Create workspace" link that 403s.
    """
    reviewer_ready = False
    if settings.reviewer_access_enabled:
        account = session.exec(
            select(OperatorAccount).where(OperatorAccount.username == settings.reviewer_username)
        ).first()
        reviewer_ready = account is not None and account.is_active and account.role == "auditor"
    return {
        "reviewer_access": reviewer_ready,
        "live_registration": settings.live_registration_enabled,
        # Same question for the demo controls. The reviewer guide has a whole section
        # telling a judge to move the clock; where the controls are off that section
        # walks them into a button that does nothing, which is the dead end this
        # endpoint exists to prevent.
        "demo_controls": settings.demo_controls_enabled,
        # How big the seeded ledger actually is. The guide used to say "eight", which
        # is wrong the moment the seed changes. Counted under service scope because
        # this endpoint has no tenant context and would otherwise always report zero
        # under the deployed role; it is a demo row count, not tenant data.
        "demo_invoice_count": _demo_invoice_count(session),
    }


@router.post("/reviewer")
def reviewer_login(response: Response, session: SessionDep) -> dict[str, str]:
    """Open a read-only session without a credential having to be sent to anyone.

    The public "Open the live demo" call to action previously ended at a login wall
    with no way through, which made a working system look like a dead demo. The
    alternative — mailing a shared password around — puts a real credential in
    somebody's inbox and gives every reviewer the same one.

    The read-only guarantee is NOT made here. It is the auditor role check in
    `app.api.deps.require_operator`, which refuses every non-GET request. This endpoint
    only refuses to issue a session for an account that is not an auditor, so a
    mistyped REVIEWER_USERNAME pointing at an admin account fails closed instead of
    handing a stranger write access to a receivables ledger.
    """
    if not settings.reviewer_access_enabled:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found")

    username = settings.reviewer_username.casefold()
    account = session.exec(
        select(OperatorAccount).where(OperatorAccount.username == username)
    ).first()

    if account is None or not account.is_active:
        log.error("auth.reviewer_account_missing", username=username)
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "Reviewer access is enabled but the reviewer account is not set up",
        )

    if account.role != "auditor":
        # Fail closed. Enabling a convenience must never be the thing that grants
        # write access to customer data.
        log.error("auth.reviewer_account_not_auditor", username=username, role=account.role)
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "The reviewer account must have the read-only auditor role",
        )

    now = utcnow()
    account.last_login_at = now
    account.updated_at = now
    session.add(account)
    session.commit()

    token = create_session_token(f"{account.username}~{account.session_version}")
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=DEFAULT_TTL_SECONDS,
        httponly=True,
        samesite="lax",
        secure=settings.is_production,
        path="/",
    )
    log.info("auth.reviewer_session_issued", operator=account.username)
    return {"status": "ok", "username": account.username, "role": account.role}


@router.post("/logout")
def logout(response: Response) -> dict[str, str]:
    response.delete_cookie(SESSION_COOKIE, path="/")
    return {"status": "ok"}
