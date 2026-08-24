"""Request-level protections. P1 security hardening.

Three things that are cheap to add and awkward to add later:

* a body-size cap, so an oversized payload is rejected before anything parses it
* rate limiting, so a single shared password is not brute-forceable
* security headers, so a browser does not have to guess how to treat a response

All in-process. Rate limiting keyed in memory is not accurate across multiple workers,
and that is a deliberate trade: this is one small deployment, and Redis for a login
counter would be more infrastructure to run, secure, and explain than the risk
justifies. The limit is documented as approximate rather than pretended to be exact.
"""

import time
from collections import defaultdict, deque
from threading import Lock

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from app.core.config import settings
from app.core.logging import get_logger

log = get_logger("security")

#: Reject anything larger before reading it. The largest legitimate request is a batch
#: ingest of a few hundred invoices; 2 MB is far above that and far below anything that
#: would trouble the process.
MAX_BODY_BYTES = 2 * 1024 * 1024

#: Per-path-group limits, as (requests, seconds).
#:
#: Login is far tighter than everything else: it is the one endpoint where guessing is
#: the attack, and a human typing a password needs a handful of attempts, not hundreds.
RATE_LIMITS: dict[str, tuple[int, int]] = {
    "/api/auth/login": (10, 60),
    "/api/webhooks/": (300, 60),  # Razorpay can burst; do not throttle real payments
    "default": (240, 60),
}


class BodySizeLimitMiddleware:
    """Refuse oversized requests, whether or not they declare their size honestly.

    Two checks, because Content-Length alone is not enough: a chunked request carries
    no Content-Length at all, and a dishonest client can simply understate it. The
    header check is the cheap path that rejects most oversized requests without reading
    a byte; the stream check is the one that actually holds.

    Written as raw ASGI rather than BaseHTTPMiddleware on purpose. BaseHTTPMiddleware
    buffers the request body in order to re-serve it downstream — which would mean
    holding the very payload we are refusing, and would break raw-body HMAC
    verification, since the webhook handler must see the exact bytes Razorpay sent.
    Wrapping `receive` leaves the body a stream and counts it as it flows past.
    """

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope["headers"]}
        declared = headers.get("content-length")
        if declared and declared.isdigit() and int(declared) > MAX_BODY_BYTES:
            await _send_413(send, scope, size=int(declared), reason="declared")
            return

        state = {"received": 0, "too_large": False, "response_started": False}

        async def counting_receive():
            message = await receive()
            if message["type"] == "http.request":
                state["received"] += len(message.get("body", b""))
                if state["received"] > MAX_BODY_BYTES:
                    state["too_large"] = True
                    # Cut the body off here. The handler sees the stream end; the
                    # guarded send below turns the outcome into a clean 413.
                    return {"type": "http.disconnect"}
            return message

        async def guarded_send(message):
            # If the body overran, replace whatever the handler was about to say with
            # a 413 — but only if nothing has been sent yet. Once the response has
            # started, the status line is already on the wire and rewriting it would
            # corrupt the response.
            if state["too_large"] and not state["response_started"]:
                state["response_started"] = True
                await _send_413(send, scope, size=state["received"], reason="streamed")
                return
            if message["type"] == "http.response.start":
                state["response_started"] = True
            if state["too_large"] and state["response_started"]:
                # Body frames belonging to the replaced response are dropped.
                return
            await send(message)

        await self.app(scope, counting_receive, guarded_send)


async def _send_413(send, scope, *, size: int, reason: str) -> None:
    log.warning("security.body_too_large", path=scope.get("path"), bytes=size, detected=reason)
    body = b'{"detail":"Request body too large"}'
    await send(
        {
            "type": "http.response.start",
            "status": 413,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode()),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Approximate per-client rate limiting, in memory.

    A sliding window of request timestamps per (client, path group). Old entries are
    discarded as they age out, so memory stays bounded by the limit itself rather than
    growing with traffic.
    """

    def __init__(self, app) -> None:
        super().__init__(app)
        self._hits: dict[tuple[str, str], deque[float]] = defaultdict(deque)
        self._lock = Lock()

    @staticmethod
    def _group(path: str) -> str:
        for prefix in RATE_LIMITS:
            if prefix != "default" and path.startswith(prefix):
                return prefix
        return "default"

    @staticmethod
    def _client(request: Request) -> str:
        """Best-effort client identity.

        Railway and Vercel both put the real address in X-Forwarded-For. It is
        spoofable, so this is a speed bump against casual abuse rather than a security
        boundary — the boundary is the signed session and the admin key.
        """
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            return forwarded.split(",")[0].strip()
        return request.client.host if request.client else "unknown"

    async def dispatch(self, request: Request, call_next):
        group = self._group(request.url.path)
        limit, window = RATE_LIMITS[group]
        key = (self._client(request), group)
        now = time.monotonic()

        with self._lock:
            hits = self._hits[key]
            while hits and now - hits[0] > window:
                hits.popleft()
            if len(hits) >= limit:
                retry_after = int(window - (now - hits[0])) + 1
                log.warning("security.rate_limited", path=request.url.path, group=group)
                return JSONResponse(
                    {"detail": "Too many requests"},
                    status_code=429,
                    headers={"Retry-After": str(retry_after)},
                )
            hits.append(now)

        return await call_next(request)


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Headers a browser should not have to guess about."""

    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        # This API serves JSON to a separate frontend, so it never needs to load
        # scripts, frames, or styles of its own.
        response.headers.setdefault(
            "Content-Security-Policy", "default-src 'none'; frame-ancestors 'none'"
        )
        if settings.is_production:
            response.headers.setdefault(
                "Strict-Transport-Security", "max-age=31536000; includeSubDomains"
            )
        return response
