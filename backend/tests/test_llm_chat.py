"""The free-model chain: one OpenAI-compatible client, many free providers.

Free tiers refuse by the minute (429) long before they run out by the day,
so what matters is that a refusal moves the request to the next provider,
that a stream already showing tokens is never restarted elsewhere, that
reasoning text never leaks into an answer, and that placeholder keys never
enable a provider. Every provider here is a MockTransport; nothing leaves
the machine.
"""

from __future__ import annotations

import json

import httpx
import pytest

from app.config import Settings
from app.llm.chat import (
    CEREBRAS_BASE_URL,
    GROQ_BASE_URL,
    ChatMessage,
    ChatModel,
    ChatProvider,
    ChatResult,
    LLMUnavailable,
    configured_providers,
    parse_json_object,
)

REAL_LOOKING = "llm-key-for-tests-0123456789abcdef"
GROQ = ChatProvider("groq", GROQ_BASE_URL, REAL_LOOKING, "openai/gpt-oss-120b")
CEREBRAS = ChatProvider("cerebras", CEREBRAS_BASE_URL, REAL_LOOKING, "gpt-oss-120b")
ASK = [ChatMessage("system", "Answer briefly."), ChatMessage("user", "What is metformin?")]


def _settings(monkeypatch: pytest.MonkeyPatch, **env: str) -> Settings:
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    return Settings(_env_file=None)


def _completion(text: str, usage: dict[str, int] | None = None) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "choices": [{"message": {"role": "assistant", "content": text, "reasoning": "hmm"}}],
            "usage": usage or {"prompt_tokens": 20, "completion_tokens": 5},
        },
    )


def _sse(*chunks: dict[str, object]) -> bytes:
    lines = [f"data: {json.dumps(c)}\n\n" for c in chunks]
    return ("".join(lines) + "data: [DONE]\n\n").encode()


# -- which providers exist ---------------------------------------------------------------


def test_no_keys_means_no_providers(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _settings(monkeypatch, GROQ_API_KEY="", CEREBRAS_API_KEY="placeholder")
    assert configured_providers(settings) == []


def test_groq_brings_its_second_model_as_a_second_quota(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _settings(monkeypatch, GROQ_API_KEY=REAL_LOOKING, CEREBRAS_API_KEY=REAL_LOOKING)
    chain = configured_providers(settings)
    assert [(p.name, p.model) for p in chain] == [
        ("groq", "openai/gpt-oss-120b"),
        ("groq", "openai/gpt-oss-20b"),
        ("cerebras", "gpt-oss-120b"),
    ]


def test_the_order_is_configurable(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _settings(
        monkeypatch,
        GROQ_API_KEY=REAL_LOOKING,
        CEREBRAS_API_KEY=REAL_LOOKING,
        LLM_PROVIDER_ORDER="cerebras,groq",
    )
    assert configured_providers(settings)[0].name == "cerebras"


def test_a_local_openai_compatible_server_needs_no_key(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _settings(
        monkeypatch,
        OPENAI_COMPAT_BASE_URL="http://localhost:11434/v1/",
        OPENAI_COMPAT_MODEL="llama3.2",
    )
    (provider,) = configured_providers(settings)
    assert provider.base_url == "http://localhost:11434/v1"
    assert provider.api_key == ""


# -- completion ------------------------------------------------------------------------


async def test_a_completion_returns_the_answer_not_the_reasoning() -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["body"] = json.loads(request.content)
        seen["auth"] = request.headers.get("authorization")
        return _completion("Metformin is a biguanide.")

    result = await ChatModel([GROQ], transport=httpx.MockTransport(handler)).complete(ASK)
    assert result.text == "Metformin is a biguanide."
    assert (result.provider, result.input_tokens, result.output_tokens) == ("groq", 20, 5)
    assert seen["url"] == f"{GROQ_BASE_URL}/chat/completions"
    assert seen["auth"] == f"Bearer {REAL_LOOKING}"
    body = seen["body"]
    assert isinstance(body, dict)
    assert body["reasoning_effort"] == "low"
    # Room for the model's thinking on top of the answer's own budget.
    assert body["max_completion_tokens"] > 1024


async def test_a_rate_limited_provider_hands_the_request_to_the_next() -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.host)
        if request.url.host == "api.groq.com":
            return httpx.Response(429, json={"error": {"message": "rate limit"}})
        return _completion("From Cerebras.")

    result = await ChatModel([GROQ, CEREBRAS], transport=httpx.MockTransport(handler)).complete(ASK)
    assert result.text == "From Cerebras." and result.provider == "cerebras"
    assert calls == ["api.groq.com", "api.cerebras.ai"]


async def test_a_rejected_parameter_is_retried_without_the_optional_ones() -> None:
    bodies: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        bodies.append(body)
        if "reasoning_effort" in body:
            return httpx.Response(400, json={"error": {"message": "unknown parameter"}})
        return _completion("Plain answer.")

    result = await ChatModel([GROQ], transport=httpx.MockTransport(handler)).complete(ASK)
    assert result.text == "Plain answer."
    assert "reasoning_effort" not in bodies[1] and bodies[1]["max_tokens"] == 1024


async def test_every_provider_refusing_raises_with_the_reasons() -> None:
    transport = httpx.MockTransport(lambda _r: httpx.Response(503))
    with pytest.raises(LLMUnavailable) as caught:
        await ChatModel([GROQ, CEREBRAS], transport=transport).complete(ASK)
    assert len(caught.value.attempts) == 2
    assert "HTTP 503" in str(caught.value)


async def test_an_empty_chain_is_unavailable_not_a_crash() -> None:
    with pytest.raises(LLMUnavailable):
        await ChatModel([]).complete(ASK)


# -- streaming -------------------------------------------------------------------------


async def test_a_stream_yields_answer_deltas_and_drops_reasoning_deltas() -> None:
    body = _sse(
        {"choices": [{"delta": {"reasoning": "Let me think"}}]},
        {"choices": [{"delta": {"content": "Metformin "}}]},
        {"choices": [{"delta": {"content": "is first-line."}}]},
        {"choices": [], "x_groq": {"usage": {"prompt_tokens": 30, "completion_tokens": 4}}},
    )
    transport = httpx.MockTransport(lambda _r: httpx.Response(200, content=body))
    items = [i async for i in ChatModel([GROQ], transport=transport).stream(ASK)]
    deltas = [i for i in items if isinstance(i, str)]
    (final,) = [i for i in items if isinstance(i, ChatResult)]
    assert deltas == ["Metformin ", "is first-line."]
    assert final.text == "Metformin is first-line."
    assert (final.input_tokens, final.output_tokens) == (30, 4)


async def test_a_stream_refused_before_its_first_token_moves_on() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.groq.com":
            return httpx.Response(429)
        return httpx.Response(200, content=_sse({"choices": [{"delta": {"content": "Hi."}}]}))

    items = [
        i
        async for i in ChatModel([GROQ, CEREBRAS], transport=httpx.MockTransport(handler)).stream(
            ASK
        )
    ]
    assert items[-1] == ChatResult(
        text="Hi.",
        provider="cerebras",
        model="gpt-oss-120b",
        input_tokens=items[-1].input_tokens,  # type: ignore[union-attr]
        output_tokens=items[-1].output_tokens,  # type: ignore[union-attr]
        latency_ms=items[-1].latency_ms,  # type: ignore[union-attr]
    )


# -- JSON replies ----------------------------------------------------------------------


def test_json_is_found_inside_a_fence() -> None:
    assert parse_json_object('```json\n{"a": 1}\n```') == {"a": 1}


def test_a_reply_without_json_is_an_error() -> None:
    with pytest.raises(ValueError):
        parse_json_object("no braces here")
