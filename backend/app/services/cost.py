"""The cost ledger (Phase 13): every billable provider call, as it happens.

Providers do not know which tenant or query they are serving; the request
does. So a request (or a voice turn) opens a :func:`collecting` context, the
providers inside it call :func:`record` with what the provider billed —
tokens, searches, audio seconds, characters — and the request flushes the
collected events to ``cost_events`` with its ``org_id`` and ``query_id``. The
context is a contextvar, so concurrent requests never see each other's
events, and a provider called outside any request (an eval, a backfill)
records nothing.

Rates are the published list prices at the date noted. The offline providers
cost a truthful $0; the same units priced at the rate of the cloud provider
they stand in for are the "projected" column the dashboard labels as such.

Two caches write to the ledger as *avoided* units (``cached=True``): the
embedding cache (an embedding event) and the semantic answer cache, which
replays the rerank and generation events of the answer it served — the hit
still embeds the query, so that event is never replayed.
"""

from __future__ import annotations

import contextlib
import contextvars
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Literal, cast
from uuid import UUID

import structlog

from app.core.deps import DbPool

logger = structlog.stdlib.get_logger("app.services.cost")

Component = Literal["embedding", "rerank", "generation", "stt", "tts"]
Unit = Literal["tokens", "output_tokens", "searches", "seconds", "characters"]
COMPONENTS: tuple[Component, ...] = ("embedding", "rerank", "generation", "stt", "tts")

# List prices, USD, as published on 2026-09-20 — the single place to update.
PRICE_CHECKED = date(2026, 9, 20)
LIST_PRICES: dict[tuple[Component, str], tuple[float, Unit]] = {
    ("embedding", "embed-v4.0"): (0.12 / 1_000_000, "tokens"),
    ("rerank", "rerank-v3.5"): (2.0 / 1_000, "searches"),
    ("generation", "claude-sonnet-4-6"): (3.0 / 1_000_000, "tokens"),  # input; output below
    ("stt", "nova-3-medical"): (0.0077 / 60, "seconds"),
    ("tts", "eleven_flash_v2_5"): (0.15 / 1_000, "characters"),
    ("tts", "eleven_multilingual_v2"): (0.30 / 1_000, "characters"),
    ("tts", "aura-2"): (0.030 / 1_000, "characters"),
}
GENERATION_OUTPUT_PRICE: dict[str, float] = {"claude-sonnet-4-6": 15.0 / 1_000_000}
# What the offline stand-ins (provider "local") would cost on the cloud
# provider each one replaces.
CLOUD_EQUIVALENT: dict[Component, str] = {
    "embedding": "embed-v4.0",
    "rerank": "rerank-v3.5",
    "generation": "claude-sonnet-4-6",
    "stt": "nova-3-medical",
    "tts": "eleven_flash_v2_5",
}
LOCAL_PROVIDER = "local"


@dataclass(slots=True)
class CostEvent:
    component: Component
    provider: str
    model: str
    units: float
    unit: Unit
    cost_usd: float
    cached: bool = False
    output_units: float = 0.0  # generation only: output tokens, priced separately

    def as_dict(self) -> dict[str, Any]:
        return {
            "component": self.component,
            "provider": self.provider,
            "model": self.model,
            "units": self.units,
            "unit": self.unit,
            "output_units": self.output_units,
        }


@dataclass(slots=True)
class Collector:
    events: list[CostEvent] = field(default_factory=list)
    org_id: UUID | None = None
    query_id: UUID | None = None
    voice_session_id: UUID | None = None

    def tag(
        self,
        *,
        org_id: UUID | None = None,
        query_id: UUID | None = None,
        voice_session_id: UUID | None = None,
    ) -> None:
        """Name the tenant/query the events belong to (once the request knows)."""
        if org_id is not None:
            self.org_id = org_id
        if query_id is not None:
            self.query_id = query_id
        if voice_session_id is not None:
            self.voice_session_id = voice_session_id

    def snapshot(self) -> list[dict[str, Any]]:
        """The billed (non-cached) rerank/generation events, in a form the
        semantic cache can store and replay as savings on a later hit."""
        return [
            e.as_dict()
            for e in self.events
            if not e.cached and e.component in ("rerank", "generation")
        ]


_collector: contextvars.ContextVar[Collector | None] = contextvars.ContextVar(
    "cost_collector", default=None
)


@contextlib.contextmanager
def using(collector: Collector) -> Iterator[Collector]:
    """Make ``collector`` the active one for the current task.

    Tasks started inside inherit the same collector (a contextvar copy points
    at the same object), so the graph's node tasks and a voice turn's speaker
    task all land in the turn that spawned them.
    """
    token = _collector.set(collector)
    try:
        yield collector
    finally:
        # An async generator closed from another context cannot reset its
        # token; the collector is simply left to be garbage-collected.
        with contextlib.suppress(ValueError):
            _collector.reset(token)


@contextlib.contextmanager
def collecting() -> Iterator[Collector]:
    """Open a fresh collector for the current task; providers record into it."""
    with using(Collector()) as collector:
        yield collector


def current() -> Collector | None:
    return _collector.get()


def list_price(component: Component, model: str) -> tuple[float, Unit] | None:
    # Aura-2 is priced per model family; the model id names the voice.
    if component == "tts" and model.startswith("aura-2"):
        model = "aura-2"
    return LIST_PRICES.get((component, model))


def priced(component: Component, model: str, units: float, *, output_units: float = 0.0) -> float:
    """The cost at the published rate — $0 for a model with no price (the
    offline stand-ins), so an unknown model never invents a cost."""
    price = list_price(component, model)
    if price is None:
        return 0.0
    total = units * price[0]
    if component == "generation":
        total += output_units * GENERATION_OUTPUT_PRICE.get(model, 0.0)
    return round(total, 6)


def unit_price(component: Component, provider: str, model: str, unit: str) -> float:
    """The list price per unit for one ledger row, projecting a local
    provider onto its cloud equivalent. Zero when nothing is published."""
    target = CLOUD_EQUIVALENT.get(component, model) if provider == LOCAL_PROVIDER else model
    if unit == "output_tokens":
        return GENERATION_OUTPUT_PRICE.get(target, 0.0)
    price = list_price(component, target)
    return price[0] if price is not None else 0.0


def record(
    component: Component,
    *,
    provider: str,
    model: str,
    units: float,
    unit: Unit,
    output_units: float = 0.0,
    cached: bool = False,
) -> CostEvent | None:
    """Record a provider call into the active collector (no-op outside one)."""
    collector = _collector.get()
    if collector is None:
        return None
    event = CostEvent(
        component=component,
        provider=provider,
        model=model,
        units=float(units),
        unit=unit,
        cost_usd=priced(component, model, units, output_units=output_units),
        cached=cached,
        output_units=float(output_units),
    )
    collector.events.append(event)
    return event


def replay_cached(snapshot: list[dict[str, Any]]) -> int:
    """Re-record a stored snapshot as avoided units (a semantic-cache hit)."""
    count = 0
    for item in snapshot:
        component = item.get("component")
        if component not in COMPONENTS:
            continue
        record(
            cast("Component", component),
            provider=str(item.get("provider", "")),
            model=str(item.get("model", "")),
            units=float(item.get("units", 0.0)),
            unit=cast("Unit", item.get("unit", "tokens")),
            output_units=float(item.get("output_units", 0.0)),
            cached=True,
        )
        count += 1
    return count


def projected_usd(event: CostEvent) -> float:
    """What the event would cost on the cloud provider it stands in for."""
    if event.provider != LOCAL_PROVIDER:
        return event.cost_usd
    equivalent = CLOUD_EQUIVALENT.get(event.component)
    if equivalent is None:
        return 0.0
    return priced(event.component, equivalent, event.units, output_units=event.output_units)


def _rows(collector: Collector) -> list[tuple[Any, ...]]:
    """Ledger rows: generation output tokens are their own row so the
    projection can price them at the output rate."""
    rows: list[tuple[Any, ...]] = []
    for e in collector.events:
        input_cost = priced(e.component, e.model, e.units)
        rows.append((e.component, e.provider, e.model, e.units, e.unit, input_cost, e.cached))
        if e.component == "generation" and e.output_units:
            rows.append(
                (
                    e.component,
                    e.provider,
                    e.model,
                    e.output_units,
                    "output_tokens",
                    round(e.cost_usd - input_cost, 6),
                    e.cached,
                )
            )
    return rows


async def flush(
    pool: DbPool,
    collector: Collector,
    *,
    org_id: UUID | None = None,
    query_id: UUID | None = None,
    voice_session_id: UUID | None = None,
) -> int:
    """Write the collected events for a request. Never raises — the answer
    was already produced; a ledger gap is logged, not surfaced."""
    org = org_id or collector.org_id
    if not collector.events or org is None:
        return 0
    query = query_id or collector.query_id
    voice = voice_session_id or collector.voice_session_id
    rows = [(org, query, voice, *row) for row in _rows(collector)]
    try:
        async with pool.acquire() as conn:
            await conn.executemany(
                """
                INSERT INTO public.cost_events
                    (org_id, query_id, voice_session_id, component, provider, model,
                     units, unit, cost_usd, cached)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
                """,
                rows,
            )
    except Exception as exc:
        logger.warning("cost_ledger_write_failed", error=type(exc).__name__, events=len(rows))
        return 0
    collector.events.clear()
    return len(rows)


def summary(collector: Collector) -> dict[str, Any]:
    billed = [e for e in collector.events if not e.cached]
    avoided = [e for e in collector.events if e.cached]
    return {
        "events": len(collector.events),
        "cost_usd": round(sum(e.cost_usd for e in billed), 6),
        "projected_usd": round(sum(projected_usd(e) for e in billed), 6),
        "saved_usd": round(sum(e.cost_usd for e in avoided), 6),
        "saved_projected_usd": round(sum(projected_usd(e) for e in avoided), 6),
    }


__all__ = [
    "CLOUD_EQUIVALENT",
    "COMPONENTS",
    "LIST_PRICES",
    "LOCAL_PROVIDER",
    "PRICE_CHECKED",
    "Collector",
    "Component",
    "CostEvent",
    "collecting",
    "current",
    "flush",
    "priced",
    "projected_usd",
    "record",
    "replay_cached",
    "summary",
    "unit_price",
    "using",
]
