"""Shared FastAPI dependencies."""

from typing import Annotated

from fastapi import Cookie, Depends, Header, HTTPException, Request, status
from sqlmodel import select

from app.core.db import SessionDep
from app.core.security import check_admin_key, verify_session_token
from app.models import OperatorAccount


def require_operator(
    request: Request,
    session: SessionDep,
    x_admin_key: Annotated[str | None, Header()] = None,
    vasooli_session: Annotated[str | None, Cookie()] = None,
) -> str:
    """Every endpoint that touches merchant data or changes state.

    Accepts either credential:

    * `X-Admin-Key` — service scripts and smoke checks only.
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
        username, separator, version_raw = subject.rpartition("~")
        try:
            session_version = int(version_raw) if separator else 0
        except ValueError:
            session_version = 0
        account = session.exec(
            select(OperatorAccount).where(OperatorAccount.username == username)
        ).first()
        if account is not None and account.is_active and account.session_version == session_version:
            if account.role == "auditor" and request.method not in {"GET", "HEAD", "OPTIONS"}:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Auditor accounts are read-only",
                )
            return account.username

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Authentication required",
        headers={"WWW-Authenticate": "Bearer"},
    )


OperatorRequired = Depends(require_operator)

#: The same gate, but with the caller's identity injected. Use this where an
#: action is attributed to a person in the audit log — a decision recorded as
#: having been made by "someone" is not accountability.
Operator = Annotated[str, Depends(require_operator)]

#: Retained so existing call sites keep working. Role enforcement is centralized in
#: ``require_operator`` rather than duplicated at each router.
AdminRequired = OperatorRequired
