"""Speculative retrieval on partial transcripts (11D.1).

While the user is still talking, a partial that is (a) long enough, (b) stable
for a moment and (c) judged complete is embedded and retrieved *speculatively*.
On turn commit the final transcript is compared with the speculated one: close
enough → the chunks are already in hand (retrieval cost ≈ 0 added latency);
diverged → the speculation is discarded and retrieval re-runs. Both outcomes
are counted (hit rate, wasted-cost rate) so the trigger thresholds are tuned
against data, not taste.
"""

from __future__ import annotations

import asyncio
import re
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

import structlog

from app.services import cost
from app.voice.endpointing import CompletenessVerdict

logger = structlog.stdlib.get_logger("app.voice.speculation")

RetrieveFn = Callable[[str], Awaitable[Any]]

_WORD = re.compile(r"[a-z0-9]+")


def normalize(text: str) -> str:
    return " ".join(_WORD.findall(text.lower()))


def similarity(a: str, b: str) -> float:
    """0..1: Jaccard similarity of the token sets. Order-insensitive, but a
    missing content word ("… managed with" vs "… managed with metformin")
    costs a full token — a prefix is not the question."""
    ta, tb = set(normalize(a).split()), set(normalize(b).split())
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


@dataclass(slots=True)
class SpeculationOutcome:
    fired: bool
    hit: bool | None
    similarity: float | None
    wasted: bool
    result: Any | None
    retrieval_ms: float | None
    speculated_text: str | None
    # The provider calls the speculation made — real spend whether it was
    # used or wasted — for the turn that resolves it to put in its ledger.
    costs: list[cost.CostEvent] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "fired": self.fired,
            "hit": self.hit,
            "similarity": None if self.similarity is None else round(self.similarity, 3),
            "wasted": self.wasted,
            "retrieval_ms": self.retrieval_ms,
        }


@dataclass(slots=True)
class _InFlight:
    text: str
    task: asyncio.Task[Any]
    started_ms: float
    costs: cost.Collector


class SpeculativeRetriever:
    def __init__(
        self,
        retrieve: RetrieveFn,
        *,
        min_words: int = 6,
        stable_ms: int = 300,
        similarity_threshold: float = 0.85,
    ) -> None:
        self._retrieve = retrieve
        self._min_words = min_words
        self._stable_ms = stable_ms
        self._threshold = similarity_threshold
        self._inflight: _InFlight | None = None
        self._last_partial = ""
        self._last_change_ms = time.monotonic() * 1000
        self.fired_count = 0
        self.hit_count = 0
        self.wasted_count = 0

    # -- partials ------------------------------------------------------------------

    def observe_partial(self, text: str) -> float:
        """Track partial stability. Returns how long the text has been stable."""
        now = time.monotonic() * 1000
        if normalize(text) != normalize(self._last_partial):
            self._last_partial = text
            self._last_change_ms = now
        return now - self._last_change_ms

    def should_fire(self, text: str, stable_ms: float, completeness: CompletenessVerdict) -> bool:
        if len(normalize(text).split()) < self._min_words:
            return False
        if stable_ms < self._stable_ms or not completeness.complete:
            return False
        # One speculation per turn: replacing it on every re-decoded partial
        # is pure waste (a re-decode is not new information).
        return self._inflight is None

    def fire(self, text: str) -> None:
        """Start (or replace) the speculative retrieval for ``text``."""
        if self._inflight is not None and not self._inflight.task.done():
            self._inflight.task.cancel()
            self.wasted_count += 1
            logger.info("speculation_replaced", previous=self._inflight.text[:80])
        started = time.monotonic() * 1000
        collector = cost.Collector()

        async def run() -> Any:
            with cost.using(collector):
                return await self._retrieve(text)

        task = asyncio.create_task(run(), name="voice-speculation")
        self._inflight = _InFlight(text, task, started, collector)
        self.fired_count += 1
        logger.info("speculation_fired", text=text[:120])

    # -- commit --------------------------------------------------------------------

    async def resolve(self, final_text: str) -> SpeculationOutcome:
        inflight = self._inflight
        self._inflight = None
        self._last_partial = ""
        if inflight is None:
            return SpeculationOutcome(False, None, None, False, None, None, None)
        score = similarity(inflight.text, final_text)
        spent = inflight.costs.events
        if score < self._threshold:
            inflight.task.cancel()
            self.wasted_count += 1
            logger.info("speculation_miss", similarity=round(score, 3))
            return SpeculationOutcome(True, False, score, True, None, None, inflight.text, spent)
        try:
            result = await inflight.task
        except asyncio.CancelledError:
            return SpeculationOutcome(True, False, score, True, None, None, inflight.text, spent)
        except Exception as exc:
            logger.warning("speculation_failed", error=f"{type(exc).__name__}: {exc}")
            self.wasted_count += 1
            return SpeculationOutcome(True, False, score, True, None, None, inflight.text, spent)
        self.hit_count += 1
        elapsed = round(time.monotonic() * 1000 - inflight.started_ms, 1)
        logger.info("speculation_hit", similarity=round(score, 3), retrieval_ms=elapsed)
        return SpeculationOutcome(True, True, score, False, result, elapsed, inflight.text, spent)

    def cancel(self) -> None:
        if self._inflight is not None and not self._inflight.task.done():
            self._inflight.task.cancel()
            self.wasted_count += 1
        self._inflight = None
        self._last_partial = ""

    def stats(self) -> dict[str, Any]:
        fired = self.fired_count
        return {
            "fired": fired,
            "hits": self.hit_count,
            "wasted": self.wasted_count,
            "hit_rate": round(self.hit_count / fired, 3) if fired else None,
            "wasted_rate": round(self.wasted_count / fired, 3) if fired else None,
        }
