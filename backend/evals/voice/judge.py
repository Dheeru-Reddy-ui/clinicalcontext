"""The turn-quality rubric (11H.5): LLM-as-Judge over spoken transcripts.

Scores three things per answered turn — did the agent lead with the answer,
attribute sources naturally, and disclose appropriately — using the
versioned prompt ``voice_turn_judge``. When no usable Anthropic key is
configured the judge does not run, and the results say so explicitly
(``ran: false``) instead of carrying an invented score.
"""

from __future__ import annotations

import json
import re
from typing import Any

import structlog

from app.config import get_settings
from app.prompts.loader import load_prompt

logger = structlog.stdlib.get_logger("evals.voice.judge")

_MODEL = "claude-sonnet-4-6"
_CRITERIA = ("leads_with_answer", "attributes_naturally", "discloses_appropriately")


def _key_usable() -> bool:
    key = get_settings().anthropic_api_key.get_secret_value()
    return bool(key) and not key.startswith(("placeholder", "test-", "your-"))


async def judge_turn(
    question: str, spoken_sentences: list[str], sources: list[str]
) -> dict[str, Any]:
    from anthropic import AsyncAnthropic

    prompt = load_prompt("voice_turn_judge", 1)
    user = (
        f"QUESTION: {question}\n\nSPOKEN ANSWER:\n"
        + "\n".join(f"- {s}" for s in spoken_sentences)
        + "\n\nSOURCES:\n"
        + "\n".join(f"- {s}" for s in sources)
    )
    client = AsyncAnthropic(api_key=get_settings().anthropic_api_key.get_secret_value())
    response = await client.messages.create(
        model=_MODEL,
        max_tokens=200,
        temperature=0.0,
        system=prompt.text,
        messages=[{"role": "user", "content": user}],
    )
    raw = "".join(b.text for b in response.content if b.type == "text")
    match = re.search(r"\{.*\}", raw, re.DOTALL)
    if not match:
        raise ValueError(f"judge returned no JSON: {raw[:120]!r}")
    payload: dict[str, Any] = json.loads(match.group())
    return {c: int(payload.get(c, 0)) for c in _CRITERIA} | {"notes": str(payload.get("notes", ""))}


async def judge_all(turns: list[dict[str, Any]]) -> dict[str, Any]:
    """→ {ran, prompt_version, model, scored, mean_scores | reason}."""
    if not _key_usable():
        return {
            "ran": False,
            "reason": "ANTHROPIC_API_KEY is a placeholder — the rubric needs a real key",
            "prompt_version": "voice_turn_judge.v1",
        }
    scored: list[dict[str, Any]] = []
    for turn in turns:
        try:
            result = await judge_turn(turn["question"], turn["spoken"], turn["sources"])
        except Exception as exc:
            logger.warning("judge_failed", fixture=turn.get("id"), error=type(exc).__name__)
            continue
        scored.append({"id": turn.get("id"), **result})
    means = {
        c: round(sum(s[c] for s in scored) / len(scored), 3) if scored else None for c in _CRITERIA
    }
    return {
        "ran": True,
        "prompt_version": "voice_turn_judge.v1",
        "model": _MODEL,
        "scored": len(scored),
        "mean_scores": means,
        "per_turn": scored,
    }
