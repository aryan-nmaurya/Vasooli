"""FastAPI application factory."""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlmodel import Session

from app.api import (
    admin,
    auth,
    dashboard,
    demo,
    exports,
    health,
    invoices,
    payments,
    replies,
    webhooks,
)
from app.core.config import settings
from app.core.db import check_database, engine, has_active_operator
from app.core.logging import RequestContextMiddleware, configure_logging, get_logger
from app.core.middleware import (
    BodySizeLimitMiddleware,
    RateLimitMiddleware,
    SecurityHeadersMiddleware,
)
from app.scheduler.setup import shutdown_scheduler, start_scheduler
from app.services.demo_control import load_into_clock

log = get_logger("app")


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging()
    settings.assert_production_safe()

    db_ok, error = check_database()
    if db_ok:
        log.info("startup.db_connected")

        # The demo clock lives in the database; module state has to be rehydrated or
        # a restart quietly returns the system to real time in the middle of a review.
        if settings.demo_controls_enabled:
            with Session(engine) as session:
                offset = load_into_clock(session)
            if offset:
                log.warning("startup.demo_clock_active", offset_days=offset)

        if settings.is_production and not has_active_operator():
            raise RuntimeError(
                "No active operator account exists. Run scripts.manage_operator before startup."
            )
    else:
        # Don't refuse to boot: /health reports 503 and the platform retries. Crashing
        # here turns a transient DB blip into a redeploy loop.
        log.error("startup.db_unavailable", error=error)

    start_scheduler()

    log.info(
        "startup.complete",
        environment=settings.environment,
        scheduler_enabled=settings.scheduler_enabled,
        email_dry_run=settings.email_dry_run,
    )
    yield

    shutdown_scheduler()
    log.info("shutdown.complete")


def create_app() -> FastAPI:
    app = FastAPI(
        title="Vasooli",
        description="AI-powered B2B receivables recovery agent",
        version=health.VERSION,
        lifespan=lifespan,
        docs_url="/docs" if not settings.is_production else None,
    )

    # Order matters: middleware added last runs first. Body-size and rate limits
    # should reject a request before it reaches logging, routing, or the database.
    app.add_middleware(SecurityHeadersMiddleware)
    app.add_middleware(RequestContextMiddleware)
    app.add_middleware(RateLimitMiddleware)
    app.add_middleware(BodySizeLimitMiddleware)
    app.add_middleware(
        # An explicit origin allowlist, never "*". CORS is a browser convenience here,
        # NOT authorization — every endpoint is gated independently, because a
        # non-browser client ignores CORS entirely.
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["X-Request-ID"],
    )

    app.include_router(health.router)
    app.include_router(auth.router)
    app.include_router(invoices.router)
    app.include_router(webhooks.router)
    app.include_router(admin.router)
    app.include_router(replies.router)
    app.include_router(dashboard.router)
    app.include_router(payments.router)
    app.include_router(demo.router)
    app.include_router(exports.router)
    return app


app = create_app()
