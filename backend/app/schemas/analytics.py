"""Analytics schemas — usage, cost, and answer-quality reporting."""

from __future__ import annotations

from datetime import date

from pydantic import BaseModel


class UsageOverview(BaseModel):
    days: int
    total_queries: int
    answered: int
    abstained: int
    blocked: int
    # Per-guardrail trigger counts: phi / scope (blocking), red_flag (escalation).
    guardrail_triggers: dict[str, int]
    cache_hits: int
    # Rates are 0..1, rounded to 4dp; None when there is nothing to divide by.
    abstention_rate: float | None
    cache_hit_rate: float | None
    contradiction_rate: float | None
    total_cost_usd: float
    cost_saved_usd: float
    latency_p50_ms: float | None
    latency_p95_ms: float | None


class UsagePoint(BaseModel):
    day: date
    queries: int
    cache_hits: int
    cost_usd: float
    cost_saved_usd: float


class UsageSeries(BaseModel):
    days: int
    points: list[UsagePoint]


class TopQuery(BaseModel):
    query: str
    count: int


class QualityReport(BaseModel):
    days: int
    abstention_rate: float | None
    contradiction_rate: float | None
    by_confidence: dict[str, int]
    by_evidence_grade: dict[str, int]
    feedback_up: int
    feedback_down: int
    feedback_by_reason: dict[str, int]
    top_queries: list[TopQuery]


class CostLine(BaseModel):
    """One (component, provider, model) line of the ledger over the window."""

    component: str  # embedding | rerank | generation | stt | tts
    provider: str  # cohere | anthropic | deepgram | elevenlabs | local
    model: str
    unit: str  # tokens | output_tokens | searches | seconds | characters
    calls: int
    units: float  # billed units (not served from a cache)
    cost_usd: float  # at the published list price; a local provider is $0
    # The same units at the cloud list price the local stand-in replaces;
    # equal to cost_usd for a cloud provider.
    projected_usd: float
    cached_units: float  # units a cache avoided
    cached_saved_usd: float
    cached_projected_usd: float


class CostPoint(BaseModel):
    day: date
    cost_usd: float
    projected_usd: float
    saved_usd: float
    saved_projected_usd: float


class CostReport(BaseModel):
    days: int
    total_cost_usd: float
    total_projected_usd: float
    # What the caches avoided in the window, split by which cache.
    semantic_cache_saved_usd: float
    semantic_cache_saved_projected_usd: float
    embedding_cache_saved_usd: float
    embedding_cache_saved_projected_usd: float
    # "Semantic caching avoided $X this month": the calendar month to date.
    month_semantic_cache_saved_usd: float
    month_semantic_cache_saved_projected_usd: float
    # True when any line in the window ran on a local (offline) provider, so
    # the dashboard labels projected_usd as a projection.
    offline: bool
    price_checked: date
    by_component: dict[str, float]  # actual spend per component
    by_component_projected: dict[str, float]
    lines: list[CostLine]
    series: list[CostPoint]
