"""Phase 13: one query, one trace — from the request the browser started,
through the graph nodes and retrieval stages, with ``tenant_id``,
``query_id`` and ``request_id`` on every span; Sentry events carry the same
ids and the release."""

from __future__ import annotations

from typing import Any

import structlog
from opentelemetry import trace
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from app.config import get_settings
from app.core.telemetry import bind_context, configure_tracing, sentry_before_send, span
from tests.conftest import ApiEnv, parse_sse

# The tracer provider is process-global: install it once with an in-memory
# exporter before any span is created, and every test reads the same buffer.
_EXPORTER = InMemorySpanExporter()
configure_tracing(get_settings(), exporter=_EXPORTER)

GROUNDED_QUERY = "How is type 2 diabetes managed with metformin?"


def _finished() -> list[Any]:
    trace.get_tracer_provider().force_flush()  # type: ignore[attr-defined]
    return list(_EXPORTER.get_finished_spans())


async def test_a_query_is_one_trace_from_the_browser_to_retrieval(env: ApiEnv) -> None:
    _org_id, _user_id, token = await env.new_org_with_owner()
    _EXPORTER.clear()
    # The browser starts the trace: a W3C traceparent on the request.
    trace_id = "4bf92f3577b34da6a3ce929d0e0e4736"
    response = await env.client.post(
        "/api/v1/queries",
        json={"query": GROUNDED_QUERY},
        headers={**env.auth(token), "traceparent": f"00-{trace_id}-00f067aa0ba902b7-01"},
    )
    assert response.status_code == 200, response.text
    events = parse_sse(response.text)
    query_id = events[0]["data"]["query_id"]

    spans = [s for s in _finished() if f"{s.context.trace_id:032x}" == trace_id]
    names = {s.name for s in spans}
    assert names >= {
        "graph.classify_query",
        "graph.retrieve",
        "graph.grade_retrieval",
        "graph.generate",
        "graph.assess_confidence",
        "retrieval.embed_query",
        "retrieval.dense",
        "retrieval.lexical",
        "retrieval.rerank",
    }, names
    server = [s for s in spans if s.kind.name == "SERVER"]
    assert server and server[0].parent is not None, "continues the browser's trace"
    assert f"{server[0].parent.span_id:016x}" == "00f067aa0ba902b7"
    # The graph nodes descend from the server span (through the endpoint's
    # spans), and the retrieval stages from the retrieve node.
    by_id = {s.context.span_id: s for s in spans}

    def ancestors(s: Any) -> list[str]:
        out: list[str] = []
        parent = s.parent
        while parent is not None and parent.span_id in by_id:
            out.append(by_id[parent.span_id].name)
            parent = by_id[parent.span_id].parent
        return out

    retrieve = next(s for s in spans if s.name == "graph.retrieve")
    assert server[0].name in ancestors(retrieve)
    rerank = next(s for s in spans if s.name == "retrieval.rerank")
    assert "graph.retrieve" in ancestors(rerank)
    # Every span carries the ids a log line carries.
    for s in spans:
        if s.name.startswith(("graph.", "retrieval.")):
            assert s.attributes is not None
            assert s.attributes.get("query_id") == query_id, s.name
            assert s.attributes.get("tenant_id") == str(_org_id), s.name
            assert s.attributes.get("request_id"), s.name
            assert s.attributes.get("channel") == "text"
    assert server[0].attributes is not None
    assert server[0].attributes.get("tenant_id") == str(_org_id), "bound after auth, still stamped"
    # Provider-shaped attributes on the stage spans.
    assert rerank.attributes is not None and rerank.attributes.get("reranker") == "bm25-local"
    assert rerank.attributes.get("candidates", 0) > 0


async def test_span_helper_records_attributes_and_errors() -> None:
    _EXPORTER.clear()
    structlog.contextvars.clear_contextvars()
    bind_context(request_id="req-1", tenant_id="org-1", query_id=None)
    with span("outer", note="x"):
        try:
            with span("inner", count=3):
                raise ValueError("boom")
        except ValueError:
            pass
    inner = next(s for s in _finished() if s.name == "inner")
    assert inner.status.status_code.name == "ERROR"
    assert inner.attributes is not None and inner.attributes["count"] == 3
    assert inner.attributes["request_id"] == "req-1" and inner.attributes["tenant_id"] == "org-1"
    assert "query_id" not in inner.attributes
    assert any(e.name == "exception" for e in inner.events)
    structlog.contextvars.clear_contextvars()


def test_sentry_events_carry_the_request_ids_and_trace() -> None:
    structlog.contextvars.clear_contextvars()
    bind_context(request_id="req-9", tenant_id="org-9", query_id="q-9")
    with span("with-error"):
        event = sentry_before_send({"tags": {"existing": "kept"}}, {})
        assert event["tags"]["request_id"] == "req-9"
        assert event["tags"]["tenant_id"] == "org-9" and event["tags"]["query_id"] == "q-9"
        assert event["tags"]["existing"] == "kept"
        assert len(event["tags"]["trace_id"]) == 32
    structlog.contextvars.clear_contextvars()
    assert get_settings().release, "the release is always set (dev locally, the SHA in deploys)"


async def test_langsmith_metadata_and_feedback_are_inert_without_a_key(monkeypatch: Any) -> None:
    """Every graph run carries prompt versions and the release in its run
    metadata; scores attach as feedback only when tracing is really on."""
    from app.graph import tracing
    from app.graph.reasoner import LLM_PROMPTS

    meta = tracing.run_metadata({"tenant_id": "t", "query_id": "q"})
    assert meta["tenant_id"] == "t" and meta["release"] == get_settings().release
    assert (
        meta["prompt_versions"]["generate_answer"]
        == f"generate_answer.v{LLM_PROMPTS['generate_answer']}"
    )
    assert not tracing.langsmith_enabled()
    assert await tracing.attach_scores("run-1", {"thumbs_up": True}) == 0

    # With tracing on, one feedback per non-null score goes to the client.
    sent: list[tuple[str, str, float, str | None]] = []

    class FakeClient:
        def create_feedback(
            self, run_id: str, *, key: str, score: float, comment: str | None
        ) -> None:
            sent.append((run_id, key, score, comment))

    import langsmith

    monkeypatch.setattr(langsmith, "Client", FakeClient)
    monkeypatch.setattr(tracing, "_ENABLED", True)
    scores: dict[str, float | bool | None] = {
        "golden.cited_gold": True,
        "golden.recall_at_10": 0.5,
        "skip": None,
    }
    assert await tracing.attach_scores("run-1", scores, comment="therapy-001") == 2
    assert sent == [
        ("run-1", "golden.cited_gold", 1.0, "therapy-001"),
        ("run-1", "golden.recall_at_10", 0.5, "therapy-001"),
    ]
