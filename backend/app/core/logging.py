"""structlog configuration.

Every log line for one invoice must be greppable by invoice_id, and every line within
one request by request_id. Console renderer locally, JSON in deployed environments.
"""

import logging
import sys
import uuid
from collections.abc import Awaitable, Callable

import structlog
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from app.core.config import settings


def configure_logging() -> None:
    processors: list[structlog.types.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]
    processors.append(
        structlog.processors.JSONRenderer()
        if settings.log_json
        else structlog.dev.ConsoleRenderer(colors=sys.stderr.isatty())
    )

    structlog.configure(
        processors=processors,
        wrapper_class=structlog.make_filtering_bound_logger(
            logging.getLevelNamesMapping()[settings.log_level.upper()]
        ),
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )
    logging.basicConfig(format="%(message)s", stream=sys.stdout, level=settings.log_level)
    # uvicorn's own access log duplicates our request middleware.
    logging.getLogger("uvicorn.access").disabled = True
    # google-genai warns about automatic function calling on every structured call.
    # We do not use function calling; the warning is noise that buries real failover
    # messages in the demo logs.
    logging.getLogger("google_genai.models").setLevel(logging.ERROR)


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    return structlog.get_logger(name)


class RequestContextMiddleware(BaseHTTPMiddleware):
    """Bind a request_id to the log context for the lifetime of the request."""

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        request_id = request.headers.get("X-Request-ID") or uuid.uuid4().hex[:16]
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(
            request_id=request_id,
            method=request.method,
            path=request.url.path,
        )
        log = get_logger("http")
        try:
            response = await call_next(request)
        except Exception:
            log.exception("request.failed")
            raise
        # Health checks are polled constantly; don't drown the log in them.
        if request.url.path != "/health":
            log.info("request.completed", status_code=response.status_code)
        response.headers["X-Request-ID"] = request_id
        return response
