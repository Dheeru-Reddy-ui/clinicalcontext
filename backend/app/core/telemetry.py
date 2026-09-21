"""OpenTelemetry tracing and Sentry error reporting (Phase 13).

One trace per request, from the browser's click to the provider calls:

* The frontend starts the trace (W3C ``traceparent`` on every fetch, or the
  browser OTel SDK when an exporter is configured) and FastAPI instrumentation
  continues it. The graph nodes, the retrieval stages and every provider
  call open child spans through :func:`span`.
* Every span carries ``tenant_id``, ``query_id`` and ``request_id``. They are
  not passed by hand: :class:`ContextAttributes` copies whatever the request
  has bound into structlog's contextvars onto each span as it starts — the
  same values the log lines carry, so a log line and a span can always be
  matched.
* Exporting is configuration: an OTLP/HTTP endpoint (a collector, Jaeger,
  a vendor), ``console`` for a terminal, or nothing. The spans exist either
  way, so tests can assert on them with an in-memory exporter.

Sentry follows the same shape: initialised only with a DSN, with the release
on every event and the request id / tenant id as tags via ``before_send``.
"""

from __future__ import annotations

import contextlib
from collections.abc import Iterator
from typing import Any

import structlog
from opentelemetry import context as otel_context
from opentelemetry import trace
from opentelemetry.propagate import set_global_textmap
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import Span as SdkSpan
from opentelemetry.sdk.trace import SpanProcessor, TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter, SpanExporter
from opentelemetry.trace import Span, Status, StatusCode
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator

from app.config import Settings

logger = structlog.stdlib.get_logger("app.core.telemetry")

CONTEXT_KEYS = ("request_id", "tenant_id", "query_id", "user_id", "channel")
_tracer = trace.get_tracer("clinicalcontext")


class ContextAttributes(SpanProcessor):
    """Stamp the request's bound context onto every span as it starts."""

    def on_start(self, span: SdkSpan, parent_context: otel_context.Context | None = None) -> None:
        bound = structlog.contextvars.get_contextvars()
        for key in CONTEXT_KEYS:
            value = bound.get(key)
            if value is not None:
                span.set_attribute(key, str(value))

    def on_end(self, span: Any) -> None:
        return None

    def shutdown(self) -> None:
        return None

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        return True


def _exporter(settings: Settings) -> SpanExporter | None:
    endpoint = settings.otel_exporter_otlp_endpoint.strip()
    if not endpoint:
        return None
    if endpoint == "console":
        return ConsoleSpanExporter()
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

    return OTLPSpanExporter(endpoint=endpoint.rstrip("/") + "/v1/traces")


def configure_tracing(
    settings: Settings, *, exporter: SpanExporter | None = None
) -> TracerProvider:
    """Install the tracer provider (idempotent per process)."""
    current = trace.get_tracer_provider()
    if isinstance(current, TracerProvider):
        return current
    resource = Resource.create(
        {
            "service.name": settings.otel_service_name,
            "service.version": settings.release,
            "deployment.environment": settings.environment,
        }
    )
    provider = TracerProvider(resource=resource)
    provider.add_span_processor(ContextAttributes())
    chosen = exporter if exporter is not None else _exporter(settings)
    if chosen is not None:
        provider.add_span_processor(BatchSpanProcessor(chosen))
    trace.set_tracer_provider(provider)
    set_global_textmap(TraceContextTextMapPropagator())
    logger.info(
        "tracing_configured",
        exporter=type(chosen).__name__ if chosen else "none",
        service=settings.otel_service_name,
        release=settings.release,
    )
    return provider


def instrument_app(app: Any, pool_module: bool = True) -> None:
    """FastAPI, asyncpg and httpx instrumentation — the server, the database
    and the provider HTTP calls each become spans in the request's trace."""
    from opentelemetry.instrumentation.asyncpg import AsyncPGInstrumentor
    from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
    from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor

    FastAPIInstrumentor.instrument_app(app, excluded_urls="health,ready")
    if pool_module:
        with contextlib.suppress(Exception):  # already instrumented in this process
            AsyncPGInstrumentor().instrument()  # type: ignore[no-untyped-call]
        with contextlib.suppress(Exception):
            HTTPXClientInstrumentor().instrument()


@contextlib.contextmanager
def span(name: str, **attributes: Any) -> Iterator[Span]:
    """A child span of whatever is current, with attributes set up front and
    the exception (if any) recorded — the one helper the graph, the retrieval
    stages and the providers use."""
    with _tracer.start_as_current_span(name) as current:
        for key, value in attributes.items():
            if value is not None:
                current.set_attribute(
                    key, value if isinstance(value, str | int | float | bool) else str(value)
                )
        try:
            yield current
        except Exception as exc:
            current.record_exception(exc)
            current.set_status(Status(StatusCode.ERROR, type(exc).__name__))
            raise


def bind_context(**values: Any) -> None:
    """Bind request context for both logs and traces: structlog's contextvars
    (which every later span copies at start) and the span that is already
    open — the server span exists before auth knows the tenant."""
    present = {k: v for k, v in values.items() if v is not None}
    if not present:
        return
    structlog.contextvars.bind_contextvars(**present)
    current = trace.get_current_span()
    if current.get_span_context().is_valid:
        for key, value in present.items():
            current.set_attribute(key, str(value))


def current_trace_id() -> str | None:
    ctx = trace.get_current_span().get_span_context()
    return f"{ctx.trace_id:032x}" if ctx.is_valid else None


# -- Sentry -----------------------------------------------------------------------


def configure_sentry(settings: Settings) -> bool:
    dsn = settings.sentry_dsn.strip()
    if not dsn:
        logger.info("sentry_disabled", reason="no SENTRY_DSN")
        return False
    import sentry_sdk
    from sentry_sdk.integrations.fastapi import FastApiIntegration
    from sentry_sdk.integrations.starlette import StarletteIntegration

    sentry_sdk.init(
        dsn=dsn,
        release=settings.release,
        environment=settings.environment,
        traces_sample_rate=settings.sentry_traces_sample_rate,
        send_default_pii=False,
        integrations=[StarletteIntegration(), FastApiIntegration()],
        before_send=sentry_before_send,
    )
    logger.info("sentry_enabled", release=settings.release)
    return True


def sentry_before_send(event: Any, hint: dict[str, Any]) -> Any:
    """Every event carries the request id and tenant id as tags, and the
    trace id so an error can be followed into its trace."""
    tags = dict(event.get("tags") or {})
    bound = structlog.contextvars.get_contextvars()
    for key in ("request_id", "tenant_id", "query_id"):
        if bound.get(key) is not None:
            tags[key] = str(bound[key])
    trace_id = current_trace_id()
    if trace_id:
        tags["trace_id"] = trace_id
    event["tags"] = tags
    return event


__all__ = [
    "CONTEXT_KEYS",
    "ContextAttributes",
    "bind_context",
    "configure_sentry",
    "configure_tracing",
    "current_trace_id",
    "instrument_app",
    "sentry_before_send",
    "span",
]
