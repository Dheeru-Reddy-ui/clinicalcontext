"""Structured JSON logging with request-scoped context.

Every log line is a single JSON object on stdout. ``RequestContextMiddleware``
binds a ``request_id`` (and, once auth lands, a ``tenant_id``) into a
contextvar, so every log line emitted anywhere in the request path carries
them automatically — services and repositories never pass IDs around by hand.
"""

from __future__ import annotations

import logging
import sys
import time
import uuid

import structlog
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response
from structlog.typing import Processor


def setup_logging(log_level: str = "INFO") -> None:
    """Configure structlog + stdlib logging to emit JSON lines on stdout.

    Uvicorn's own loggers are re-routed through the same pipeline so the
    process has exactly one log format.
    """
    shared_processors: list[Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
    ]

    structlog.configure(
        processors=[
            *shared_processors,
            structlog.processors.StackInfoRenderer(),
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=shared_processors,
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(),
        ],
    )

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)

    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(log_level.upper())

    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        uvicorn_logger = logging.getLogger(name)
        uvicorn_logger.handlers = []
        uvicorn_logger.propagate = True


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    """Return a named structlog logger."""
    return structlog.stdlib.get_logger(name)


class RequestContextMiddleware(BaseHTTPMiddleware):
    """Bind a request ID to the log context and stamp it on the response.

    Honors an inbound ``X-Request-ID`` header (so IDs propagate across
    services); generates one otherwise. ``tenant_id`` is bound here too once
    the auth layer (Phase 2) attaches it to ``request.state``.
    """

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        request_id = request.headers.get("x-request-id") or uuid.uuid4().hex
        request.state.request_id = request_id

        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(request_id=request_id)

        logger = get_logger("app.request")
        start = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            logger.exception(
                "http_request_failed",
                method=request.method,
                path=request.url.path,
            )
            raise
        duration_ms = round((time.perf_counter() - start) * 1000, 2)

        response.headers["X-Request-ID"] = request_id
        logger.info(
            "http_request",
            method=request.method,
            path=request.url.path,
            status_code=response.status_code,
            duration_ms=duration_ms,
        )
        return response
