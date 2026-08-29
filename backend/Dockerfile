# Vasooli backend.
#
# Multi-stage so the runtime image carries the virtualenv but not uv, build tools, or
# the lockfile resolution step — smaller to push and fewer things installed that could
# have a CVE.

FROM python:3.13-slim AS builder

# Pin the build helper by digest; a mutable `latest` tag should not be able to change
# the production toolchain between two builds of the same source revision.
COPY --from=ghcr.io/astral-sh/uv@sha256:95f2aa1fe59274951cfe9b0cbc7972e879ff1004bc8945d130a32eb0dbd85945 /uv /usr/local/bin/uv

WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy

# Dependencies first, in their own layer: application code changes far more often than
# the lockfile, so this layer survives most rebuilds.
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-install-project --no-dev

COPY . .
RUN uv sync --locked --no-dev


FROM python:3.13-slim AS runtime

# Non-root. A container process that does not need to write to its own filesystem
# should not be able to.
RUN useradd --create-home --uid 10001 vasooli

WORKDIR /app
COPY --from=builder --chown=vasooli:vasooli /app /app

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

USER vasooli
EXPOSE 8000

# /live checks the process only; /health also checks the database. The liveness probe
# must not fail because Postgres blipped, or the platform restarts a healthy app.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/live', timeout=4).status == 200 else 1)"

# Migrations run at start, before the server binds. A container that boots against an
# out-of-date schema fails in a far more confusing way than one that refuses to start.
CMD ["sh", "-c", "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
