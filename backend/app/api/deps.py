"""Shared FastAPI dependencies."""

from typing import Annotated

from fastapi import Cookie, Depends, Header, HTTPException, status

from app.core.security import check_admin_key, verify_session_token


def require_operator(
    x_admin_key: Annotated[str | None, Header()] = None,
    vasooli_session: Annotated[str | None, Cookie()] = None,
) -> str:
    """Every endpoint that touches merchant data or changes state.

    Accepts either credential:

    * `X-Admin-Key` — scripts, the scheduler, and the dashboard's server-side proxy.
    * A session cookie — a browser that has logged in.

    Applied to reads as well as writes. An invoice ledger is customer names, email
    addresses, amounts owed, and a full audit trail; leaving that readable to anyone
    who knows the URL is a data breach whether or not they can also change anything.

    Returns 401 (not 403): the caller is unauthenticated, and 403 would imply a
    recognised identity that lacks permission.
    """
    if check_admin_key(x_admin_key):
        return "service"

    subject = verify_session_token(vasooli_session)
    if subject:
        return subject

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Authentication required",
        headers={"WWW-Authenticate": "Bearer"},
    )


OperatorRequired = Depends(require_operator)

#: Retained so existing call sites keep working. Same gate — Vasooli has one role.
AdminRequired = OperatorRequired
