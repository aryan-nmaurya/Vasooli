"""Liveness and readiness endpoints."""

from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory
from fastapi import APIRouter, Response, status
from pydantic import BaseModel
from sqlalchemy import text

from app.core.config import settings
from app.core.db import check_database, engine
from app.core.logging import get_logger

log = get_logger("health")

router = APIRouter(tags=["ops"])

VERSION = "0.1.0"


class HealthResponse(BaseModel):
    status: str
    db: str
    version: str
    environment: str
    detail: str | None = None
    #: Applied Alembic revision, and whether it is the latest one on disk. A schema
    #: that silently lags head is otherwise invisible without shelling into the host.
    schema_revision: str | None = None
    schema_current: bool | None = None


def _schema_state() -> tuple[str | None, bool | None]:
    """The applied revision and whether it matches head.

    Reported, never fatal: a container serving correctly against a schema one
    revision behind should be visible, not pulled out of rotation. Any failure to
    determine the revision is logged and reported as unknown for the same reason.
    """
    try:
        with engine.connect() as connection:
            applied = connection.execute(text("SELECT version_num FROM alembic_version")).scalar()
        config = Config(str(Path(__file__).resolve().parents[2] / "alembic.ini"))
        config.set_main_option(
            "script_location", str(Path(__file__).resolve().parents[2] / "alembic")
        )
        heads = set(ScriptDirectory.from_config(config).get_heads())
        return applied, (applied in heads if applied else False)
    except Exception as exc:  # missing table, unreadable scripts, DB down
        log.warning("health.schema_revision_unavailable", error=str(exc))
        return None, None


@router.get("/health", response_model=HealthResponse)
def health(response: Response) -> HealthResponse:
    """Readiness check — includes a real database round-trip.

    Returns 503 when the database is unreachable so Railway's healthcheck pulls the
    instance out of rotation instead of serving 500s.
    """
    db_ok, error = check_database()
    if not db_ok:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    revision, current = _schema_state() if db_ok else (None, None)
    # A stale schema degrades the reported status but never the HTTP code: it must be
    # visible without SSH, and it must not take a working instance out of rotation.
    healthy = db_ok and current is not False
    return HealthResponse(
        status="ok" if healthy else "degraded",
        db="ok" if db_ok else "unavailable",
        version=VERSION,
        environment=settings.environment,
        detail=error or (None if current is not False else f"schema at {revision}, behind head"),
        schema_revision=revision,
        schema_current=current,
    )


@router.get("/live", include_in_schema=False)
def live() -> dict[str, str]:
    """Process liveness only — no dependencies touched."""
    return {"status": "ok"}
