"""Dashboard login. Doc §12.

Exchanges the dashboard password for a short-lived signed cookie. The password is
never stored by the browser and never sent again after login.
"""

from fastapi import APIRouter, HTTPException, Response, status
from pydantic import BaseModel, Field

from app.core.config import settings
from app.core.logging import get_logger
from app.core.security import (
    DEFAULT_TTL_SECONDS,
    SESSION_COOKIE,
    check_dashboard_password,
    create_session_token,
)

router = APIRouter(prefix="/api/auth", tags=["auth"])
log = get_logger("auth")


class LoginRequest(BaseModel):
    password: str = Field(min_length=1, max_length=512)


@router.post("/login")
def login(payload: LoginRequest, response: Response) -> dict[str, str]:
    if not check_dashboard_password(payload.password):
        # Deliberately vague, and the attempt is logged without the password.
        log.warning("auth.login_failed")
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid password")

    token = create_session_token()
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
    log.info("auth.login_succeeded")
    return {"status": "ok"}


@router.post("/logout")
def logout(response: Response) -> dict[str, str]:
    response.delete_cookie(SESSION_COOKIE, path="/")
    return {"status": "ok"}
