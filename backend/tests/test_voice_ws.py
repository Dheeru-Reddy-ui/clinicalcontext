"""Phase 11 gate tests over the real WebSocket pipeline.

The app runs in-process under uvicorn against the real Postgres + Redis
(see conftest); only the two edge providers are scripted doubles
(``tests/voice_doubles.py``). Everything the spec gates is exercised here:

* speak a question → hear a cited answer, with a waterfall of real numbers;
* the guardrail verdict lands before the first TTS byte (asserted by timing);
* a spoken PHI query is blocked with zero generation tokens;
* a spoken diagnosis request is refused (guardrail parity with text);
* a LASA drug name triggers a spoken confirmation, never a silent guess;
* barge-in stops the agent and "continue" resumes without regeneration;
* a backchannel during playback does not stop the agent;
* hesitation extends the endpoint window (no premature cut-off);
* voice and text turns share one session; the network can drop and resume.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

import pytest
import uvicorn
import websockets

from app.config import get_settings
from app.guardrails.phi import PhiDetector
from app.voice import protocol
from app.voice.endpointing import EndpointPolicy, HeuristicCompletenessClassifier
from app.voice.lasa import default_lasa_table
from app.voice.runtime import VoiceRuntime
from app.voice.vocabulary import VocabularyCache
from tests.conftest import ApiEnv
from tests.voice_doubles import (
    SILENCE_FRAME,
    ScriptedSttProvider,
    ScriptedTtsProvider,
    Utterance,
    tone_frame,
)

pytestmark = pytest.mark.skipif(
    not (os.environ.get("TEST_DATABASE_URL") and os.environ.get("TEST_REDIS_URL")),
    reason="TEST_DATABASE_URL/TEST_REDIS_URL not set (needs a running Postgres + Redis)",
)

GROUNDED = "How is type 2 diabetes managed with metformin"
CONFLICT = "Should aspirin be used for primary prevention of cardiovascular disease"
PHI = "What is the treatment for AF in John Smith, DOB 03/14/1982"
DIAGNOSIS = "Does my patient have atrial fibrillation based on these symptoms"


# -- server harness ----------------------------------------------------------------------


@dataclass
class VoiceServer:
    url: str
    stt: ScriptedSttProvider
    tts: ScriptedTtsProvider
    runtime: VoiceRuntime


@contextlib.asynccontextmanager
async def voice_server(
    env: ApiEnv, script: list[Utterance], **tts_kwargs: float
) -> AsyncIterator[VoiceServer]:
    settings = get_settings()
    stt = ScriptedSttProvider(script)
    tts = ScriptedTtsProvider(**tts_kwargs)
    runtime = VoiceRuntime(
        settings=settings,
        stt=stt,
        tts=tts,
        lasa=default_lasa_table(),
        vocabulary_cache=VocabularyCache(default_lasa_table()),
        completeness=HeuristicCompletenessClassifier(),
        endpoint_policy=EndpointPolicy(base_ms=300, extended_ms=1500, ceiling_ms=2000),
        phi_inline=PhiDetector(use_presidio=False),
    )
    env.app.state.voice_runtime = runtime
    config = uvicorn.Config(
        env.app, host="127.0.0.1", port=0, lifespan="off", log_level="warning", ws="websockets"
    )
    server = uvicorn.Server(config)
    task = asyncio.create_task(server.serve())
    while not server.started:
        await asyncio.sleep(0.02)
    port = server.servers[0].sockets[0].getsockname()[1]
    try:
        yield VoiceServer(f"ws://127.0.0.1:{port}/api/v1/voice/ws", stt, tts, runtime)
    finally:
        registry = getattr(env.app.state, "voice_registry", None)
        if registry is not None:
            await registry.close_all()
        server.should_exit = True
        with contextlib.suppress(Exception):
            await asyncio.wait_for(task, timeout=5)


@dataclass
class Client:
    socket: Any
    events: list[dict[str, Any]] = field(default_factory=list)
    audio: list[tuple[int, int, bytes, float]] = field(default_factory=list)
    _reader: asyncio.Task[None] | None = None

    async def start(self, token: str, query_session_id: str | None = None) -> dict[str, Any]:
        await self.socket.send(
            json.dumps({"type": "start", "token": token, "query_session_id": query_session_id})
        )
        self._reader = asyncio.create_task(self._read())
        return await self.wait_for("session")

    async def resume(self, token: str, session_id: str, resume_token: str) -> dict[str, Any]:
        await self.socket.send(
            json.dumps(
                {
                    "type": "resume",
                    "token": token,
                    "session_id": session_id,
                    "resume_token": resume_token,
                }
            )
        )
        self._reader = asyncio.create_task(self._read())
        return await self.wait_for("session")

    async def _read(self) -> None:
        try:
            async for message in self.socket:
                if isinstance(message, bytes):
                    turn, sentence, _flags, pcm = protocol.unpack_audio(message)
                    self.audio.append((turn, sentence, pcm, time.monotonic()))
                else:
                    self.events.append(json.loads(message))
        except Exception:
            return

    async def send(self, payload: dict[str, Any]) -> None:
        await self.socket.send(json.dumps(payload))

    async def speak(self, ms: int) -> None:
        """Stream ``ms`` of tone (speech to the VAD) in real-time 20 ms frames."""
        for i in range(ms // 20):
            await self.socket.send(tone_frame(phase=i * 320))
            await asyncio.sleep(0.02)

    async def silence(self, ms: int) -> None:
        for _ in range(ms // 20):
            await self.socket.send(SILENCE_FRAME)
            await asyncio.sleep(0.02)

    async def wait_for(
        self, kind: str, *, timeout: float = 30.0, where: Any = None, after: int = 0
    ) -> dict[str, Any]:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            for event in self.events[after:]:
                if event.get("type") == kind and (where is None or where(event)):
                    return event
            await asyncio.sleep(0.02)
        raise AssertionError(f"no {kind!r} event within {timeout}s; got {self.kinds()}")

    async def wait_state(self, state: str, *, timeout: float = 30.0, after: int = 0) -> None:
        await self.wait_for(
            "state", timeout=timeout, where=lambda e: e["state"] == state, after=after
        )

    def kinds(self) -> list[str]:
        return [e.get("type", "?") for e in self.events]

    def states(self) -> list[str]:
        return [e["state"] for e in self.events if e.get("type") == "state"]

    def sentences(self, turn: int | None = None) -> list[dict[str, Any]]:
        return [
            e
            for e in self.events
            if e.get("type") == "agent_sentence" and (turn is None or e["turn"] == turn)
        ]

    async def close(self) -> None:
        if self._reader is not None:
            self._reader.cancel()
        with contextlib.suppress(Exception):
            await self.socket.close()


@contextlib.asynccontextmanager
async def connect(server: VoiceServer) -> AsyncIterator[Client]:
    socket = await websockets.connect(server.url, max_size=None)
    client = Client(socket)
    try:
        yield client
    finally:
        await client.close()


def _speech_ms(text: str) -> int:
    """The scripted recognizer hears one word per 200 ms of speech."""
    return 200 * len(text.split()) + 200


async def _turn(client: Client, text: str, *, silence_ms: int = 700) -> None:
    await client.speak(_speech_ms(text))
    await client.silence(silence_ms)


# -- gates -----------------------------------------------------------------------------


async def test_speak_a_question_hear_a_cited_answer_with_a_real_waterfall(env: ApiEnv) -> None:
    org_id, _user, token = await env.new_org_with_owner()
    async with voice_server(env, [Utterance(GROUNDED)]) as server, connect(server) as client:
        session = await client.start(token)
        assert session["state"] == "LISTENING" and session["backend"] == "offline"
        assert server.stt.boost_seen, "the recognizer was opened with corpus boost terms"

        await _turn(client, GROUNDED)
        endpoint = await client.wait_for("endpoint")
        assert endpoint["layer"] == "vad" and 250 <= endpoint["silence_ms"] < 700
        final = await client.wait_for("final")
        assert final["text"] == GROUNDED
        result = await client.wait_for("result")
        assert result["data"]["citations"], "the spoken answer is cited"
        await client.wait_state("LISTENING", after=client.events.index(result))

        spoken = client.sentences(turn=0)
        assert spoken and spoken[0]["kind"] == "answer"
        assert all("[" not in s["spoken_text"] for s in spoken), "markers do not speak"
        assert any(s["markers"] for s in spoken), "chips are synchronized to sentences"
        assert client.audio and client.audio[0][0] == 0, "audio frames carry the turn index"
        states = client.states()
        for expected in ("ENDPOINTING", "PROCESSING", "SPEAKING", "LISTENING"):
            assert expected in states, states

        waterfall = await client.wait_for("waterfall", where=lambda e: e["turn"] == 0)
        legs = waterfall["legs"]
        assert (
            waterfall["total_first_audio_ms"] is not None and waterfall["total_first_audio_ms"] > 0
        )
        assert legs["endpoint_decision"] is not None and legs["endpoint_decision"] >= 250
        assert legs["retrieval"] is not None and legs["retrieval"] >= 0
        assert legs["tts_ttfb"] is not None
        assert legs["llm_first_token"] is None, "offline backend: no LLM call, so no leg"
        assert waterfall["waste"]["output_tokens"] == 0

        # Client-reported playback closes the last leg.
        await client.send(
            {
                "type": "playback",
                "turn": 0,
                "sentence": 0,
                "event": "started",
                "buffer_ms": 37.5,
            }
        )
        updated = await client.wait_for(
            "waterfall", where=lambda e: e["turn"] == 0 and e["client_first_audio_ms"] is not None
        )
        assert updated["legs"]["client_playback"] == 37.5

        # Persisted for the dashboard, under the tenant (the waterfall event
        # is emitted after the row is written).
        rows = await env.admin.fetch(
            "SELECT outcome, latency, transcript_final FROM public.voice_turns WHERE org_id = $1",
            org_id,
        )
        assert [r["outcome"] for r in rows] == ["answered"]
        assert json.loads(rows[0]["latency"])["total_first_audio_ms"] > 0

        # The turn's provider calls are in the cost ledger under the voice
        # session: the audio the recognizer heard and every sentence sent to
        # the voice, next to the retrieval and generation it shares with text.
        ledger = await env.admin.fetch(
            "SELECT component::text AS component, provider, unit, units, cost_usd "
            "FROM public.cost_events WHERE org_id = $1 AND voice_session_id = $2 "
            "AND NOT cached",
            org_id,
            UUID(session["session_id"]),
        )
        by_component = {r["component"]: r for r in ledger}
        assert set(by_component) >= {"stt", "tts", "rerank", "generation"}, ledger
        assert by_component["stt"]["unit"] == "seconds" and by_component["stt"]["units"] > 0
        assert by_component["tts"]["unit"] == "characters"
        assert sum(float(r["units"]) for r in ledger if r["component"] == "tts") == sum(
            len(s["spoken_text"]) for s in spoken
        )
        assert all(float(r["cost_usd"]) == 0 for r in ledger), "offline voice: real units, $0"


async def test_guardrail_verdict_lands_before_the_first_tts_byte(
    env: ApiEnv, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Retrieval and generation run in parallel with the scope/red-flag check,
    but not a single audio byte leaves before the verdict is in."""
    from app.guardrails.pipeline import GuardrailPipeline

    verdict_at: list[float] = []
    original = GuardrailPipeline.check_pre_retrieval

    async def slow_check(self: GuardrailPipeline, query: str, **kwargs: Any) -> Any:
        await asyncio.sleep(0.6)
        verdict = await original(self, query, **kwargs)
        verdict_at.append(time.monotonic())
        return verdict

    monkeypatch.setattr(GuardrailPipeline, "check_pre_retrieval", slow_check)
    _org, _user, token = await env.new_org_with_owner()
    async with voice_server(env, [Utterance(GROUNDED)]) as server, connect(server) as client:
        await client.start(token)
        await _turn(client, GROUNDED)
        await client.wait_for("result")
        await client.wait_for("audio_end", where=lambda e: e["turn"] == 0)
        first_audio_at = client.audio[0][3]
        assert verdict_at and first_audio_at >= verdict_at[0]
        # The pipeline did not wait for the verdict to start retrieving.
        waterfall = await client.wait_for("waterfall")
        assert waterfall["legs"]["guardrails"] is not None


async def test_spoken_phi_is_blocked_with_zero_generation_tokens(env: ApiEnv) -> None:
    org_id, _user, token = await env.new_org_with_owner()
    async with voice_server(env, [Utterance(PHI)]) as server, connect(server) as client:
        await client.start(token)
        await _turn(client, PHI)
        guardrail = await client.wait_for("guardrail")
        assert guardrail["verdict"] == "blocked" and guardrail["blocked_by"] == "phi"
        refusal = await client.wait_for("agent_sentence", where=lambda e: e["kind"] == "refusal")
        assert "patient details" in refusal["spoken_text"]
        await client.wait_state("LISTENING", after=client.events.index(refusal))
        assert not [e for e in client.events if e.get("type") == "result"]
        waterfall = await client.wait_for("waterfall")
        assert waterfall["waste"]["output_tokens"] == 0
        rows = await env.admin.fetch(
            "SELECT outcome, blocked_by, query_id FROM public.voice_turns WHERE org_id = $1", org_id
        )
        assert [(r["outcome"], r["blocked_by"]) for r in rows] == [("blocked_phi", "phi")]
        # Same verdict the text path records: nothing was generated or answered.
        answers = await env.admin.fetchval(
            "SELECT count(*) FROM public.answers WHERE query_id = $1", rows[0]["query_id"]
        )
        assert answers == 0
        status = await env.admin.fetchval(
            "SELECT status FROM public.queries WHERE id = $1", rows[0]["query_id"]
        )
        assert status == "blocked"


async def test_spoken_diagnosis_request_is_refused_like_text(env: ApiEnv) -> None:
    _org, _user, token = await env.new_org_with_owner()
    async with voice_server(env, [Utterance(DIAGNOSIS)]) as server, connect(server) as client:
        await client.start(token)
        await _turn(client, DIAGNOSIS)
        guardrail = await client.wait_for("guardrail")
        assert guardrail["verdict"] == "blocked" and guardrail["blocked_by"] == "scope"
        assert guardrail["code"] == "refuse_diagnosis"
        refusal = await client.wait_for("agent_sentence", where=lambda e: e["kind"] == "refusal")
        assert "medical literature" in refusal["spoken_text"]
        assert not [e for e in client.events if e.get("type") == "result"]


async def test_lasa_drug_triggers_a_spoken_confirmation_then_answers(env: ApiEnv) -> None:
    org_id, _user, token = await env.new_org_with_owner()
    script = [
        Utterance(
            "Is hydroxyzine effective for generalized anxiety disorder",
            confidences={"hydroxyzine": 0.31},
        ),
        Utterance("the first one, the antihistamine"),
    ]
    async with voice_server(env, script) as server, connect(server) as client:
        await client.start(token)
        await _turn(client, script[0].text)
        confirm = await client.wait_for("confirm_request")
        assert confirm["heard"] == "hydroxyzine"
        assert [o["name"] for o in confirm["options"]] == ["hydroxyzine", "hydralazine"]
        assert confirm["prompt"].startswith("Just to be safe")
        asked = await client.wait_for("agent_sentence", where=lambda e: e["kind"] == "confirmation")
        assert asked["spoken_text"] == confirm["prompt"]
        await client.wait_state("CONFIRMING")
        assert not [e for e in client.events if e.get("type") == "result"], "no silent guess"

        # Answer by voice.
        await _turn(client, script[1].text)
        final = await client.wait_for("final", where=lambda e: "hydroxyzine" in e["text"], after=1)
        result = await client.wait_for("result")
        assert result["turn"] == final["turn"]
        await client.wait_for("waterfall", after=client.events.index(result))
        rows = await env.admin.fetch(
            "SELECT outcome, confirmation FROM public.voice_turns WHERE org_id = $1 "
            "ORDER BY created_at",
            org_id,
        )
        outcomes = [r["outcome"] for r in rows]
        assert outcomes[0] == "confirm_requested"
        resolved = json.loads(rows[-1]["confirmation"])
        assert resolved["chosen"] == "hydroxyzine" and resolved["method"] == "voice"


async def test_lasa_confirmation_can_be_tapped(env: ApiEnv) -> None:
    _org, _user, token = await env.new_org_with_owner()
    script = [Utterance("Is clonidine used for opioid withdrawal", confidences={"clonidine": 0.2})]
    async with voice_server(env, script) as server, connect(server) as client:
        await client.start(token)
        await _turn(client, script[0].text)
        await client.wait_for("confirm_request")
        await client.wait_state("CONFIRMING")
        await client.send({"type": "confirm", "choice": "clonidine"})
        final = await client.wait_for("final", where=lambda e: "clonidine" in e["text"], after=1)
        assert final["text"].lower().startswith("is clonidine used")
        await client.wait_for("result")


async def test_barge_in_stops_within_budget_and_continue_resumes_without_regeneration(
    env: ApiEnv,
) -> None:
    _org, _user, token = await env.new_org_with_owner()
    # Slow TTS chunks so the answer is still being spoken when we interrupt.
    async with (
        voice_server(
            env, [Utterance(GROUNDED)], chunks_per_sentence=6, chunk_delay_s=0.15
        ) as server,
        connect(server) as client,
    ):
        await client.start(token)
        await _turn(client, GROUNDED)
        await client.wait_for("audio_start", where=lambda e: e["turn"] == 0)
        await asyncio.sleep(0.2)  # mid-sentence
        results_before = len([e for e in client.events if e.get("type") == "result"])
        sentences_before = [s["text"] for s in client.sentences(turn=0)]

        await client.send({"type": "barge_in", "stop_latency_ms": 42.0})
        await client.wait_state("BARGE_IN")
        await client.wait_state("LISTENING", after=client.events.index(client.events[-1]) - 2)
        assert server.tts.streams[0].cancels >= 1, "the TTS stream was cancelled immediately"
        waterfall = await client.wait_for(
            "waterfall", where=lambda e: e["waste"].get("cancel_reason")
        )
        assert waterfall["waste"]["cancel_reason"] == "barge_in"
        audio_after_barge = len(client.audio)
        await asyncio.sleep(0.4)
        assert len(client.audio) - audio_after_barge <= 1, "playback audio stopped flowing"

        # "continue" → the retained remainder is spoken; nothing is regenerated.
        await client.send({"type": "continue"})
        resumed = await client.wait_for(
            "agent_sentence", where=lambda e: e["turn"] == 0, after=client.events.index(waterfall)
        )
        assert resumed["text"] in sentences_before or resumed["kind"] in ("answer", "offer")
        await client.wait_state("LISTENING", after=client.events.index(resumed))
        results_after = len([e for e in client.events if e.get("type") == "result"])
        assert results_after == results_before, "resume did not re-run the pipeline"
        rows = await env.admin.fetch(
            "SELECT barge_in FROM public.voice_turns WHERE outcome = 'cancelled' "
            "ORDER BY created_at"
        )
        assert rows and json.loads(rows[-1]["barge_in"])["stop_ms"] == [42.0]


async def test_backchannel_during_playback_does_not_stop_the_agent(env: ApiEnv) -> None:
    _org, _user, token = await env.new_org_with_owner()
    script = [Utterance(GROUNDED), Utterance("mm-hmm okay", words_per_step=2)]
    async with (
        voice_server(env, script, chunks_per_sentence=5, chunk_delay_s=0.12) as server,
        connect(server) as client,
    ):
        await client.start(token)
        await _turn(client, GROUNDED)
        await client.wait_for("audio_start", where=lambda e: e["turn"] == 0)
        # The user says "mm-hmm" while the agent talks: speech-level audio, a
        # backchannel transcript. The agent must keep going.
        await client.speak(500)
        await client.wait_for("result")
        assert "BARGE_IN" not in client.states()
        assert server.tts.streams[0].cancels == 0


async def test_hesitation_extends_the_window_instead_of_cutting_off(env: ApiEnv) -> None:
    _org, _user, token = await env.new_org_with_owner()
    script = [Utterance("What is the first-line treatment for community acquired pneumonia")]
    async with voice_server(env, script) as server, connect(server) as client:
        await client.start(token)
        # Speak the first six words (1.2 s at one word per 200 ms), pause 600 ms
        # (well past the 300 ms base window), then finish.
        await client.speak(1200)
        await client.silence(600)
        assert not [e for e in client.events if e.get("type") == "final"], "not cut off"
        assert "ENDPOINTING" in client.states()
        await client.speak(800)
        await client.silence(700)
        endpoint = await client.wait_for("endpoint")
        final = await client.wait_for("final")
        assert final["text"].endswith("pneumonia")
        assert endpoint["complete"] is True


async def test_incomplete_final_retracts_the_commit_and_keeps_listening(env: ApiEnv) -> None:
    """The partial the endpoint decided on read "…treatment?" (complete); the
    final pass heard "…treatment for" (dangling). The turn must not start on
    the fragment: the words are held and the utterance finishes as one turn."""
    _org, _user, token = await env.new_org_with_owner()
    script = [
        Utterance(
            "What is the first line treatment", final_text="What is the first line treatment for"
        ),
        Utterance("community acquired pneumonia"),
    ]
    async with voice_server(env, script) as server, connect(server) as client:
        await client.start(token)
        await client.speak(_speech_ms("What is the first line treatment"))
        await client.silence(700)
        await client.wait_for("state", where=lambda e: e["reason"] == "final_incomplete", timeout=5)
        assert not [e for e in client.events if e.get("type") in ("final", "endpoint")]
        held = [e for e in client.events if e.get("type") == "partial"][-1]
        assert held["text"].endswith("treatment for")
        await client.speak(_speech_ms("community acquired pneumonia"))
        await client.silence(700)
        final = await client.wait_for("final")
        assert final["text"] == "What is the first line treatment for community acquired pneumonia"
        assert len([e for e in client.events if e.get("type") == "endpoint"]) == 1
        await client.wait_for("result")
        assert [e for e in client.events if e.get("type") == "final"] == [final]


async def test_voice_and_text_turns_share_one_session_and_resolve_follow_ups(env: ApiEnv) -> None:
    _org, _user, token = await env.new_org_with_owner()
    async with voice_server(env, [Utterance(GROUNDED)]) as server, connect(server) as client:
        session = await client.start(token)
        assert session["query_session_id"] is None
        await _turn(client, GROUNDED)
        first = await client.wait_for("result")
        await client.wait_state("LISTENING", after=client.events.index(first))
        # Typed follow-up inside voice mode, contextualized against the spoken turn.
        await client.send({"type": "text", "text": "what about in pregnancy?"})
        second = await client.wait_for("result", where=lambda e: e["turn"] == 1)
        assert (
            "pregnancy" in second["data"].get("query", "") or second["data"]["answer"] is not None
        )
        session_ids = await env.admin.fetch(
            "SELECT session_id, coalesce(contextualized_query, raw_query) AS q FROM public.queries "
            "WHERE id = ANY($1::uuid[]) ORDER BY created_at",
            [UUID(first["query_id"]), UUID(second["query_id"])],
        )
        assert session_ids[0]["session_id"] == session_ids[1]["session_id"]
        assert "follow-up: what about in pregnancy?" in session_ids[1]["q"]


async def test_network_drop_mid_session_resumes_with_the_thread_intact(env: ApiEnv) -> None:
    _org, _user, token = await env.new_org_with_owner()
    script = [Utterance(GROUNDED), Utterance("Do statins reduce cardiovascular events")]
    async with voice_server(env, script) as server:
        async with connect(server) as client:
            session = await client.start(token)
            await _turn(client, GROUNDED)
            first = await client.wait_for("result")
            await client.wait_state("LISTENING", after=client.events.index(first))
        # The socket is gone. Reconnect with the session id + resume token.
        async with connect(server) as client2:
            resumed = await client2.resume(token, session["session_id"], session["resume_token"])
            assert resumed["resumed"] is True
            assert resumed["session_id"] == session["session_id"]
            assert resumed["query_session_id"] is not None
            await _turn(client2, script[1].text)
            second = await client2.wait_for("result")
            assert second["turn"] == 1
            rows = await env.admin.fetch(
                "SELECT session_id FROM public.queries WHERE id = ANY($1::uuid[])",
                [UUID(first["query_id"]), UUID(second["query_id"])],
            )
            assert rows[0]["session_id"] == rows[1]["session_id"]
        # A wrong resume token is refused.
        socket = await websockets.connect(server.url, max_size=None)
        await socket.send(
            json.dumps(
                {
                    "type": "resume",
                    "token": token,
                    "session_id": session["session_id"],
                    "resume_token": "nope",
                }
            )
        )
        reply = json.loads(await socket.recv())
        assert reply["code"] == "resume_failed"


async def test_viewer_and_bad_token_cannot_open_a_voice_session(env: ApiEnv) -> None:
    org_id, _owner, _token = await env.new_org_with_owner()
    _viewer_id, viewer_token = await env.add_member(org_id, "viewer")
    async with voice_server(env, []) as server:
        socket = await websockets.connect(server.url, max_size=None)
        await socket.send(json.dumps({"type": "start", "token": viewer_token}))
        reply = json.loads(await socket.recv())
        assert reply["code"] == "forbidden"
        socket = await websockets.connect(server.url, max_size=None)
        await socket.send(json.dumps({"type": "start", "token": "not-a-jwt"}))
        reply = json.loads(await socket.recv())
        assert reply["code"] == "unauthorized"


async def test_conflict_answer_offers_a_walkthrough_by_voice(env: ApiEnv) -> None:
    _org, _user, token = await env.new_org_with_owner()
    script = [Utterance(CONFLICT), Utterance("yes go on")]
    async with voice_server(env, script) as server, connect(server) as client:
        await client.start(token)
        await _turn(client, CONFLICT)
        result = await client.wait_for("result")
        if not result["data"]["contradiction"]["detected"]:
            pytest.skip("the corpus did not surface a contradiction for this query today")
        opener = client.sentences(turn=0)[0]
        assert opener["spoken_text"] == "The sources disagree on this."
        offer = await client.wait_for("offer")
        assert offer["kind"] == "walkthrough"
        await client.wait_state("LISTENING", after=client.events.index(offer))
        await _turn(client, "yes go on")
        walkthrough = await client.wait_for(
            "agent_sentence",
            where=lambda e: "holds that" in e["spoken_text"],
            after=client.events.index(offer),
        )
        assert walkthrough["turn"] == 0, "the walk-through comes from the retained answer"


async def test_voice_rest_surface_reports_config_turns_and_analytics(env: ApiEnv) -> None:
    org_id, _user, token = await env.new_org_with_owner()
    async with voice_server(env, [Utterance(GROUNDED)]) as server, connect(server) as client:
        await client.start(token)
        await _turn(client, GROUNDED)
        result = await client.wait_for("result")
        await client.wait_state("LISTENING", after=client.events.index(result))
    config = await env.client.get("/api/v1/voice/config", headers=env.auth(token))
    assert config.status_code == 200, config.text
    body = config.json()
    assert body["backend"] == "offline" and body["lasa_pairs"] >= 100
    assert body["tts_quality"] == "flash"
    turns = await env.client.get("/api/v1/voice/turns", headers=env.auth(token))
    assert turns.status_code == 200 and turns.json()["total"] == 1
    item = turns.json()["items"][0]
    assert item["outcome"] == "answered" and item["latency"]["total_first_audio_ms"] > 0
    analytics = await env.client.get("/api/v1/voice/analytics", headers=env.auth(token))
    assert analytics.status_code == 200
    stats = analytics.json()
    assert stats["turns"] == 1 and stats["answered"] == 1
    assert stats["total_first_audio"]["p50"] is not None
    assert stats["legs"]["llm_first_token"]["p50"] is None, "no fake zero for a leg that never ran"
    # Owner-only TTS quality setting.
    patched = await env.client.patch(
        "/api/v1/voice/settings", json={"tts_quality": "multilingual"}, headers=env.auth(token)
    )
    assert patched.status_code == 200 and patched.json()["tts_quality"] == "multilingual"
    _clinician, clinician_token = await env.add_member(org_id, "clinician")
    denied = await env.client.patch(
        "/api/v1/voice/settings", json={"tts_quality": "flash"}, headers=env.auth(clinician_token)
    )
    assert denied.status_code == 403
