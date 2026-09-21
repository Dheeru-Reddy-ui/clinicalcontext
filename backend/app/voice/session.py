"""The per-connection voice session (11A, 11C, 11D.1, 11F.3).

Owns the state machine, the STT and TTS streams, the endpointing loop, the
speculative retriever, and the current turn. It talks to the client through
a ``Transport`` (the WebSocket in production, an in-memory double in tests)
and survives a dropped socket: the client reconnects with the session id and
resume token and the same object keeps going.

Audio path per 20 ms frame: VAD → (speech bookkeeping) → STT. Silence after
speech runs the layered endpoint decision every frame; a commit finalizes the
recognizer, runs the correction pass, and hands the turn to a TurnRunner.
"""

from __future__ import annotations

import asyncio
import contextlib
import secrets
from collections.abc import AsyncIterator
from dataclasses import dataclass, replace
from typing import Any, Literal, Protocol
from uuid import UUID, uuid4

import structlog
from pydantic import BaseModel

from app.core.security import CurrentUser
from app.retrieval.embed import EmbeddingService
from app.retrieval.embedders import get_embedder
from app.retrieval.pipeline import RetrievalConfig, RetrievalPipeline
from app.retrieval.rerank import get_reranker
from app.retrieval.types import RetrievedChunk
from app.services.ask import AskService, _backend_components
from app.voice import protocol
from app.voice.audio import EnergyVad
from app.voice.correction import MedicalTermCorrector, Word
from app.voice.endpointing import (
    CompletenessVerdict,
    EndpointLayer,
    classify_reply,
    heuristic_completeness,
    is_backchannel,
)
from app.voice.gate import LasaDecision, apply_choice
from app.voice.lasa import resolve_choice
from app.voice.render import (
    SPOKEN_CONFIRM_FAILED,
    SPOKEN_NOTHING_TO_RESUME,
    SpokenSentence,
)
from app.voice.runtime import BOOST_TERMS, VoiceRuntime
from app.voice.speculation import SpeculationOutcome, SpeculativeRetriever, similarity
from app.voice.states import IllegalTransition, SessionStateMachine, Transition, VoiceState
from app.voice.stt.base import SttEvent, SttStream, SttWord
from app.voice.tts.base import TtsStream
from app.voice.turn import TurnInput, TurnRunner
from app.voice.waterfall import TurnClock, now_ms

logger = structlog.stdlib.get_logger("app.voice.session")

_FINAL_TIMEOUT_S = 12.0
# Word gaps are ~50-100 ms; a pause this long is where a speech-end decode
# pays for itself (Deepgram needs no hint — it streams continuously).
_HINT_SILENCE_MS = 160.0
_LISTEN_STATES = frozenset({VoiceState.LISTENING, VoiceState.ENDPOINTING, VoiceState.CONFIRMING})


class Transport(Protocol):
    async def send_json(self, payload: dict[str, Any]) -> None: ...
    async def send_bytes(self, data: bytes) -> None: ...


@dataclass(slots=True)
class PendingConfirm:
    turn: TurnRunner
    decision: LasaDecision


@dataclass(slots=True)
class PendingRemainder:
    sentences: list[SpokenSentence]
    kind: Literal["walkthrough", "more", "resume"]
    turn: TurnRunner


class VoiceSession:
    def __init__(
        self,
        *,
        runtime: VoiceRuntime,
        pool: Any,
        redis: Any,
        user: CurrentUser,
        query_session_id: UUID | None,
        transport: Transport,
        tts_quality: str = "flash",
    ) -> None:
        assert user.org_id is not None
        self.session_id = uuid4()
        self.resume_token = secrets.token_urlsafe(24)
        self.org_id: UUID = user.org_id
        self.user_id: UUID = user.user_id
        self.query_session_id = query_session_id
        self.pool = pool
        self.redis = redis
        self._runtime = runtime
        self._transport: Transport | None = transport
        self._tts_quality = tts_quality
        self.sm = SessionStateMachine(str(self.session_id))
        self.sm.on_transition(self._on_transition)
        self.turn_index = 0
        self.trusted_drugs: set[str] = set()
        self.stt: SttStream | None = None
        self.tts: TtsStream = _NoTts()
        self._vad = EnergyVad()
        self._corrector: MedicalTermCorrector | None = None
        self._boost: list[str] = []
        # listening-turn bookkeeping
        self._had_speech = False
        self._speech_end_ms: float | None = None
        self._speech_start_ms: float | None = None
        self._partial_text = ""
        self._partial_words: list[SttWord] = []
        self._completeness: CompletenessVerdict | None = None
        self._completeness_for = ""
        self._completeness_task: asyncio.Task[None] | None = None
        self._partial_at_ms: float | None = None
        self._partial_covered_ms: float | None = None
        # Stream time: ms of audio handed to the recognizer, and where the
        # user's last speech frame sits in it.
        self._stt_sent_ms = 0.0
        self._stt_billed_ms = 0.0  # audio already attributed to a turn's cost
        self._speech_end_sent_ms: float | None = None
        self._final_future: asyncio.Future[SttEvent] | None = None
        self._committing = False
        self._speech_end_hinted = False
        # A commit whose *final* transcript read as incomplete (the partial the
        # decision was made on lacked the trailing "for…") is retracted: its
        # words are held as a prefix and the window runs on to the limit.
        self._held_words: list[SttWord] = []
        self._held_text = ""
        # pipeline
        self._spec: SpeculativeRetriever | None = None
        self._current: TurnRunner | None = None
        self._current_task: asyncio.Task[None] | None = None
        self._pending_confirm: PendingConfirm | None = None
        self._pending_remainder: PendingRemainder | None = None
        self._stt_task: asyncio.Task[None] | None = None
        self._watchdog: asyncio.Task[None] | None = None
        self._closed = False
        self._turns: dict[int, TurnRunner] = {}
        self._send_lock = asyncio.Lock()
        self._current_spoken_text = ""

    # -- SessionPort ------------------------------------------------------------------

    @property
    def backend(self) -> str:
        return self._runtime.backend

    @property
    def stt_model(self) -> str:
        return self._runtime.stt.model

    @property
    def stt_provider(self) -> str:
        name = self._runtime.stt.name
        return name if name == "deepgram" else "local"

    @property
    def lasa(self) -> Any:
        return self._runtime.lasa

    @property
    def confidence_floor(self) -> float:
        return self._runtime.settings.voice_lasa_confidence_floor

    @property
    def mask_after_ms(self) -> int:
        return self._runtime.settings.voice_mask_after_ms

    @property
    def phi_detector(self) -> Any:
        return self._runtime.phi_full

    async def emit(self, event: Any) -> None:
        payload = event.model_dump(mode="json") if isinstance(event, BaseModel) else dict(event)
        transport = self._transport
        if transport is None:
            return
        async with self._send_lock:
            try:
                await transport.send_json(payload)
            except Exception as exc:  # the socket is gone; resume will re-attach
                logger.info("voice_transport_send_failed", error=type(exc).__name__)
                self._transport = None

    async def send_audio(self, turn: int, sentence: int, pcm: bytes) -> None:
        transport = self._transport
        if transport is None:
            return
        async with self._send_lock:
            try:
                await transport.send_bytes(protocol.pack_audio(turn, sentence, pcm))
            except Exception as exc:
                logger.info("voice_transport_send_failed", error=type(exc).__name__)
                self._transport = None

    def transition(self, state: VoiceState, reason: str) -> None:
        try:
            self.sm.transition(state, reason)
        except IllegalTransition as exc:
            logger.warning("voice_illegal_transition", error=str(exc))

    def set_pending_confirm(self, turn: TurnRunner, decision: LasaDecision) -> None:
        self._pending_confirm = PendingConfirm(turn, decision)
        self._pending_remainder = None

    def set_pending_remainder(
        self, sentences: list[SpokenSentence], kind: Literal["walkthrough", "more", "resume"]
    ) -> None:
        if self._current is not None:
            self._pending_remainder = PendingRemainder(sentences, kind, self._current)

    async def speculation_result(self) -> SpeculationOutcome:
        if self._spec is None:
            return SpeculationOutcome(False, None, None, False, None, None, None)
        return await self._spec.resolve(self._committed_text)

    # -- lifecycle ---------------------------------------------------------------------

    async def start(self) -> None:
        runtime = self._runtime
        vocabulary = await runtime.vocabulary(self.pool)
        self._boost = vocabulary.boost_terms(BOOST_TERMS)
        self._corrector = MedicalTermCorrector(vocabulary)
        lexicon = runtime.lexicon_for(vocabulary)
        tts_provider = runtime.tts
        set_respeller = getattr(tts_provider, "set_respeller", None)
        if callable(set_respeller):
            set_respeller(lexicon.apply_respellings)
        # Pre-warm both providers (11F.1) and the PHI engine: handshakes and
        # model loads off the critical path of the first turn.
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, runtime.warm_guardrails)
        self.stt, self.tts = await asyncio.gather(
            runtime.stt.open(boost=self._boost),
            tts_provider.open(quality=self._tts_quality, lexicon_pls=lexicon.to_pls()),
        )
        self._spec = SpeculativeRetriever(
            self._speculative_retrieve,
            min_words=runtime.settings.voice_speculation_min_words,
            stable_ms=runtime.settings.voice_speculation_stable_ms,
            similarity_threshold=runtime.settings.voice_speculation_similarity,
        )
        self._stt_task = asyncio.create_task(self._stt_loop(), name="voice-stt-events")
        self._watchdog = asyncio.create_task(self._endpoint_watchdog(), name="voice-endpoint")
        self.sm.transition(VoiceState.LISTENING, "session_start")
        await self.emit(self._session_event(resumed=False))

    async def attach(self, transport: Transport) -> None:
        """Re-attach a reconnected client: same session, same conversation."""
        self._transport = transport
        await self.emit(self._session_event(resumed=True))
        if self._partial_text:
            await self.emit(
                protocol.PartialEvent(
                    turn=self.turn_index, text=self._partial_text, words=self._words_out()
                )
            )

    def detach(self) -> None:
        self._transport = None

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        await self._cancel_current("session_closed")
        if self._stt_task is not None:
            self._stt_task.cancel()
        if self._watchdog is not None:
            self._watchdog.cancel()
        if self._spec is not None:
            self._spec.cancel()
        if self.stt is not None:
            with contextlib.suppress(Exception):
                await self.stt.close()
        with contextlib.suppress(Exception):
            await self.tts.close()
        with contextlib.suppress(IllegalTransition):
            self.sm.transition(VoiceState.CLOSED, "close")

    def _session_event(self, *, resumed: bool) -> protocol.SessionEvent:
        return protocol.SessionEvent(
            session_id=str(self.session_id),
            resume_token=self.resume_token,
            query_session_id=str(self.query_session_id) if self.query_session_id else None,
            backend=self.backend,
            stt_model=self.stt_model,
            tts_model=self.tts.model,
            state=self.sm.state.value,
            resumed=resumed,
        )

    def _on_transition(self, record: Transition) -> None:
        asyncio.get_running_loop().create_task(
            self.emit(
                protocol.StateEvent(
                    state=record.to_state.value,
                    from_state=record.from_state.value,
                    reason=record.reason,
                    turn=self.turn_index,
                )
            )
        )

    # -- audio in ----------------------------------------------------------------------

    async def on_audio(self, pcm: bytes) -> None:
        if self._closed or self.stt is None:
            return
        decision = self._vad.process(pcm)
        state = self.sm.state
        now = now_ms()
        if state in _LISTEN_STATES:
            if decision.speech:
                if not self._had_speech:
                    self._had_speech = True
                    self._speech_start_ms = now
                self._speech_end_ms = now
                self._speech_end_sent_ms = self._stt_sent_ms + len(pcm) / 32
                self._speech_end_hinted = False
                if state == VoiceState.ENDPOINTING:
                    self.sm.transition(VoiceState.LISTENING, "speech_resumed")
            await self.stt.send_audio(pcm)
            self._stt_sent_ms += len(pcm) / 32
            if self._had_speech and not decision.speech and not self._committing:
                # A pause longer than a gap between words: ask the recognizer
                # to decode what it has, so the commit finds a fresh transcript.
                if (
                    not self._speech_end_hinted
                    and self._speech_end_ms is not None
                    and now - self._speech_end_ms >= _HINT_SILENCE_MS
                ):
                    self._speech_end_hinted = True
                    hint = getattr(self.stt, "hint_speech_end", None)
                    if hint is not None:
                        logger.debug(
                            "voice_speech_end_hint",
                            speech_end_sent_ms=self._speech_end_sent_ms,
                            partial_covered_ms=self._partial_covered_ms,
                        )
                        await hint(speech_end_ms=self._speech_end_sent_ms)
                await self._maybe_endpoint(now)
        elif state == VoiceState.SPEAKING and decision.speech:
            # Server-side barge-in backup: the recognizer decides (see _stt_loop),
            # so speech-level audio keeps flowing while the agent talks.
            await self.stt.send_audio(pcm)
            self._stt_sent_ms += len(pcm) / 32

    async def _maybe_endpoint(self, now: float) -> None:
        assert self._speech_end_ms is not None
        silence_ms = now - self._speech_end_ms
        policy = self._runtime.endpoint_policy
        if silence_ms >= policy.base_ms and self.sm.state == VoiceState.LISTENING:
            self.sm.transition(VoiceState.ENDPOINTING, f"silence:{int(silence_ms)}ms")
        # The semantic layer needs a partial that includes the words spoken
        # just before the pause; a stale one (the recognizer still decoding)
        # leaves the verdict pending — the ceiling still commits.
        fresh = self._partial_is_fresh()
        completeness = (
            self._completeness if fresh and self._completeness_for == self._partial_text else None
        )
        commit, layer = policy.decide(silence_ms, completeness)
        if commit:
            await self._commit(layer, silence_ms)

    async def _endpoint_watchdog(self) -> None:
        """The layered decision must fire even if the client stops streaming
        frames (its own VAD went quiet, a hiccup): the ceiling never hangs."""
        try:
            while not self._closed:
                await asyncio.sleep(0.05)
                if (
                    self._had_speech
                    and self._speech_end_ms is not None
                    and not self._vad.in_speech
                    and not self._committing
                    and self.sm.state in _LISTEN_STATES
                ):
                    await self._maybe_endpoint(now_ms())
                await self._maybe_speculate()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("voice_endpoint_watchdog_failed")

    # -- STT events --------------------------------------------------------------------

    async def _stt_loop(self) -> None:
        assert self.stt is not None
        try:
            async for event in self.stt.events():
                if event.kind == "partial":
                    await self._on_partial(event)
                elif event.kind == "final":
                    if self._final_future is not None and not self._final_future.done():
                        self._final_future.set_result(event)
                elif event.kind == "error":
                    logger.warning("voice_stt_error", message=event.message)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("voice_stt_loop_failed")

    async def _on_partial(self, event: SttEvent) -> None:
        text = event.text.strip()
        if not text:
            return
        state = self.sm.state
        if state == VoiceState.SPEAKING:
            await self._maybe_server_barge_in(text)
            return
        if state not in _LISTEN_STATES or self._committing:
            return
        text = self._with_held(text)
        self._partial_text = text
        self._partial_words = [*self._held_words, *event.words]
        self._partial_at_ms = now_ms()
        self._partial_covered_ms = event.covered_ms
        await self.emit(
            protocol.PartialEvent(turn=self.turn_index, text=text, words=self._words_out())
        )
        # Semantic layer: heuristic now, LLM tier (if configured) in the background.
        verdict = heuristic_completeness(text)
        self._completeness = verdict
        self._completeness_for = text
        if self._runtime.settings.ai_backend == "cloud" and verdict.complete:
            if self._completeness_task is not None and not self._completeness_task.done():
                self._completeness_task.cancel()
            self._completeness_task = asyncio.create_task(self._refine_completeness(text))
        # Speculative retrieval on a stable, complete-looking, PHI-free partial
        # (stability is re-checked by the watchdog once partials stop coming).
        if self._spec is not None:
            self._spec.observe_partial(text)
        await self._maybe_speculate()

    async def _maybe_speculate(self) -> None:
        """Fire speculative retrieval on a partial that (a) was produced after
        the user paused, (b) has been stable for the configured window, and
        (c) reads as a complete question — i.e. while the endpoint decision is
        still pending. Evaluated on a timer: the last partial of an utterance
        is followed by silence, not by another partial."""
        spec = self._spec
        text = self._partial_text
        if spec is None or not text or self._pending_confirm is not None or self._committing:
            return
        if self.sm.state not in (VoiceState.LISTENING, VoiceState.ENDPOINTING):
            return
        if self._speech_end_ms is None or self._vad.in_speech:
            return
        fresh = self._partial_is_fresh()
        verdict = self._completeness if fresh and self._completeness_for == text else None
        if verdict is None:
            return
        # A partial produced after the pause is the utterance; it needs no
        # further stability window (the pause itself was the wait).
        spec.observe_partial(text)
        stable_ms = (
            now_ms() - self._speech_end_ms + self._runtime.settings.voice_speculation_stable_ms
        )
        if not spec.should_fire(text, stable_ms, verdict):
            return
        # Retrieve for what the final will say: the correction pass runs on
        # the partial too ("tursepotide" → tirzepatide), so a hit is a hit.
        query = self._corrected_partial_text()
        if self._runtime.phi_inline.scan(query).detected:
            return
        spec.fire(await self._contextualize(query))

    def _corrected_partial_text(self) -> str:
        if self._corrector is None or not self._partial_words:
            return self._partial_text
        corrected = self._corrector.correct(
            [Word(w.text, w.confidence) for w in self._partial_words]
        )
        return " ".join(w.text for w in corrected.words)

    def _partial_is_fresh(self) -> bool:
        """Does the current partial include the words spoken just before the
        pause? By coverage when the recognizer reports it (a decode that
        *finished* after the pause may have started before the last word), by
        emission time otherwise."""
        if self._speech_end_ms is None or self._partial_at_ms is None:
            return False
        if self._partial_covered_ms is not None and self._speech_end_sent_ms is not None:
            return self._partial_covered_ms >= self._speech_end_sent_ms
        return self._partial_at_ms >= self._speech_end_ms

    async def _refine_completeness(self, text: str) -> None:
        verdict = await self._runtime.completeness.classify(text)
        if self._completeness_for == text:
            self._completeness = verdict

    async def _maybe_server_barge_in(self, text: str) -> None:
        """A real utterance during playback interrupts; a backchannel or the
        agent's own echo does not."""
        if is_backchannel(text):
            return
        if self._current_spoken_text and similarity(text, self._current_spoken_text) >= 0.6:
            return  # echo of the agent's own sentence (no AEC on the client)
        await self.barge_in(stop_latency_ms=None, source="server")

    # -- turn commit -------------------------------------------------------------------

    async def _commit(self, layer: EndpointLayer, silence_ms: float) -> None:
        assert self.stt is not None
        self._committing = True
        clock = TurnClock()
        speech_end = self._speech_end_ms if self._speech_end_ms is not None else now_ms()
        clock.mark("speech_end", speech_end)
        clock.mark("endpoint_commit")
        decision_ms = now_ms() - speech_end
        decided_complete = bool(self._completeness and self._completeness.complete)
        self._speech_start_ms = None
        with contextlib.suppress(IllegalTransition):
            self.sm.transition(VoiceState.PROCESSING, f"endpoint:{layer}")
        loop = asyncio.get_running_loop()
        self._final_future = loop.create_future()
        await self.stt.finalize()
        try:
            final = await asyncio.wait_for(self._final_future, timeout=_FINAL_TIMEOUT_S)
        except TimeoutError:
            logger.warning("voice_final_timeout")
            final = SttEvent("final", text=self._partial_text, words=self._partial_words)
        finally:
            self._final_future = None
        # The provisional decision was made on a partial; the final transcript
        # can carry a word the partial lacked ("…treatment?" → "…treatment
        # for?"). If it now reads as incomplete and the window has room, hold
        # the words and keep listening — the ceiling still commits.
        final_text = self._with_held(final.text.strip()) if final.text.strip() else self._held_text
        final_words = [*self._held_words, *final.words] if final.text.strip() else self._held_words
        if final_text and layer == "vad" and decided_complete:
            verdict = heuristic_completeness(final_text)
            silence_now = now_ms() - speech_end
            if not verdict.complete and silence_now < self._runtime.endpoint_policy.ceiling_ms:
                await self._hold_final(final_text, final_words, verdict)
                logger.info(
                    "voice_commit_retracted",
                    session_id=str(self.session_id),
                    reason=verdict.reason,
                    silence_ms=round(silence_now),
                )
                return
        await self.emit(
            protocol.EndpointEvent(
                turn=self.turn_index,
                layer=layer,
                silence_ms=round(silence_ms, 1),
                complete=decided_complete,
                decision_ms=round(decision_ms, 1),
            )
        )
        self._reset_listening()
        raw_words = [Word(w.text, w.confidence) for w in final_words] or [
            Word(t, 1.0) for t in final_text.split()
        ]
        if not raw_words:
            self._committing = False
            self.transition(VoiceState.LISTENING, "empty_utterance")
            return
        await self._start_turn(raw_words, final_text, "voice", clock)

    def _with_held(self, text: str) -> str:
        """Held words from a retracted commit, then the recognizer's new text."""
        if not self._held_text:
            return text
        return f"{self._held_text.rstrip(' ?.!,')} {text}".strip()

    async def _hold_final(
        self, text: str, words: list[SttWord], verdict: CompletenessVerdict
    ) -> None:
        # The recognizer's guessed end-of-sentence punctuation on the last held
        # word ("for?") is not evidence and must not end up mid-question.
        held_words = list(words)
        if held_words:
            last = held_words[-1]
            held_words[-1] = replace(last, text=last.text.rstrip("?.!,"))
        self._held_text = text.rstrip(" ?.!,")
        self._held_words = held_words
        self._partial_text = text
        self._partial_words = list(words)
        self._partial_at_ms = now_ms()
        self._partial_covered_ms = None  # the final covered everything said so far
        self._completeness = verdict
        self._completeness_for = text
        self._speech_end_hinted = False
        self._committing = False
        # The recognizer starts a fresh buffer after finalize(); speech that
        # resumes is decoded on its own and joined to the held prefix.
        with contextlib.suppress(IllegalTransition):
            self.sm.transition(VoiceState.ENDPOINTING, "final_incomplete")
        # The held words stay on screen as the (grey) partial they still are.
        await self.emit(
            protocol.PartialEvent(turn=self.turn_index, text=text, words=self._words_out())
        )

    async def _start_turn(
        self,
        raw_words: list[Word],
        raw_text: str,
        source: Literal["voice", "text"],
        clock: TurnClock,
    ) -> None:
        assert self._corrector is not None
        corrected = self._corrector.correct(raw_words) if source == "voice" else None
        words = corrected.words if corrected else raw_words
        corrections = (
            [
                {"original": c.original, "corrected": c.corrected, "score": c.score}
                for c in corrected.corrections
            ]
            if corrected
            else []
        )
        text = " ".join(w.text for w in words)
        self._committed_text = await self._contextualize(text)
        await self.emit(
            protocol.FinalEvent(
                turn=self.turn_index,
                text=text,
                raw_text=raw_text,
                corrections=[protocol.CorrectionOut(**c) for c in corrections],
                source=source,
            )
        )
        turn_input = TurnInput(
            words=words,
            raw_text=raw_text,
            source=source,
            clock=clock,
            corrections=corrections,
            stt_audio_ms=self._stt_sent_ms - self._stt_billed_ms,
        )
        self._stt_billed_ms = self._stt_sent_ms

        # A reply to a pending confirmation or offer is consumed here.
        if self._pending_confirm is not None:
            await self._resolve_confirm(text, "voice" if source == "voice" else "tap", turn_input)
            self._committing = False
            return
        if self._pending_remainder is not None:
            reply = classify_reply(text)
            if reply == "continue":
                self._committing = False
                await self.resume_remainder()
                return
            if reply == "decline":
                self._pending_remainder = None
                self._committing = False
                self.transition(VoiceState.LISTENING, "offer_declined")
                return
            self._pending_remainder = None  # a new question supersedes the offer

        await self._run_turn(turn_input)

    async def _run_turn(self, turn_input: TurnInput) -> None:
        await self._cancel_current("superseded")
        if self.sm.state != VoiceState.PROCESSING:
            self.transition(VoiceState.PROCESSING, "turn_start")
        runner = TurnRunner(self, turn_index=self.turn_index, turn_input=turn_input)
        self._turns[self.turn_index] = runner
        self._current = runner
        self._committing = False
        self._current_task = asyncio.create_task(self._run_and_release(runner), name="voice-turn")

    async def _run_and_release(self, runner: TurnRunner) -> None:
        try:
            await runner.run()
        finally:
            if runner.outcome != "confirm_requested":
                self.turn_index += 1
            if self._current is runner:
                self._current = None
                self._current_task = None
            self._current_spoken_text = ""
            if runner.outcome != "cancelled" and self.stt is not None and not self._had_speech:
                # Drop whatever the recognizer heard while the agent was busy
                # (the next utterance starts clean). A barge-in keeps it, and
                # so does speech that already began after the agent finished.
                with contextlib.suppress(Exception):
                    await self.stt.reset()

    async def _resolve_confirm(
        self, reply: str, method: Literal["voice", "tap"], reply_input: TurnInput | None
    ) -> None:
        pending = self._pending_confirm
        assert pending is not None
        chosen = resolve_choice(reply, pending.decision.options)
        if chosen is None:
            self.transition(VoiceState.SPEAKING, "confirm_retry")
            await self._speak_system(SPOKEN_CONFIRM_FAILED, kind="confirmation")
            self.transition(VoiceState.CONFIRMING, "confirm_retry")
            return
        self._pending_confirm = None
        self.trusted_drugs.add(chosen.name)
        words = apply_choice(pending.turn.input.words, pending.decision, chosen)
        confirmation = {
            **pending.decision.as_dict(),
            "chosen": chosen.name,
            "method": method,
        }
        if reply_input is not None:
            clock = reply_input.clock
        else:
            # A tapped answer: the tap is the "speech end" the waterfall counts from.
            clock = TurnClock()
            clock.mark("speech_end")
            clock.mark("endpoint_commit")
        turn_input = TurnInput(
            words=words,
            raw_text=pending.turn.input.raw_text,
            source=pending.turn.input.source,
            clock=clock,
            corrections=pending.turn.input.corrections,
            confirmation=confirmation,
        )
        self._committed_text = await self._contextualize(turn_input.text)
        await self.emit(
            protocol.FinalEvent(
                turn=self.turn_index,
                text=turn_input.text,
                raw_text=turn_input.raw_text,
                corrections=[protocol.CorrectionOut(**c) for c in turn_input.corrections],
                source=turn_input.source,
            )
        )
        self.transition(VoiceState.PROCESSING, f"confirmed:{chosen.name}")
        await self._run_turn(turn_input)

    # -- client messages ---------------------------------------------------------------

    async def on_text(self, text: str) -> None:
        """A typed turn inside voice mode (handoff, both directions)."""
        clock = TurnClock()
        clock.mark("speech_end")
        clock.mark("endpoint_commit")
        await self.emit(
            protocol.EndpointEvent(
                turn=self.turn_index, layer="text", silence_ms=0.0, complete=True, decision_ms=0.0
            )
        )
        if self.sm.state == VoiceState.SPEAKING:
            await self.barge_in(stop_latency_ms=None, source="text")
        self._reset_listening()
        self._committing = True
        words = [Word(t, 1.0) for t in text.split()]
        await self._start_turn(words, text, "text", clock)

    async def on_confirm(self, choice: str) -> None:
        if self._pending_confirm is None:
            return
        await self._resolve_confirm(choice, "tap", None)

    async def on_continue(self) -> None:
        await self.resume_remainder()

    async def resume_remainder(self) -> None:
        pending = self._pending_remainder
        self._pending_remainder = None
        if pending is None or not pending.sentences:
            if self._current is None:
                await self._speak_system(SPOKEN_NOTHING_TO_RESUME)
            return
        self.transition(VoiceState.PROCESSING, f"resume:{pending.kind}")
        self._current = pending.turn
        self._current_spoken_text = pending.sentences[0].spoken
        self._current_task = asyncio.create_task(self._speak_pending(pending), name="voice-resume")

    async def _speak_pending(self, pending: PendingRemainder) -> None:
        try:
            await pending.turn.speak_remainder(pending.sentences)
        except asyncio.CancelledError:
            raise
        finally:
            if self._current is pending.turn:
                self._current = None
                self._current_task = None
            if self.sm.state == VoiceState.SPEAKING:
                self.transition(VoiceState.LISTENING, "resume_done")
            await pending.turn.merge_client_latency()

    async def barge_in(self, *, stop_latency_ms: float | None, source: str) -> None:
        """User speech during playback: stop within the budget, keep the rest."""
        if self.sm.state != VoiceState.SPEAKING:
            return
        self.sm.transition(VoiceState.BARGE_IN, f"barge_in:{source}")
        runner = self._current
        task = self._current_task
        if runner is not None:
            await runner.cancel("barge_in", stop_latency_ms=stop_latency_ms)
        if task is not None and not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
        self._current = None
        self._current_task = None
        self._reset_listening()
        self.sm.transition(VoiceState.LISTENING, "after_barge_in")

    async def on_playback(self, message: protocol.PlaybackMessage) -> None:
        runner = self._turns.get(message.turn)
        if runner is None:
            return
        runner.note_playback(message.sentence, message.event, message.buffer_ms)
        if message.event == "started" and message.sentence == 0:
            # The client-side leg landed: refresh the waterfall row and event.
            await self.emit(
                protocol.WaterfallEvent(
                    turn=runner.turn_index,
                    legs=runner.clock.legs(),
                    total_first_audio_ms=runner.clock.total_first_audio_ms(),
                    client_first_audio_ms=runner.clock.client_first_audio_ms(),
                    speculation=runner.speculation,
                    waste=runner.waste(),
                    mask_used=runner.mask_used,
                )
            )
            await runner.merge_client_latency()
        elif message.event == "ended":
            await runner.merge_client_latency()

    async def on_stop(self) -> None:
        await self._cancel_current("stop")
        self._pending_remainder = None
        self._pending_confirm = None
        self._reset_listening()
        with contextlib.suppress(IllegalTransition):
            self.sm.transition(VoiceState.IDLE, "stop")

    # -- helpers -----------------------------------------------------------------------

    _committed_text: str = ""

    async def _cancel_current(self, reason: str) -> None:
        runner = self._current
        task = self._current_task
        if runner is not None:
            await runner.cancel(reason)
        if task is not None and not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
        self._current = None
        self._current_task = None

    def _reset_listening(self) -> None:
        self._had_speech = False
        self._speech_end_ms = None
        self._speech_start_ms = None
        self._partial_text = ""
        self._partial_words = []
        self._completeness = None
        self._completeness_for = ""
        self._partial_at_ms = None
        self._partial_covered_ms = None
        self._speech_end_sent_ms = None
        self._speech_end_hinted = False
        self._held_words = []
        self._held_text = ""
        self._vad.reset()

    def _words_out(self) -> list[protocol.WordOut]:
        return [
            protocol.WordOut(text=w.text, confidence=round(w.confidence, 3))
            for w in self._partial_words
        ]

    async def _contextualize(self, text: str) -> str:
        if self.pool is None:
            return text
        try:
            return await AskService(self.pool, self.redis).contextualize(
                org_id=self.org_id,
                user_id=self.user_id,
                session_id=self.query_session_id,
                query=text,
            )
        except Exception as exc:
            logger.warning("voice_contextualize_failed", error=type(exc).__name__)
            return text

    async def _speculative_retrieve(self, query: str) -> list[RetrievedChunk]:
        _reasoner, embedder_name, reranker_name = _backend_components()
        embedder = EmbeddingService(get_embedder(embedder_name), self.redis)
        retrieval = RetrievalPipeline(
            self.pool, embedder, get_reranker(reranker_name), RetrievalConfig()
        )
        result = await retrieval.retrieve(query, org_id=self.org_id)
        return result.chunks

    def note_spoken(self, text: str) -> None:
        self._current_spoken_text = text

    async def _speak_system(self, text: str, *, kind: protocol.SentenceKind = "system") -> None:
        """A single line spoken outside any turn (nothing to resume, retry)."""
        index = protocol.SYSTEM_SENTENCE_INDEX
        self.transition(VoiceState.SPEAKING, "system_line")
        await self.emit(
            protocol.AgentSentenceEvent(
                turn=self.turn_index,
                index=index,
                text=text,
                spoken_text=text,
                kind=kind,
            )
        )
        await self.emit(protocol.AudioStartEvent(turn=self.turn_index, index=index))
        self._current_spoken_text = text
        async for chunk in self.tts.synthesize(text):
            await self.send_audio(self.turn_index, index, chunk)
        await self.emit(protocol.AudioEndEvent(turn=self.turn_index, index=index, last=True))
        self._current_spoken_text = ""
        if self.sm.state == VoiceState.SPEAKING:
            self.transition(VoiceState.LISTENING, "system_line_done")


class _NoTts:
    """Placeholder until ``start`` opens the real stream."""

    model = "none"

    async def synthesize(self, text: str) -> AsyncIterator[bytes]:
        del text
        empty: list[bytes] = []
        for chunk in empty:  # an empty async generator, typed as one
            yield chunk

    async def cancel(self) -> None:
        return None

    async def close(self) -> None:
        return None
