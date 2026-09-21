"""One spoken turn, end to end (11D, 11E, 11F).

    final transcript
      → LASA gate                     (confirm by voice instead of guessing)
      → provision query row           (same sessions/queries as the text path)
      → inline PHI scan               (regex, <5 ms; blocks before anything)
      → speculation resolve           (retrieval already done? reuse it)
      → guardrails ∥ graph            (scope/red-flag in parallel with RAG)
      → token stream → sentences      (segmented, grounding-gated, rendered)
      → TTS → audio frames            (released only after the guardrail verdict)
      → persist + waterfall

Three concurrent stages — the graph (LLM), the speaker (TTS), and the client's
playback — form one pipeline. Cancelling the turn (barge-in, guardrail block,
stop) cancels the graph and the TTS immediately and accounts for what was
thrown away (11D.4). Sentences not yet spoken are retained so "continue"
resumes rather than regenerates.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from collections.abc import Awaitable
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol
from uuid import UUID

import structlog

from app.config import get_settings
from app.core.telemetry import bind_context
from app.graph.graph import AgentGraph, citation_from_chunk
from app.graph.reasoner import get_reasoner
from app.graph.state import GraphEvent
from app.guardrails.grounding import verify_grounding
from app.guardrails.pipeline import GuardrailPipeline
from app.repositories.base import tenant_connection
from app.repositories.voice import VoiceRepository
from app.retrieval.embed import EmbeddingService
from app.retrieval.embedders import get_embedder
from app.retrieval.pipeline import RetrievalConfig, RetrievalPipeline
from app.retrieval.rerank import get_reranker
from app.retrieval.types import RetrievedChunk
from app.schemas.answer import AnswerResult, Citation, Contradiction
from app.schemas.guardrails import PreRetrievalVerdict
from app.services import cost
from app.services.ask import AskService, _backend_components, _result_event
from app.voice import protocol
from app.voice.audio import duration_ms
from app.voice.correction import Word, join_words
from app.voice.gate import LasaDecision, lasa_gate
from app.voice.render import (
    OFFER_MORE,
    OFFER_WALKTHROUGH,
    SPOKEN_MASK,
    SPOKEN_RED_FLAG,
    SpokenSentence,
    abstention_spoken,
    guardrail_spoken,
    positions_spoken,
    render_sentence,
)
from app.voice.segmenter import SentenceSegmenter
from app.voice.speculation import SpeculationOutcome, normalize
from app.voice.states import VoiceState
from app.voice.tts.base import TtsStream
from app.voice.waterfall import TurnClock

logger = structlog.stdlib.get_logger("app.voice.turn")

Outcome = Literal[
    "answered",
    "abstained",
    "blocked_phi",
    "blocked_scope",
    "confirm_requested",
    "cancelled",
    "error",
]

CORE_SENTENCES = 3
CORE_SENTENCES_CONFLICT = 2
# How far ahead of real-time playback audio may be sent (11F.3).
LEAD_MS = 400.0


class SessionPort(Protocol):
    """What a turn needs from its session (implemented by VoiceSession)."""

    session_id: UUID
    org_id: UUID
    user_id: UUID
    query_session_id: UUID | None
    tts: TtsStream
    trusted_drugs: set[str]
    pool: Any
    redis: Any

    async def emit(self, event: Any) -> None: ...
    async def send_audio(self, turn: int, sentence: int, pcm: bytes) -> None: ...
    def transition(self, state: VoiceState, reason: str) -> None: ...
    def set_pending_confirm(self, turn: TurnRunner, decision: LasaDecision) -> None: ...
    def set_pending_remainder(
        self, sentences: list[SpokenSentence], kind: Literal["walkthrough", "more", "resume"]
    ) -> None: ...
    def speculation_result(self) -> Awaitable[SpeculationOutcome]: ...
    def note_spoken(self, text: str) -> None: ...
    @property
    def backend(self) -> str: ...
    @property
    def stt_model(self) -> str: ...
    @property
    def stt_provider(self) -> str: ...
    @property
    def lasa(self) -> Any: ...
    @property
    def confidence_floor(self) -> float: ...
    @property
    def mask_after_ms(self) -> int: ...
    @property
    def phi_detector(self) -> Any: ...


@dataclass(slots=True)
class TurnInput:
    words: list[Word]
    raw_text: str
    source: Literal["voice", "text"]
    clock: TurnClock
    corrections: list[dict[str, Any]] = field(default_factory=list)
    confirmation: dict[str, Any] = field(default_factory=dict)
    # Audio streamed to the recognizer since the previous turn — what the STT
    # provider bills for this turn (0 for a typed turn).
    stt_audio_ms: float = 0.0

    @property
    def text(self) -> str:
        return join_words(self.words)


@dataclass(slots=True)
class SpokenRecord:
    sentence: SpokenSentence
    index: int
    audio_ms: float = 0.0
    fully_sent: bool = False


class TurnRunner:
    def __init__(self, session: SessionPort, *, turn_index: int, turn_input: TurnInput) -> None:
        self._s = session
        self.turn_index = turn_index
        self.input = turn_input
        self.clock = turn_input.clock
        self._costs = cost.Collector()  # replaced by run()'s live collector
        self.outcome: Outcome = "cancelled"
        self.blocked_by: str | None = None
        self.query_id: UUID | None = None
        self.voice_turn_id: UUID | None = None
        self.result: AnswerResult | None = None
        self.mask_used = False
        self.barge_ins: list[float] = []
        self.speculation: dict[str, Any] = {}
        self._spec_outcome: SpeculationOutcome | None = None
        # pipeline plumbing
        self._queue: asyncio.Queue[SpokenSentence | None] = asyncio.Queue()
        self._gate = asyncio.Event()  # opens when the guardrail verdict allows speech
        self._blocked = False
        self._escalation: str | None = None
        self._speaker: asyncio.Task[None] | None = None
        self._graph_task: asyncio.Task[None] | None = None
        self._mask_task: asyncio.Task[None] | None = None
        self._merged: dict[str, RetrievedChunk] = {}
        self._citations: dict[int, Citation] = {}
        self._contradiction: Contradiction | None = None
        self._planned: list[SpokenSentence] = []
        self._remainder: list[SpokenSentence] = []
        self._offer_kind: Literal["walkthrough", "more"] | None = None
        self._core_limit = CORE_SENTENCES
        self._spoken: list[SpokenRecord] = []
        self._sentence_counter = 0
        self._current_sentence: SpokenRecord | None = None
        self._synthesized_ms = 0.0
        self._played_ms = 0.0
        self._first_audio_sent = False
        self._reasoner: Any = None
        self._cancel_reason: str | None = None
        self._done = asyncio.Event()
        self._pace_started: float | None = None
        self._pace_sent_ms = 0.0
        self._pace_last = 0.0
        # One sentence in the TTS at a time: the mask line and the answer
        # pipeline must never interleave audio.
        self._speak_lock = asyncio.Lock()

    # -- public ----------------------------------------------------------------------

    async def run(self) -> None:
        # One cost collector per turn: the recognizer's audio, the retrieval
        # and generation calls, and every sentence sent to the voice — the
        # graph and speaker tasks started inside inherit it.
        with cost.collecting() as self._costs:
            await self._run_guarded()

    async def _run_guarded(self) -> None:
        try:
            await self._run()
        except asyncio.CancelledError:
            self.outcome = "cancelled"
            await asyncio.shield(self._finish(cancelled=True))
            raise
        except Exception as exc:
            logger.exception("voice_turn_failed", turn=self.turn_index)
            self.outcome = "error"
            with contextlib.suppress(Exception):
                await self._s.emit(
                    protocol.ErrorEvent(code="turn_failed", message=type(exc).__name__)
                )
            await asyncio.shield(self._finish(cancelled=False))
        else:
            await self._finish(cancelled=False)
        finally:
            self._done.set()

    async def cancel(self, reason: str, *, stop_latency_ms: float | None = None) -> None:
        """Barge-in / stop: cut the LLM stream and the TTS immediately."""
        self._cancel_reason = reason
        if stop_latency_ms is not None:
            self.barge_ins.append(stop_latency_ms)
        await self._s.tts.cancel()
        for task in (self._graph_task, self._speaker, self._mask_task):
            if task is not None and not task.done():
                task.cancel()
        # Retain what was not spoken so "continue" resumes mid-answer.
        unspoken = self._unspoken_sentences()
        if unspoken:
            self._s.set_pending_remainder(unspoken, "resume")

    def note_playback(self, sentence: int, event: str, buffer_ms: float | None) -> None:
        if event == "started" and sentence == 0 and buffer_ms is not None:
            self.clock.mark("client_first_audio_ms", buffer_ms)
        if event == "ended":
            for record in self._spoken:
                if record.index == sentence:
                    self._played_ms += record.audio_ms

    async def speak_remainder(self, sentences: list[SpokenSentence]) -> None:
        """Speak retained sentences (after an offer was accepted or a resume)."""
        with cost.collecting() as collector:
            self._gate.set()
            self._speaker = asyncio.create_task(self._speak_loop(), name="voice-speaker")
            for sentence in sentences:
                await self._queue.put(sentence)
            await self._queue.put(None)
            await self._speaker
            self._speaker = None
            await self._flush_costs(collector)

    # -- the pipeline ------------------------------------------------------------------

    async def _run(self) -> None:
        s = self._s
        text = self.input.text
        self.clock.mark("transcript_final")
        if self.input.stt_audio_ms > 0:
            cost.record(
                "stt",
                provider=s.stt_provider,
                model=s.stt_model,
                units=round(self.input.stt_audio_ms / 1000, 3),
                unit="seconds",
            )

        # 1. LASA confirmation gate — before anything is written or retrieved.
        decision = lasa_gate(
            self.input.words,
            s.lasa,
            confidence_floor=s.confidence_floor,
            trusted=frozenset(s.trusted_drugs),
        )
        if decision is not None and not self.input.confirmation.get("chosen"):
            await self._confirm(decision)
            return

        # 2. Provision the query row in the shared session (11D.5).
        ask = AskService(s.pool, s.redis)
        self.query_id, session_id, contextualized = await ask.provision(
            query=text,
            org_id=s.org_id,
            user_id=s.user_id,
            session_id=s.query_session_id,
        )
        s.query_session_id = session_id
        bind_context(
            tenant_id=str(s.org_id),
            user_id=str(s.user_id),
            query_id=str(self.query_id),
            channel="voice",
        )
        self.clock.mark("provisioned")

        # 3. Inline PHI (regex, deterministic) — a hard stop before retrieval.
        guardrails = GuardrailPipeline(
            pool=s.pool,
            phi_detector=s.phi_detector,
            allow_llm=get_settings().ai_backend == "cloud",
        )
        if _inline_phi(contextualized):
            verdict = await guardrails.check_pre_retrieval(
                contextualized, org_id=s.org_id, user_id=s.user_id, query_id=self.query_id
            )
            self.clock.mark("guardrail_verdict")
            await self._block(verdict)
            return

        # 4. Speculation: were the chunks already retrieved on a partial?
        self._spec_outcome = await s.speculation_result()
        # Whatever the speculation spent is this turn's — used or wasted.
        self._costs.events.extend(self._spec_outcome.costs)
        self.speculation = self._spec_outcome.as_dict()
        await s.emit(
            protocol.SpeculationEvent(
                turn=self.turn_index,
                fired=self._spec_outcome.fired,
                hit=self._spec_outcome.hit,
                similarity=self._spec_outcome.similarity,
                wasted=self._spec_outcome.wasted,
            )
        )

        # 5. Guardrails in parallel with retrieval/generation. The speaker
        #    stage will not release a byte until the verdict lands.
        guardrail_task = asyncio.create_task(
            guardrails.check_pre_retrieval(
                contextualized, org_id=s.org_id, user_id=s.user_id, query_id=self.query_id
            ),
            name="voice-guardrails",
        )
        self._speaker = asyncio.create_task(self._speak_loop(), name="voice-speaker")
        self._mask_task = asyncio.create_task(self._mask_loop(), name="voice-mask")
        self._graph_task = asyncio.create_task(
            self._graph_loop(ask, contextualized), name="voice-graph"
        )

        verdict = await guardrail_task
        self.clock.mark("guardrail_verdict")
        if not verdict.allowed:
            self._blocked = True
            self._graph_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._graph_task
            self._speaker.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._speaker
            self._speaker = None
            await ask.mark_blocked(org_id=s.org_id, user_id=s.user_id, query_id=self.query_id)
            await self._block(verdict)
            return
        if verdict.escalation_banner:
            self._escalation = SPOKEN_RED_FLAG
        self._gate.set()

        await self._graph_task
        await self._queue.put(None)
        await self._speaker
        self._speaker = None
        if self._mask_task is not None:
            self._mask_task.cancel()

        # 6. Progressive disclosure: offer the rest instead of monologuing.
        if self._remainder and self.outcome in ("answered",):
            kind = self._offer_kind or "more"
            offer_text = OFFER_WALKTHROUGH if kind == "walkthrough" else OFFER_MORE
            await self.speak_line(SpokenSentence(offer_text, offer_text, [], "offer"))
            await s.emit(
                protocol.OfferEvent(
                    turn=self.turn_index, kind=kind, remaining_sentences=len(self._remainder)
                )
            )
            s.set_pending_remainder(self._remainder, kind)

    async def _confirm(self, decision: LasaDecision) -> None:
        s = self._s
        self.outcome = "confirm_requested"
        self.input.confirmation = decision.as_dict()
        await s.emit(
            protocol.ConfirmRequestEvent(
                turn=self.turn_index,
                heard=decision.heard,
                options=[
                    protocol.ConfirmOption(name=o.name, description=o.description)
                    for o in decision.options
                ],
                prompt=decision.prompt,
            )
        )
        s.set_pending_confirm(self, decision)
        await self.speak_line(
            SpokenSentence(decision.prompt, decision.prompt, [], "confirmation"), mark=False
        )

    async def _block(self, verdict: PreRetrievalVerdict) -> None:
        code = verdict.findings[0].code if verdict.findings else None
        spoken = guardrail_spoken(verdict.blocked_by, code)
        self.outcome = "blocked_phi" if verdict.blocked_by == "phi" else "blocked_scope"
        self.blocked_by = verdict.blocked_by
        await self._s.emit(
            protocol.GuardrailEvent(
                turn=self.turn_index,
                verdict="blocked",
                blocked_by=verdict.blocked_by,
                code=code,
                message=verdict.message or "Blocked by a safety guardrail.",
                spoken=spoken,
                query_session_id=self._thread_id(),
            )
        )
        await self.speak_line(SpokenSentence(spoken, spoken, [], "refusal"), mark=False)

    # -- graph stage ---------------------------------------------------------------------

    async def _graph_loop(self, ask: AskService, contextualized: str) -> None:
        s = self._s
        reasoner_name, embedder_name, reranker_name = _backend_components()
        embedder = EmbeddingService(get_embedder(embedder_name), s.redis)
        retrieval = RetrievalPipeline(
            s.pool, embedder, get_reranker(reranker_name), RetrievalConfig()
        )
        spec = self._spec_outcome
        speculated_text = normalize(spec.speculated_text or "") if spec else ""

        async def retrieve_fn(sub_query: str) -> list[RetrievedChunk]:
            wanted = normalize(sub_query)
            speculated = spec.result if spec is not None and spec.hit else None
            if speculated is not None and wanted in (normalize(contextualized), speculated_text):
                chunks = list(speculated)
                self._merge(chunks)
                self.clock.mark("retrieval_done")
                self.speculation["reused"] = True
                return chunks
            result = await retrieval.retrieve(sub_query, org_id=s.org_id, query_id=self.query_id)
            self._merge(result.chunks)
            self.clock.mark("retrieval_done")
            return result.chunks

        self._reasoner = get_reasoner(reasoner_name, voice=True)
        graph = AgentGraph(retrieve_fn, self._reasoner, stream_tokens=True)
        segmenter = SentenceSegmenter(min_chars=40)
        started = time.perf_counter()
        tags = {"tenant_id": str(s.org_id), "query_id": str(self.query_id), "channel": "voice"}
        result: AnswerResult | None = None

        async for kind, payload in graph.astream(contextualized, tags=tags):
            if kind == "result":
                result = payload
                continue
            assert isinstance(payload, GraphEvent)
            if payload.stage == "generating":
                # Events are consumed behind the graph's execution, so the
                # retrieval callbacks have already merged every chunk; the
                # marker order is what the generator announces here.
                ordered = [str(i) for i in payload.data.get("chunk_ids", [])]
                self._citations = {
                    marker: citation_from_chunk(marker, self._merged[cid])
                    for marker, cid in enumerate(ordered, start=1)
                    if cid in self._merged
                }
            elif payload.stage == "conflict_found":
                raw = payload.data.get("contradiction")
                if isinstance(raw, dict):
                    self._contradiction = Contradiction.model_validate(raw)
                    self._core_limit = CORE_SENTENCES_CONFLICT
                    self._offer_kind = "walkthrough"
                    opener = "The sources disagree on this."
                    await self._plan(SpokenSentence(opener, opener, []))
            elif payload.stage == "token":
                if self._reasoner.name == "llm":  # the heuristic replay is not an LLM leg
                    self.clock.mark("llm_first_token")
                for sentence in segmenter.push(str(payload.data.get("text", ""))):
                    await self._on_sentence(sentence)

        for sentence in segmenter.flush():
            await self._on_sentence(sentence)
        assert result is not None
        self.result = result

        if result.abstained:
            self.outcome = "abstained"
            line = abstention_spoken(result)
            await self._plan(SpokenSentence(line, line, [], "abstention"), force=True)
        else:
            self.outcome = "answered"
            if self._contradiction is not None:
                # The walk-through: both positions first, then whatever the
                # core did not cover.
                self._remainder = [
                    *positions_spoken(self._contradiction, self._citations),
                    *self._remainder,
                ]

        latency_ms = int((time.perf_counter() - started) * 1000)
        usage = self._reasoner.usage
        assert self.query_id is not None
        answer_id = await ask.persist_result(
            org_id=s.org_id,
            user_id=s.user_id,
            query_id=self.query_id,
            result=result,
            latency_ms=latency_ms,
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            cost_usd=usage.cost_usd,
        )
        event = _result_event(
            answer_id,
            self.query_id,
            result,
            latency_ms,
            cached=False,
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            cost_usd=usage.cost_usd,
        )
        await s.emit(
            protocol.ResultEvent(
                turn=self.turn_index,
                query_id=str(self.query_id),
                query_session_id=self._thread_id(),
                data=event["data"],
            )
        )

    def _thread_id(self) -> str | None:
        sid = self._s.query_session_id
        return str(sid) if sid else None

    def _merge(self, chunks: list[RetrievedChunk]) -> None:
        for chunk in chunks:
            key = str(chunk.chunk_id)
            if key not in self._merged or chunk.score > self._merged[key].score:
                self._merged[key] = chunk

    async def _on_sentence(self, sentence: str) -> None:
        """Grounding-gate a generated sentence, then render it for speech."""
        passages = {marker: c.passage for marker, c in self._citations.items()}
        verdict = await verify_grounding(sentence, passages)
        if not verdict.accepted or not verdict.kept_answer:
            logger.info(
                "voice_sentence_withheld",
                sentence=sentence[:80],
                verdicts=[(v.support, v.cited_markers) for v in verdict.sentence_verdicts],
                known_markers=sorted(passages),
            )
            return
        previous = self._planned[-1].markers if self._planned else None
        spoken = render_sentence(sentence, self._citations, previous_markers=previous)
        if not spoken.spoken:
            return
        await self._plan(spoken)

    async def _plan(self, sentence: SpokenSentence, *, force: bool = False) -> None:
        self._planned.append(sentence)
        core_count = sum(1 for p in self._planned if p.kind == "answer")
        if force or core_count <= self._core_limit:
            if not self.clock.has("first_sentence"):
                self.clock.mark("first_sentence")
            await self._queue.put(sentence)
        else:
            self._remainder.append(sentence)
            if self._offer_kind is None:
                self._offer_kind = "more"

    # -- speaker stage -------------------------------------------------------------------

    async def _speak_loop(self) -> None:
        await self._gate.wait()
        if self._escalation is not None:
            line = self._escalation
            await self._s.emit(
                protocol.GuardrailEvent(
                    turn=self.turn_index,
                    verdict="escalation",
                    blocked_by=None,
                    code="red_flag_emergency",
                    message="Emergency indicators detected.",
                    spoken=line,
                    query_session_id=self._thread_id(),
                )
            )
            await self._speak_one(SpokenSentence(line, line, [], "escalation"), mark=False)
        while True:
            sentence = await self._queue.get()
            if sentence is None:
                return
            await self._speak_one(sentence)

    async def speak_line(self, sentence: SpokenSentence, *, mark: bool = True) -> None:
        """Speak a single system line outside the answer pipeline."""
        self._gate.set()
        await self._speak_one(sentence, mark=mark)

    async def _speak_one(self, sentence: SpokenSentence, *, mark: bool = True) -> None:
        async with self._speak_lock:
            await self._speak_locked(sentence, mark=mark)

    async def _speak_locked(self, sentence: SpokenSentence, *, mark: bool) -> None:
        s = self._s
        index = self._sentence_counter
        self._sentence_counter += 1
        record = SpokenRecord(sentence, index)
        self._spoken.append(record)
        self._current_sentence = record
        if s_state_needs_speaking(sentence.kind):
            s.transition(VoiceState.SPEAKING, f"sentence:{index}")
        s.note_spoken(sentence.spoken)
        await s.emit(
            protocol.AgentSentenceEvent(
                turn=self.turn_index,
                index=index,
                text=sentence.text,
                spoken_text=sentence.spoken,
                markers=sentence.markers,
                kind=sentence.kind,
            )
        )
        await s.emit(protocol.AudioStartEvent(turn=self.turn_index, index=index))
        cost.record(
            "tts",
            provider=_tts_provider(s.tts.model),
            model=s.tts.model,
            units=len(sentence.spoken),
            unit="characters",
        )
        first = True
        async for chunk in s.tts.synthesize(sentence.spoken):
            if first:
                first = False
                if mark and sentence.kind != "mask":
                    self.clock.mark("tts_first_byte")
            self._synthesized_ms += duration_ms(chunk)
            record.audio_ms += duration_ms(chunk)
            await self._pace(duration_ms(chunk))
            await s.send_audio(self.turn_index, index, chunk)
            if mark and sentence.kind != "mask" and not self._first_audio_sent:
                self._first_audio_sent = True
                self.clock.mark("first_audio_sent")
                if self._mask_task is not None:
                    self._mask_task.cancel()
        record.fully_sent = True
        await s.emit(protocol.AudioEndEvent(turn=self.turn_index, index=index))
        self._current_sentence = None

    async def _pace(self, chunk_ms: float) -> None:
        """Send audio no further ahead of real time than ``LEAD_MS``: the
        client only needs a small jitter buffer, and a session that is still
        SPEAKING while the answer plays is what makes barge-in coherent."""
        now = time.perf_counter()
        if self._pace_started is None or now - self._pace_last > 1.0:
            # A new sentence after a gap starts a fresh clock.
            self._pace_started = now
            self._pace_sent_ms = 0.0
        self._pace_sent_ms += chunk_ms
        self._pace_last = now
        ahead = self._pace_sent_ms - (now - self._pace_started) * 1000.0
        if ahead > LEAD_MS:
            await asyncio.sleep((ahead - LEAD_MS) / 1000.0)
            self._pace_last = time.perf_counter()

    async def _mask_loop(self) -> None:
        """Past ``mask_after_ms`` with nothing spoken, say what is happening.
        Counted as a failure indicator — it is one."""
        await asyncio.sleep(self._s.mask_after_ms / 1000)
        await self._gate.wait()
        if self._first_audio_sent or self._blocked:
            return
        self.mask_used = True
        await self._speak_one(SpokenSentence(SPOKEN_MASK, SPOKEN_MASK, [], "mask"), mark=False)

    def _unspoken_sentences(self) -> list[SpokenSentence]:
        unspoken: list[SpokenSentence] = []
        current = self._current_sentence
        if current is not None and not current.fully_sent and current.sentence.kind == "answer":
            unspoken.append(current.sentence)
        while not self._queue.empty():
            item = self._queue.get_nowait()
            if item is not None:
                unspoken.append(item)
        unspoken.extend(self._remainder)
        self._remainder = []
        return unspoken

    # -- accounting ----------------------------------------------------------------------

    def waste(self) -> dict[str, Any]:
        output_tokens = int(self._reasoner.usage.output_tokens) if self._reasoner else 0
        wasted_tokens = 0
        if self.outcome == "cancelled" and output_tokens:
            spoken_chars = sum(len(r.sentence.spoken) for r in self._spoken if r.fully_sent)
            total_chars = sum(len(p.spoken) for p in self._planned) or 1
            wasted_tokens = round(output_tokens * (1 - min(spoken_chars / total_chars, 1.0)))
        return {
            "output_tokens": output_tokens,
            "wasted_output_tokens": wasted_tokens,
            "synthesized_audio_ms": round(self._synthesized_ms, 1),
            "played_audio_ms": round(self._played_ms, 1),
            "wasted_audio_ms": round(max(self._synthesized_ms - self._played_ms, 0.0), 1),
            "cancel_reason": self._cancel_reason,
        }

    def waterfall(self) -> dict[str, Any]:
        return {
            "legs": self.clock.legs(),
            "total_first_audio_ms": self.clock.total_first_audio_ms(),
            "client_first_audio_ms": self.clock.client_first_audio_ms(),
        }

    async def _finish(self, *, cancelled: bool) -> None:
        s = self._s
        if self._mask_task is not None and not self._mask_task.done():
            self._mask_task.cancel()
        # Persist first: the waterfall event and the state change both mean
        # "the row is written" to anyone watching.
        await self._persist()
        await self._flush_costs(self._costs)
        waterfall = self.waterfall()
        await s.emit(
            protocol.WaterfallEvent(
                turn=self.turn_index,
                legs=waterfall["legs"],
                total_first_audio_ms=waterfall["total_first_audio_ms"],
                client_first_audio_ms=waterfall["client_first_audio_ms"],
                speculation=self.speculation,
                waste=self.waste(),
                mask_used=self.mask_used,
            )
        )
        next_state = s_state_after_turn(self.outcome)
        if next_state is not None and not cancelled:
            with contextlib.suppress(Exception):
                s.transition(next_state, self.outcome)

    async def _flush_costs(self, collector: cost.Collector) -> None:
        s = self._s
        if s.pool is None:
            return
        await cost.flush(
            s.pool,
            collector,
            org_id=s.org_id,
            query_id=self.query_id,
            voice_session_id=s.session_id,
        )

    async def _persist(self) -> None:
        s = self._s
        if s.pool is None:
            return
        try:
            async with tenant_connection(s.pool, s.org_id, s.user_id) as conn:
                self.voice_turn_id = await VoiceRepository().insert_turn(
                    conn,
                    org_id=s.org_id,
                    user_id=s.user_id,
                    voice_session_id=s.session_id,
                    query_session_id=s.query_session_id,
                    query_id=self.query_id,
                    turn_index=self.turn_index,
                    backend=s.backend,
                    stt_model=s.stt_model,
                    tts_model=s.tts.model,
                    transcript_raw=self.input.raw_text,
                    transcript_final=self.input.text,
                    corrections=self.input.corrections,
                    confirmation=self.input.confirmation,
                    outcome=self.outcome,
                    blocked_by=self.blocked_by,
                    latency=self.waterfall(),
                    speculation=self.speculation,
                    barge_in={"count": len(self.barge_ins), "stop_ms": self.barge_ins},
                    waste=self.waste(),
                    mask_used=self.mask_used,
                )
        except Exception as exc:  # persistence must never break the conversation
            logger.warning("voice_turn_persist_failed", error=f"{type(exc).__name__}: {exc}")

    async def merge_client_latency(self) -> None:
        s = self._s
        if self.voice_turn_id is None or s.pool is None:
            return
        try:
            async with tenant_connection(s.pool, s.org_id, s.user_id) as conn:
                await VoiceRepository().merge_latency(
                    conn, org_id=s.org_id, turn_id=self.voice_turn_id, latency=self.waterfall()
                )
                await VoiceRepository().merge_waste(
                    conn, org_id=s.org_id, turn_id=self.voice_turn_id, waste=self.waste()
                )
        except Exception as exc:
            logger.warning("voice_turn_latency_merge_failed", error=f"{type(exc).__name__}: {exc}")


def s_state_needs_speaking(kind: str) -> bool:
    return kind != "mask"


def s_state_after_turn(outcome: Outcome) -> VoiceState | None:
    if outcome == "confirm_requested":
        return VoiceState.CONFIRMING
    if outcome == "cancelled":
        return None
    return VoiceState.LISTENING


def _inline_phi(text: str) -> bool:
    from app.guardrails.phi import PhiDetector

    return PhiDetector(use_presidio=False).scan(text).detected


def _tts_provider(model: str) -> str:
    """The billing provider behind a TTS stream, from its model label."""
    return "elevenlabs" if model.startswith("eleven") else cost.LOCAL_PROVIDER
