"""Voice in the chat: a spoken question in, a spoken answer out.

The chat's voice mode is two plain requests around the ordinary chat — a
recording to /voice/transcribe, answer text to /voice/speak — so these run
against the real app with a scripted speaker, and the transcription test
runs the real local Whisper model on a real recording of speech.
"""

from __future__ import annotations

import importlib.util
import io
import os
import wave
from pathlib import Path

import pytest

from app.voice.stt.whisper_local import WhisperProvider
from tests.conftest import ApiEnv
from tests.voice_doubles import ScriptedSttProvider, ScriptedTtsProvider, scripted_runtime

pytestmark = pytest.mark.skipif(
    not (os.environ.get("TEST_DATABASE_URL") and os.environ.get("TEST_REDIS_URL")),
    reason="needs TEST_DATABASE_URL and TEST_REDIS_URL",
)

SPEECH = Path(__file__).resolve().parents[2] / "frontend/e2e/fixtures/audio/golden-01-padded.wav"


async def test_an_answer_is_spoken_as_a_playable_wav(env: ApiEnv) -> None:
    tts = ScriptedTtsProvider()
    env.app.state.voice_runtime = scripted_runtime(ScriptedSttProvider([]), tts)
    _org, user_id, token = await env.new_org_with_owner()
    await env.redis.delete(f"rl:speak:{user_id}")
    response = await env.client.post(
        "/api/v1/voice/speak",
        json={"text": "Doxycycline is the preferred first-line antibiotic."},
        headers=env.auth(token),
    )
    assert response.status_code == 200, response.text
    assert response.headers["content-type"] == "audio/wav"
    with wave.open(io.BytesIO(response.content)) as clip:
        assert (clip.getframerate(), clip.getnchannels(), clip.getsampwidth()) == (16000, 1, 2)
        assert clip.getnframes() > 0
    assert tts.streams[0].spoken == ["Doxycycline is the preferred first-line antibiotic."]


async def test_speaking_needs_a_session_and_sensible_text(env: ApiEnv) -> None:
    env.app.state.voice_runtime = scripted_runtime(ScriptedSttProvider([]), ScriptedTtsProvider())
    anonymous = await env.client.post("/api/v1/voice/speak", json={"text": "hello"})
    assert anonymous.status_code == 401
    _org, _user, token = await env.new_org_with_owner()
    too_long = await env.client.post(
        "/api/v1/voice/speak", json={"text": "x" * 1201}, headers=env.auth(token)
    )
    assert too_long.status_code == 422


async def test_a_server_that_cannot_speak_says_why(env: ApiEnv) -> None:
    class Mute(ScriptedTtsProvider):
        def unavailable_reason(self) -> str:
            return "No speech service is configured."

    env.app.state.voice_runtime = scripted_runtime(ScriptedSttProvider([]), Mute())
    _org, _user, token = await env.new_org_with_owner()
    response = await env.client.post(
        "/api/v1/voice/speak", json={"text": "hello"}, headers=env.auth(token)
    )
    assert response.status_code == 503
    assert "No speech service" in response.text


async def test_voice_has_its_own_rate_limit_bucket(env: ApiEnv) -> None:
    env.app.state.voice_runtime = scripted_runtime(ScriptedSttProvider([]), ScriptedTtsProvider())
    _org, user_id, token = await env.new_org_with_owner()
    await env.redis.set(f"rl:speak:{user_id}", 90, ex=60)
    limited = await env.client.post(
        "/api/v1/voice/speak", json={"text": "hello"}, headers=env.auth(token)
    )
    assert limited.status_code == 429
    # The plan's shared bucket is untouched: chat still works.
    status = await env.client.get("/api/v1/assistant/status", headers=env.auth(token))
    assert status.status_code == 200
    await env.redis.delete(f"rl:speak:{user_id}")


@pytest.mark.skipif(
    importlib.util.find_spec("faster_whisper") is None, reason="needs the local Whisper engine"
)
async def test_a_spoken_question_becomes_text_with_the_local_engine(env: ApiEnv) -> None:
    env.app.state.voice_runtime = scripted_runtime(
        WhisperProvider(model="tiny.en"), ScriptedTtsProvider()
    )
    _org, user_id, token = await env.new_org_with_owner()
    await env.redis.delete(f"rl:transcribe:{user_id}")
    response = await env.client.post(
        "/api/v1/voice/transcribe",
        content=SPEECH.read_bytes(),
        headers={**env.auth(token), "Content-Type": "audio/wav"},
    )
    assert response.status_code == 200, response.text
    text = response.json()["text"].lower()
    assert "metformin" in text or "diabetes" in text, text
