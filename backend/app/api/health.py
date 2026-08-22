"""Liveness and readiness endpoints."""

from fastapi import APIRouter, Response, status
from pydantic import BaseModel

from app.core.config import settings
from app.core.db import check_database

router = APIRouter(tags=["ops"])

VERSION = "0.1.0"


class HealthResponse(BaseModel):
    status: str
    db: str
    version: str
    environment: str
    detail: str | None = None


@router.get("/health", response_model=HealthResponse)
def health(response: Response) -> HealthResponse:
    """Readiness check — includes a real database round-trip.

    Returns 503 when the database is unreachable so Railway's healthcheck pulls the
    instance out of rotation instead of serving 500s.
    """
    db_ok, error = check_database()
    if not db_ok:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return HealthResponse(
        status="ok" if db_ok else "degraded",
        db="ok" if db_ok else "unavailable",
        version=VERSION,
        environment=settings.environment,
        detail=error,
    )


@router.get("/live", include_in_schema=False)
def live() -> dict[str, str]:
    """Process liveness only — no dependencies touched."""
    return {"status": "ok"}
