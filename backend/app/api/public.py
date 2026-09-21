"""Unauthenticated, read-only surface — deliberately outside ``/api/v1``.

Every ``/api/v1`` route requires a credential (enforced by a test sweep). The
few things a logged-out reader may see live here, and each one enforces its
own gates explicitly rather than relying on RLS, because there is no tenant
context to run under.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request

from app.core.deps import DbPool, get_asyncpg_pool, get_redis_client
from app.core.errors import NotFoundError, RateLimitError
from app.core.ratelimit import RateLimiter
from app.schemas.demo import DemoAnswerOut, DemoQuestionsOut, DemoRequest
from app.schemas.sharing import PublicAnswerOut
from app.services.demo import DEMO_QUESTIONS, DemoService, demo_question
from app.services.sharing import SharingService

router = APIRouter(prefix="/api/public", tags=["public"])

# Anonymous traffic is limited per client address — generous for humans,
# enough to stop slug enumeration.
_ANON_PER_MINUTE = 120
# The demo runs the real pipeline: a few per visitor per minute, and a
# ceiling across all visitors so the marketing page can never be used to
# load the API.
_DEMO_PER_MINUTE = 6
_DEMO_GLOBAL_PER_MINUTE = 60


async def _anon_rate_limit(
    request: Request, redis: Annotated[Any, Depends(get_redis_client)]
) -> None:
    client = request.client.host if request.client else "unknown"
    ok, retry_after = await RateLimiter(redis).hit(f"rl:anon:{client}", _ANON_PER_MINUTE)
    if not ok:
        raise RateLimitError("too many requests", retry_after=retry_after)


@router.get("/answers/{slug}", dependencies=[Depends(_anon_rate_limit)])
async def public_answer(
    slug: str,
    pool: Annotated[DbPool, Depends(get_asyncpg_pool)],
) -> PublicAnswerOut:
    """A shared answer, or 404 — disabled links and disabled orgs look identical."""
    return await SharingService(pool).resolve(slug)


_EVAL_RESULTS = Path(__file__).resolve().parents[2] / "evals" / "results"
_PUBLIC_EVALS = {
    "voice": "voice.json",
    "adversarial": "adversarial_results.json",
    "golden": "golden.json",
    "ablation": "ablation.json",
    "calibration": "calibration.json",
    "retrieval_ablation": "retrieval_ablation.json",
    "chunking_ablation": "chunking_ablation.json",
    "load": "load.json",
}
# golden.json carries every per-item answer; the public surface serves the
# aggregates only (the items stay in the repository for anyone who wants them).
_STRIP = {"golden": ("items",)}


@router.get("/evals/{name}", dependencies=[Depends(_anon_rate_limit)])
async def public_eval(name: str) -> dict[str, Any]:
    """A committed eval result file, as written by its runner — the methodology
    page reads these live rather than restating numbers by hand."""
    filename = _PUBLIC_EVALS.get(name)
    if filename is None:
        raise NotFoundError(f"unknown eval {name!r}")
    path = _EVAL_RESULTS / filename
    if not path.is_file():
        raise NotFoundError(f"eval {name!r} has not been run")
    payload: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    for key in _STRIP.get(name, ()):
        payload.pop(key, None)
    return payload


async def _demo_rate_limit(
    request: Request, redis: Annotated[Any, Depends(get_redis_client)]
) -> None:
    client = request.client.host if request.client else "unknown"
    limiter = RateLimiter(redis)
    ok, retry_after = await limiter.hit(f"rl:demo:{client}", _DEMO_PER_MINUTE)
    if not ok:
        raise RateLimitError("demo limit reached — try again in a minute", retry_after=retry_after)
    ok, retry_after = await limiter.hit("rl:demo:all", _DEMO_GLOBAL_PER_MINUTE)
    if not ok:
        raise RateLimitError("the demo is busy — try again in a minute", retry_after=retry_after)


@router.get("/demo/questions", dependencies=[Depends(_anon_rate_limit)])
async def demo_questions() -> DemoQuestionsOut:
    """The questions the public demo can be asked (the only input it accepts)."""
    return DemoQuestionsOut(questions=DEMO_QUESTIONS)


@router.post("/demo", dependencies=[Depends(_demo_rate_limit)])
async def demo_ask(
    body: DemoRequest,
    pool: Annotated[DbPool, Depends(get_asyncpg_pool)],
    redis: Annotated[Any, Depends(get_redis_client)],
) -> DemoAnswerOut:
    """Run one allowlisted question through the real pipeline as the demo
    tenant — the answer, its citations, and a contradiction when the sources
    disagree — with no login."""
    question = demo_question(body.question_id)
    if question is None:
        raise NotFoundError(f"unknown demo question {body.question_id!r}")
    return await DemoService(pool, redis).ask(question)
