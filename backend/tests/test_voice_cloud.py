"""Voice on the free deployment: Deepgram for both halves, and honesty about
whether voice can run at all.

The first deployment's voice page connected, then the session died on start
(no speech engine in the image) while /voice/config reported a ready runtime.
These pin the pieces that replace that: a provider that says why it cannot
run, the Aura-2 speaker, and the keyterm budget Deepgram enforces.
"""

from __future__ import annotations

import json

import httpx
import pytest

from app.config import Settings
from app.voice.availability import CLOUD_SETUP, key_configured, provider_unavailable_reason
from app.voice.runtime import build_runtime
from app.voice.stt.deepgram import DeepgramProvider, select_keyterms
from app.voice.tts import get_tts_provider
from app.voice.tts.deepgram import DeepgramTtsProvider, DeepgramTtsStream

# Shaped like nothing real: the secret scanner must stay quiet.
REAL_LOOKING = "dg-key-for-tests-0123456789abcdef"


def _settings(monkeypatch: pytest.MonkeyPatch, **env: str) -> Settings:
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    return Settings(_env_file=None)


# -- what counts as a configured key -----------------------------------------------------


@pytest.mark.parametrize("value", ["", "placeholder", "ci", "ci-placeholder-x", "test-anything"])
def test_placeholder_keys_do_not_count(value: str) -> None:
    assert not key_configured(value)


def test_a_real_looking_key_counts() -> None:
    assert key_configured(REAL_LOOKING)


# -- availability ------------------------------------------------------------------------


def test_cloud_voice_without_a_deepgram_key_says_how_to_get_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings(monkeypatch, VOICE_BACKEND="cloud", DEEPGRAM_API_KEY="placeholder")
    runtime = build_runtime(settings)
    reason = runtime.unavailable_reason()
    assert reason == CLOUD_SETUP
    assert "deepgram.com" in reason and "no card" in reason


def test_cloud_voice_with_a_key_uses_deepgram_for_both_halves(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings(monkeypatch, VOICE_BACKEND="cloud", DEEPGRAM_API_KEY=REAL_LOOKING)
    runtime = build_runtime(settings)
    assert runtime.unavailable_reason() is None
    assert runtime.stt.name == "deepgram" and runtime.stt.model == "nova-3-medical"
    assert runtime.tts.name == "deepgram"
    assert isinstance(runtime.tts, DeepgramTtsProvider)


def test_elevenlabs_remains_selectable(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _settings(
        monkeypatch,
        VOICE_BACKEND="cloud",
        VOICE_TTS_PROVIDER="elevenlabs",
        ELEVENLABS_API_KEY="placeholder",
    )
    provider = get_tts_provider(settings)
    assert provider.name == "elevenlabs"
    assert "ELEVENLABS_API_KEY" in (provider_unavailable_reason(provider) or "")


def test_offline_voice_without_the_engine_says_so_rather_than_crashing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import importlib.util

    real_find_spec = importlib.util.find_spec

    def no_whisper(name: str, *args: object, **kwargs: object) -> object:
        return None if name == "faster_whisper" else real_find_spec(name, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(importlib.util, "find_spec", no_whisper)
    settings = _settings(monkeypatch, VOICE_BACKEND="offline")
    reason = build_runtime(settings).unavailable_reason()
    assert reason is not None
    assert "no speech-recognition engine" in reason
    assert "Deepgram" in reason  # and what to do about it


def test_a_provider_without_the_check_counts_as_available() -> None:
    # The scripted doubles the tests inject have no unavailable_reason().
    assert provider_unavailable_reason(object()) is None


# -- keyterms ----------------------------------------------------------------------------


def test_keyterms_stay_inside_deepgrams_token_limit_and_keep_rank_order() -> None:
    boost = [f"hydroxychloroquinesulfate{i}" for i in range(120)] + ["aspirin"]
    chosen = select_keyterms(boost)
    assert chosen == boost[: len(chosen)], "the ranked head of the list, in order"
    assert len(chosen) <= 50
    estimated = sum(max(1, -(-len(t) // 3)) for t in chosen)
    assert estimated <= 400, "Deepgram rejects more than 500 keyterm tokens"


def test_short_terms_fill_the_count_cap_not_the_token_cap() -> None:
    chosen = select_keyterms([f"drug{i}" for i in range(80)])
    assert len(chosen) == 50


async def test_the_listen_request_carries_the_budgeted_keyterms(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: dict[str, str] = {}

    class _Socket:
        async def send(self, _data: object) -> None: ...

        async def close(self) -> None: ...

        def __aiter__(self) -> _Socket:
            return self

        async def __anext__(self) -> str:
            raise StopAsyncIteration

    async def fake_connect(url: str, **kwargs: object) -> _Socket:
        seen["url"] = url
        seen["auth"] = str(kwargs.get("additional_headers"))
        return _Socket()

    monkeypatch.setattr("app.voice.stt.deepgram.websockets.connect", fake_connect)
    stream = await DeepgramProvider(api_key=REAL_LOOKING).open(
        boost=[f"term{i}" for i in range(200)]
    )
    await stream.close()
    assert seen["url"].count("keyterm=") == 50
    assert "model=nova-3-medical" in seen["url"]
    assert "Token " in seen["auth"]


# -- Aura-2 speaking ---------------------------------------------------------------------


def _stream_with(handler: object) -> DeepgramTtsStream:
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))  # type: ignore[arg-type]
    return DeepgramTtsStream(client, api_key=REAL_LOOKING, model="aura-2-thalia-en")


class _Chunked(httpx.AsyncByteStream):
    """A response body delivered in the given pieces, as a network would."""

    def __init__(self, pieces: list[bytes]) -> None:
        self._pieces = pieces

    async def __aiter__(self):  # type: ignore[override]
        for piece in self._pieces:
            yield piece


async def test_speech_is_requested_as_raw_16khz_pcm() -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["params"] = dict(request.url.params)
        seen["body"] = json.loads(request.content)
        seen["auth"] = request.headers.get("authorization")
        return httpx.Response(200, stream=_Chunked([b"\x01\x00\x02\x00"]))

    stream = _stream_with(handler)
    audio = b"".join([c async for c in stream.synthesize("Metformin is first line.")])
    await stream.close()
    assert audio == b"\x01\x00\x02\x00"
    assert seen["params"] == {
        "model": "aura-2-thalia-en",
        "encoding": "linear16",
        "sample_rate": "16000",
        "container": "none",
    }
    assert seen["body"] == {"text": "Metformin is first line."}
    assert seen["auth"] == f"Token {REAL_LOOKING}"


async def test_a_sample_split_across_network_chunks_is_not_misaligned() -> None:
    # Three 2-byte samples arriving as 3 + 1 + 2 bytes: a naive pass-through
    # would yield an odd chunk and shift every later sample into noise.
    pieces = [b"\x01\x00\x02", b"\x00", b"\x03\x00"]
    stream = _stream_with(lambda _r: httpx.Response(200, stream=_Chunked(pieces)))
    chunks = [c async for c in stream.synthesize("One two three.")]
    await stream.close()
    assert all(len(c) % 2 == 0 for c in chunks)
    assert b"".join(chunks) == b"\x01\x00\x02\x00\x03\x00"


async def test_barge_in_stops_the_audio_mid_sentence() -> None:
    pieces = [b"\x01\x00" * 4] * 10
    stream = _stream_with(lambda _r: httpx.Response(200, stream=_Chunked(pieces)))
    received: list[bytes] = []
    async for chunk in stream.synthesize("A long answer that is interrupted."):
        received.append(chunk)
        if len(received) == 2:
            await stream.cancel()
    await stream.close()
    assert len(received) == 2


async def test_a_refused_request_yields_silence_not_an_exception() -> None:
    stream = _stream_with(lambda _r: httpx.Response(401, json={"err_msg": "Invalid credentials"}))
    audio = [c async for c in stream.synthesize("Hello.")]
    await stream.close()
    assert audio == []
