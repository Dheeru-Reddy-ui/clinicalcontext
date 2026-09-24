"""A chat model behind a chain of free providers.

Every provider here speaks the OpenAI chat-completions API — Groq, Cerebras,
OpenRouter, a local vLLM or Ollama — so one client covers all of them. The
chain exists because free tiers are generous per day and stingy per minute:
Groq's free gpt-oss-120b allows 8,000 tokens a minute, which one long answer
can use up. A provider that refuses (429, 5xx, timeout, bad key) hands the
same request to the next, and only when every one has refused does the
caller fall back to the offline engine. A partially streamed answer is never
restarted elsewhere: its tokens have already been shown.

Keys are read from settings and only a real-looking key enables a provider
(placeholders from CI and .env.example do not).
"""

from __future__ import annotations

import json
import time
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass
from typing import Any, Literal

import httpx
import structlog

from app.config import Settings, get_settings
from app.services import cost
from app.voice.availability import key_configured

logger = structlog.stdlib.get_logger("app.llm.chat")

Role = Literal["system", "user", "assistant"]
Effort = Literal["low", "medium", "high"]

GROQ_BASE_URL = "https://api.groq.com/openai/v1"
CEREBRAS_BASE_URL = "https://api.cerebras.ai/v1"

# Statuses that mean "this provider cannot serve the request right now" — the
# next provider may. 400 is included because a strict endpoint may reject a
# parameter another accepts; it is retried once without the optional ones.
_HAND_OFF = frozenset({401, 402, 403, 404, 408, 409, 413, 422, 429, 500, 502, 503, 504})


@dataclass(frozen=True, slots=True)
class ChatMessage:
    role: Role
    content: str

    def as_dict(self) -> dict[str, str]:
        return {"role": self.role, "content": self.content}


@dataclass(frozen=True, slots=True)
class ChatProvider:
    name: str
    base_url: str
    api_key: str
    model: str

    @property
    def reasons(self) -> bool:
        """gpt-oss models think before answering and take an effort level."""
        return "gpt-oss" in self.model


@dataclass(slots=True)
class ChatResult:
    text: str
    provider: str
    model: str
    input_tokens: int
    output_tokens: int
    latency_ms: int


class LLMUnavailable(Exception):
    """No configured provider could serve the request."""

    def __init__(self, attempts: list[str]) -> None:
        self.attempts = attempts
        super().__init__("; ".join(attempts) or "no language model is configured")


def configured_providers(settings: Settings | None = None) -> list[ChatProvider]:
    """The providers with real keys, in the configured order."""
    settings = settings or get_settings()
    by_name: dict[str, list[ChatProvider]] = {}

    groq_key = settings.groq_api_key.get_secret_value()
    if key_configured(groq_key):
        groq = [ChatProvider("groq", GROQ_BASE_URL, groq_key, settings.groq_model)]
        if settings.groq_fallback_model and settings.groq_fallback_model != settings.groq_model:
            groq.append(ChatProvider("groq", GROQ_BASE_URL, groq_key, settings.groq_fallback_model))
        by_name["groq"] = groq

    cerebras_key = settings.cerebras_api_key.get_secret_value()
    if key_configured(cerebras_key):
        by_name["cerebras"] = [
            ChatProvider("cerebras", CEREBRAS_BASE_URL, cerebras_key, settings.cerebras_model)
        ]

    compat_key = settings.openai_compat_api_key.get_secret_value()
    if settings.openai_compat_base_url and settings.openai_compat_model:
        # A local server needs no key; a hosted one does.
        local = "localhost" in settings.openai_compat_base_url or "127.0.0.1" in (
            settings.openai_compat_base_url
        )
        if local or key_configured(compat_key):
            by_name["openai_compat"] = [
                ChatProvider(
                    "openai_compat",
                    settings.openai_compat_base_url.rstrip("/"),
                    compat_key,
                    settings.openai_compat_model,
                )
            ]

    ordered: list[ChatProvider] = []
    for name in (n.strip() for n in settings.llm_provider_order.split(",")):
        ordered.extend(by_name.pop(name, []))
    for rest in by_name.values():  # configured but unlisted: still usable, last
        ordered.extend(rest)
    return ordered


def _estimate_tokens(text: str) -> int:
    return max(1, len(text) // 4)


class ChatModel:
    """Completion and streaming over the provider chain."""

    def __init__(
        self,
        providers: Sequence[ChatProvider] | None = None,
        *,
        timeout: float | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._providers = list(providers) if providers is not None else configured_providers()
        self._timeout = timeout if timeout is not None else get_settings().llm_timeout_seconds
        self._transport = transport

    @property
    def available(self) -> bool:
        return bool(self._providers)

    @property
    def providers(self) -> list[ChatProvider]:
        return list(self._providers)

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            timeout=httpx.Timeout(self._timeout, connect=10.0), transport=self._transport
        )

    @staticmethod
    def _body(
        provider: ChatProvider,
        messages: Sequence[ChatMessage],
        *,
        max_tokens: int,
        temperature: float,
        effort: Effort,
        stream: bool,
        json_mode: bool,
        minimal: bool,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {
            "model": provider.model,
            "messages": [m.as_dict() for m in messages],
            "stream": stream,
        }
        if minimal:
            body["max_tokens"] = max_tokens
            return body
        body["temperature"] = temperature
        # Reasoning tokens count against the completion budget: leave room
        # for them so the answer itself is never cut short.
        body["max_completion_tokens"] = max_tokens + (1024 if provider.reasons else 0)
        if provider.reasons:
            body["reasoning_effort"] = effort
        if json_mode:
            body["response_format"] = {"type": "json_object"}
        return body

    @staticmethod
    def _headers(provider: ChatProvider) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if provider.api_key:
            headers["Authorization"] = f"Bearer {provider.api_key}"
        return headers

    @staticmethod
    def _record(provider: ChatProvider, input_tokens: int, output_tokens: int) -> None:
        cost.record(
            "generation",
            provider=provider.name,
            model=provider.model,
            units=input_tokens,
            unit="tokens",
            output_units=output_tokens,
        )

    async def complete(
        self,
        messages: Sequence[ChatMessage],
        *,
        max_tokens: int = 1024,
        temperature: float = 0.2,
        effort: Effort = "low",
        json_mode: bool = False,
    ) -> ChatResult:
        attempts: list[str] = []
        async with self._client() as client:
            for provider in self._providers:
                for minimal in (False, True):
                    started = time.perf_counter()
                    try:
                        response = await client.post(
                            f"{provider.base_url}/chat/completions",
                            headers=self._headers(provider),
                            json=self._body(
                                provider,
                                messages,
                                max_tokens=max_tokens,
                                temperature=temperature,
                                effort=effort,
                                stream=False,
                                json_mode=json_mode,
                                minimal=minimal,
                            ),
                        )
                    except httpx.HTTPError as exc:
                        attempts.append(f"{provider.name}/{provider.model}: {type(exc).__name__}")
                        break
                    if response.status_code == 400 and not minimal:
                        continue  # once more without the optional parameters
                    if response.status_code != 200:
                        attempts.append(
                            f"{provider.name}/{provider.model}: HTTP {response.status_code}"
                        )
                        logger.warning(
                            "llm_provider_refused",
                            provider=provider.name,
                            model=provider.model,
                            status=response.status_code,
                            detail=response.text[:300],
                        )
                        break
                    payload = response.json()
                    choice = (payload.get("choices") or [{}])[0]
                    text = str((choice.get("message") or {}).get("content") or "").strip()
                    if not text:
                        attempts.append(f"{provider.name}/{provider.model}: empty answer")
                        break
                    usage = payload.get("usage") or {}
                    input_tokens = int(usage.get("prompt_tokens") or 0) or sum(
                        _estimate_tokens(m.content) for m in messages
                    )
                    output_tokens = int(usage.get("completion_tokens") or 0) or _estimate_tokens(
                        text
                    )
                    self._record(provider, input_tokens, output_tokens)
                    return ChatResult(
                        text=text,
                        provider=provider.name,
                        model=provider.model,
                        input_tokens=input_tokens,
                        output_tokens=output_tokens,
                        latency_ms=int((time.perf_counter() - started) * 1000),
                    )
        raise LLMUnavailable(attempts)

    async def complete_json(
        self,
        messages: Sequence[ChatMessage],
        *,
        max_tokens: int = 800,
        effort: Effort = "low",
    ) -> dict[str, Any]:
        """A completion parsed as one JSON object (fenced or bare)."""
        result = await self.complete(
            messages, max_tokens=max_tokens, temperature=0.0, effort=effort, json_mode=True
        )
        return parse_json_object(result.text)

    async def stream(
        self,
        messages: Sequence[ChatMessage],
        *,
        max_tokens: int = 1400,
        temperature: float = 0.2,
        effort: Effort = "low",
    ) -> AsyncIterator[str | ChatResult]:
        """Text deltas, then one :class:`ChatResult` with the whole answer.

        A provider that refuses before its first token is skipped for the
        next; one that fails after it ends the answer where it stopped."""
        attempts: list[str] = []
        async with self._client() as client:
            for provider in self._providers:
                for minimal in (False, True):
                    started = time.perf_counter()
                    pieces: list[str] = []
                    usage: dict[str, Any] = {}
                    try:
                        async with client.stream(
                            "POST",
                            f"{provider.base_url}/chat/completions",
                            headers=self._headers(provider),
                            json=self._body(
                                provider,
                                messages,
                                max_tokens=max_tokens,
                                temperature=temperature,
                                effort=effort,
                                stream=True,
                                json_mode=False,
                                minimal=minimal,
                            ),
                        ) as response:
                            if response.status_code == 400 and not minimal:
                                await response.aread()
                                continue
                            if response.status_code != 200:
                                detail = (await response.aread()).decode("utf-8", "replace")
                                attempts.append(
                                    f"{provider.name}/{provider.model}: HTTP {response.status_code}"
                                )
                                logger.warning(
                                    "llm_provider_refused",
                                    provider=provider.name,
                                    model=provider.model,
                                    status=response.status_code,
                                    detail=detail[:300],
                                )
                                break
                            async for line in response.aiter_lines():
                                delta, chunk_usage = _parse_stream_line(line)
                                if chunk_usage:
                                    usage = chunk_usage
                                if delta:
                                    pieces.append(delta)
                                    yield delta
                    except httpx.HTTPError as exc:
                        if not pieces:
                            attempts.append(
                                f"{provider.name}/{provider.model}: {type(exc).__name__}"
                            )
                            break
                        logger.warning(
                            "llm_stream_cut",
                            provider=provider.name,
                            error=f"{type(exc).__name__}: {exc}",
                        )
                    text = "".join(pieces).strip()
                    if not text:
                        attempts.append(f"{provider.name}/{provider.model}: empty answer")
                        break
                    input_tokens = int(usage.get("prompt_tokens") or 0) or sum(
                        _estimate_tokens(m.content) for m in messages
                    )
                    output_tokens = int(usage.get("completion_tokens") or 0) or _estimate_tokens(
                        text
                    )
                    self._record(provider, input_tokens, output_tokens)
                    yield ChatResult(
                        text=text,
                        provider=provider.name,
                        model=provider.model,
                        input_tokens=input_tokens,
                        output_tokens=output_tokens,
                        latency_ms=int((time.perf_counter() - started) * 1000),
                    )
                    return
        raise LLMUnavailable(attempts)


def _parse_stream_line(line: str) -> tuple[str, dict[str, Any] | None]:
    """One server-sent line → (content delta, usage if this chunk carries it).

    Reasoning deltas (gpt-oss's ``reasoning`` field) are not content and are
    dropped: a reader sees the answer, not the model's scratch work."""
    if not line.startswith("data:"):
        return "", None
    data = line[5:].strip()
    if not data or data == "[DONE]":
        return "", None
    try:
        chunk = json.loads(data)
    except json.JSONDecodeError:
        return "", None
    usage = chunk.get("usage") or (chunk.get("x_groq") or {}).get("usage")
    choices = chunk.get("choices") or []
    delta = ""
    if choices:
        delta = str((choices[0].get("delta") or {}).get("content") or "")
    return delta, usage if isinstance(usage, dict) else None


def parse_json_object(text: str) -> dict[str, Any]:
    """The first JSON object in a model's reply (bare, or inside a fence)."""
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end <= start:
        raise ValueError("no JSON object in the reply")
    value = json.loads(text[start : end + 1])
    if not isinstance(value, dict):
        raise ValueError("the reply is not a JSON object")
    return value


def llm_available(settings: Settings | None = None) -> bool:
    return bool(configured_providers(settings))
