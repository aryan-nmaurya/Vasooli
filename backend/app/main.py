"""FastAPI application factory."""

from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlmodel import Session

from app.api import (
    admin,
    auth,
    billing,
    controls,
    dashboard,
    demo,
    exports,
    health,
    integrations,
    invoices,
    live_auth,
    live_dashboard,
    live_invoices,
    operations,
    payment_connections,
    payments,
    replies,
    team,
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
from app.services.auth import verify_reviewer_account
from app.services.demo_control import load_into_clock

log = get_logger("app")


def _scrub_validation_errors(errors: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Say what was wrong with the request without repeating the request back.

    FastAPI's default validation response includes `input`: the value that failed. On
    a login route the value that failed is the password, so a mistyped field name — or
    a client sending `{"password": ...}` without `username` — returned a 422 with the
    plaintext password in the body. That lands in browser devtools, proxy access logs,
    and any error tracker watching 4xx responses.

    `loc` and `msg` are what a client needs to fix the request. `input` and `ctx` are
    the parts that echo submitted data, and neither is needed to act on the error.
    """
    return [
        {"type": error.get("type"), "loc": error.get("loc"), "msg": error.get("msg")}
        for error in errors
    ]


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

        # Reviewer access is a public, credential-less door, and its read-only
        # property rests on the account behind it being an `auditor`. Checked here so
        # a misconfiguration is visible at deploy time rather than on someone's first
        # click.
        #
        # Logged, not raised. The request path already fails closed twice — the modes
        # endpoint hides the button unless the account is a live auditor, and
        # app.api.deps refuses writes from one — so a bad reviewer account cannot
        # grant anything. Refusing to boot would take live merchants offline over a
        # broken demo door, which trades a real outage for a risk already covered.
        if settings.reviewer_access_enabled:
            try:
                verify_reviewer_account()
            except RuntimeError as exc:
                log.error("startup.reviewer_access_misconfigured", error=str(exc))
    else:
        # Don't refuse to boot: /health reports 503 and the platform retries. Crashing
        # here turns a transient DB blip into a redeploy loop.
        log.error("startup.db_unavailable", error=error)

    start_scheduler()

    log.info(
        "startup.complete",
        environment=settings.environment,
        process_role=settings.process_role,
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

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(_request: Request, exc: RequestValidationError):
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content={"detail": _scrub_validation_errors(exc.errors())},
        )

    app.include_router(health.router)
    app.include_router(auth.router)
    app.include_router(live_auth.router)
    app.include_router(billing.router)
    app.include_router(controls.router)
    app.include_router(integrations.router)
    app.include_router(operations.router)
    app.include_router(payment_connections.router)
    app.include_router(team.router)
    app.include_router(live_dashboard.router)
    app.include_router(live_invoices.router)
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
