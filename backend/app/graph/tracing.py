"""LangSmith tracing setup.

LangGraph/LangChain emit traces to LangSmith when the standard env vars are
set. We enable them at startup only when a real key is present (a placeholder
value leaves tracing off), so every graph run is traced in a keyed
environment and is a silent no-op otherwise. Per-run tags (tenant_id,
query_id, prompt versions) are attached in AgentGraph.run via the run config.
"""

from __future__ import annotations

import os
from typing import Any

import structlog

from app.config import get_settings

logger = structlog.stdlib.get_logger("app.graph.tracing")

_PLACEHOLDER_MARKERS = ("placeholder", "your-", "changeme", "test-")
_ENABLED = False


def langsmith_enabled() -> bool:
    return _ENABLED


def configure_langsmith() -> bool:
    """Enable LangSmith tracing if a real key is configured. Returns whether on."""
    settings = get_settings()
    key = settings.langsmith_api_key.get_secret_value()
    if not key or any(marker in key.lower() for marker in _PLACEHOLDER_MARKERS):
        logger.info("langsmith_disabled", reason="no real LANGSMITH_API_KEY")
        return False
    os.environ["LANGCHAIN_TRACING_V2"] = "true"
    os.environ["LANGSMITH_TRACING"] = "true"
    os.environ["LANGCHAIN_API_KEY"] = key
    os.environ["LANGSMITH_API_KEY"] = key
    os.environ["LANGCHAIN_PROJECT"] = settings.langsmith_project
    global _ENABLED
    _ENABLED = True
    logger.info("langsmith_enabled", project=settings.langsmith_project)
    return True


def run_metadata(tags: dict[str, str] | None) -> dict[str, Any]:
    """What every graph run carries in LangSmith besides its tags: the prompt
    versions it ran with and the release, so a score can be read against the
    exact prompts that produced the answer."""
    from app.graph.reasoner import LLM_PROMPTS

    settings = get_settings()
    return {
        **(tags or {}),
        "release": settings.release,
        "ai_backend": settings.ai_backend,
        "prompt_versions": {name: f"{name}.v{v}" for name, v in LLM_PROMPTS.items()},
    }


async def attach_scores(
    run_id: str | None, scores: dict[str, float | bool | None], *, comment: str | None = None
) -> int:
    """Attach evaluation scores (or a thumbs verdict) to a traced run as
    LangSmith feedback: one feedback per key. Returns how many were sent;
    silently zero when tracing is off or the run id is unknown."""
    if not _ENABLED or not run_id:
        return 0
    from langsmith import Client

    client = Client()
    sent = 0
    for key, value in scores.items():
        if value is None:
            continue
        try:
            client.create_feedback(run_id, key=key, score=float(value), comment=comment)
            sent += 1
        except Exception as exc:  # feedback is best-effort; never fail the request
            logger.warning("langsmith_feedback_failed", key=key, error=type(exc).__name__)
    return sent
