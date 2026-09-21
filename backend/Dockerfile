# The API and the worker ship as one image with two entrypoints (Phase 14.5).
#
# Built from the **repository root** as context (`docker build -f
# backend/Dockerfile .`), which is what `fly deploy` does with a fly.toml at
# the root. Multi-stage: dependencies resolve once against the committed
# lockfile, and the runtime image carries no build tooling. The voice group is deliberately
# left out — faster-whisper and its ONNX runtime add ~2 GB, and a deployment
# uses Deepgram and ElevenLabs. Build with `--build-arg VOICE=1` for an image
# that can run the offline voice stack.

FROM python:3.12-slim AS builder

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

COPY --from=ghcr.io/astral-sh/uv:0.9.29 /uv /usr/local/bin/uv

WORKDIR /app

# Dependencies first, as their own layer: application code changes far more
# often than the lockfile does.
COPY backend/pyproject.toml backend/uv.lock ./
ARG VOICE=0
RUN --mount=type=cache,target=/root/.cache/uv \
    if [ "$VOICE" = "1" ]; then \
        uv sync --locked --no-dev --group voice --no-install-project; \
    else \
        uv sync --locked --no-dev --no-install-project; \
    fi

COPY backend/ .
RUN --mount=type=cache,target=/root/.cache/uv uv sync --locked --no-dev --no-editable


FROM python:3.12-slim AS runtime

# libpq for asyncpg's DSN handling of SCRAM, and CA certificates for every
# outbound provider call.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates libpq5 curl \
    && rm -rf /var/lib/apt/lists/*

# A non-root user: the container never needs to write outside /tmp.
RUN useradd --create-home --uid 10001 clinical
WORKDIR /app

COPY --from=builder --chown=clinical:clinical /app /app

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    ENVIRONMENT=production

USER clinical
EXPOSE 8000

# The platform's own probe should use /ready (it checks Postgres and Redis);
# this one only asks whether the process is alive.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS http://localhost:8000/health || exit 1

# `--timeout-graceful-shutdown` is the drain window: on SIGTERM uvicorn stops
# accepting connections and finishes the requests it has, which for a
# streaming answer can be tens of seconds. The platform must allow at least
# as long before SIGKILL.
CMD ["uvicorn", "app.main:app", \
     "--host", "0.0.0.0", \
     "--port", "8000", \
     "--timeout-graceful-shutdown", "60", \
     "--no-access-log"]
