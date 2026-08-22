"""Shared FastAPI dependencies."""

from typing import Annotated

from fastapi import Depends, Header, HTTPException, status

from app.core.config import settings


def require_admin(x_admin_key: Annotated[str | None, Header()] = None) -> None:
    """Guard for endpoints that mutate state or trigger outbound work.

    Compared with `secrets.compare_digest` so the check does not leak key length or a
    matching prefix through response timing.

    This key must never reach the browser. The Phase 10 dashboard calls these
    endpoints through a Next.js route handler that holds the key server-side; a
    NEXT_PUBLIC_ variable would ship it in the client bundle.
    """
    import secrets

    if not x_admin_key or not secrets.compare_digest(x_admin_key, settings.admin_api_key):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or invalid X-Admin-Key",
        )


AdminRequired = Depends(require_admin)
