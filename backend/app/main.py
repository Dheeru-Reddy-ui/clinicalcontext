"""FastAPI application factory and process entrypoint.

Run locally with::

    uv run uvicorn app.main:app --reload --port 8000

Settings are resolved at import time, so a missing required environment
variable fails the boot immediately (see ``app.config``). Infrastructure
*outages* (Postgres/Redis down) do not crash the process — ``/ready``
reports them as degraded instead, so orchestrators can gate traffic.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress

import asyncpg
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from redis.asyncio import Redis

from app import __version__
from app.api.health import router as health_router
from app.api.public import router as public_router
from app.api.v1 import router as v1_router
from app.config import Settings, get_settings
from app.core.errors import register_exception_handlers
from app.core.headers import SecurityHeadersMiddleware
from app.core.logging import RequestContextMiddleware, get_logger, setup_logging
from app.core.security import JWTVerifier

logger = get_logger("app.main")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    setup_logging(settings.log_level)
    logger.info("startup_begin", environment=settings.environment, version=__version__)

    from app.core.telemetry import configure_sentry, configure_tracing
    from app.graph.tracing import configure_langsmith

    configure_langsmith()
    configure_tracing(settings)
    configure_sentry(settings)

    try:
        pooled = settings.db_uses_transaction_pooler
        app.state.db_pool = await asyncpg.create_pool(
            dsn=settings.database_url,
            min_size=settings.db_pool_min_size,
            max_size=settings.db_pool_max_size,
            timeout=10,
            # Deterministic type resolution for pgvector & friends whether the
            # extension lives in public (compose) or extensions (Supabase).
            server_settings={
                "search_path": "public, extensions",
                # See app/retrieval/dense.py: below the requested candidate
                # count this silently truncates the search.
                "hnsw.ef_search": str(settings.hnsw_ef_search),
            },
            # Behind a transaction-mode pooler, server connections are shared
            # between clients mid-transaction, so asyncpg's prepared-statement
            # cache is unsafe: it would reuse a name the server has since
            # handed to someone else ("prepared statement _pg1 already
            # exists"). Disabling the cache is the documented way to run
            # asyncpg through pgbouncer in transaction mode.
            statement_cache_size=0 if pooled else 100,
            max_cached_statement_lifetime=0 if pooled else 300,
        )
        logger.info(
            "database_pool_ready",
            min_size=settings.db_pool_min_size,
            max_size=settings.db_pool_max_size,
            transaction_pooler=pooled,
        )
    except Exception as exc:
        app.state.db_pool = None
        logger.error("database_pool_failed", error=f"{type(exc).__name__}: {exc}")

    app.state.redis = Redis.from_url(
        settings.redis_url,
        socket_connect_timeout=5,
        socket_timeout=5,
    )

    # Without a worker process (see config: webhook_drain_interval_seconds),
    # the API drains its own webhook queue. Deliveries are queued by requests,
    # so the process is awake whenever there is work.
    drain: asyncio.Task[None] | None = None
    if settings.webhook_drain_interval_seconds > 0 and app.state.db_pool is not None:
        from app.services.webhooks import drain_forever

        drain = asyncio.create_task(
            drain_forever(
                app.state.db_pool, interval_seconds=settings.webhook_drain_interval_seconds
            ),
            name="webhook-drain",
        )
        logger.info("webhook_drain_started", interval_s=settings.webhook_drain_interval_seconds)
    logger.info("startup_complete")

    yield

    if drain is not None:
        drain.cancel()
        with suppress(asyncio.CancelledError):
            await drain
    registry = getattr(app.state, "voice_registry", None)
    if registry is not None:
        await registry.close_all()
    pool = app.state.db_pool
    if isinstance(pool, asyncpg.Pool):
        await pool.close()
    redis_client = app.state.redis
    if isinstance(redis_client, Redis):
        await redis_client.aclose()
    logger.info("shutdown_complete")


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build the application. Tests call this directly with fakes on state."""
    resolved = settings if settings is not None else get_settings()

    app = FastAPI(
        title="ClinicalContext AI",
        description="Evidence-grounded clinical question answering over public medical literature.",
        version=__version__,
        lifespan=lifespan,
    )
    app.state.jwt_verifier = JWTVerifier(resolved)

    # Middleware added last runs first: request context wraps everything.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=resolved.cors_origin_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        # Every response header the browser is expected to read has to be
        # named here: cross-origin JavaScript can otherwise only see the six
        # CORS-safelisted ones. Retry-After was missing, so the UI's "try
        # again in Ns" was silently blank on a 429 (found by the Phase 14 E2E
        # suite, which asserted the text the UI meant to show).
        expose_headers=["X-Request-ID", "Retry-After"],
    )
    app.add_middleware(RequestContextMiddleware)
    # Deployed environments are always behind TLS; local development is not,
    # and an HSTS header on localhost would poison the whole host for a year.
    app.add_middleware(
        SecurityHeadersMiddleware,
        https_only=resolved.environment in ("staging", "production"),
    )

    register_exception_handlers(app)

    from app.core.telemetry import instrument_app

    instrument_app(app)

    app.include_router(health_router)
    app.include_router(v1_router)
    app.include_router(public_router)  # unauthenticated, read-only (see module)
    return app


app = create_app()
