"""FastAPI application factory."""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import health
from app.core.config import settings
from app.core.db import check_database
from app.core.logging import RequestContextMiddleware, configure_logging, get_logger

log = get_logger("app")


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging()
    settings.assert_production_safe()

    db_ok, error = check_database()
    if db_ok:
        log.info("startup.db_connected")
    else:
        # Don't refuse to boot: /health reports 503 and the platform retries. Crashing
        # here turns a transient DB blip into a redeploy loop.
        log.error("startup.db_unavailable", error=error)

    log.info(
        "startup.complete",
        environment=settings.environment,
        scheduler_enabled=settings.scheduler_enabled,
        email_dry_run=settings.email_dry_run,
    )
    yield
    log.info("shutdown.complete")


def create_app() -> FastAPI:
    app = FastAPI(
        title="Vasooli",
        description="AI-powered B2B receivables recovery agent",
        version=health.VERSION,
        lifespan=lifespan,
        docs_url="/docs" if not settings.is_production else None,
    )

    app.add_middleware(RequestContextMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["X-Request-ID"],
    )

    app.include_router(health.router)
    return app


app = create_app()
