"""The load-test user (Phase 13): a clinician session against the public API.

Each simulated user asks clinical questions from the golden set through
``POST /api/v1/queries`` — the streaming endpoint the product uses — reads
the whole SSE stream, and now and then opens their history. The latency
recorded for a question is the time to the ``result`` event, not the time
to the first byte, because that is what the user waits for.

Answers served by the semantic cache are recorded under their own name
("ask (cache)") so the pipeline's numbers are never flattered by cache hits:
a pool of 165 questions and dozens of users repeat questions quickly, and
the split shows exactly how much of the traffic each path carried.

Run through ``python -m evals.load.run`` (which seeds a tenant and writes
``evals/results/load.json``), or directly with Locust for an interactive
session:

    CC_LOAD_API_KEY=... locust -f evals/load/locustfile.py --host http://127.0.0.1:8010
"""

from __future__ import annotations

import json
import os
import random
import time
from pathlib import Path
from typing import Any

import requests
from locust import User, between, task

GOLDEN_SET = Path(__file__).resolve().parents[1] / "golden" / "set.jsonl"
STREAM_TIMEOUT = (5.0, float(os.environ.get("CC_LOAD_STREAM_TIMEOUT", "120")))


def load_questions(path: Path = GOLDEN_SET) -> list[str]:
    """The golden set's answerable questions — real clinical traffic shape,
    and the same questions the eval harness scores."""
    questions: list[str] = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            item = json.loads(line)
            if not item.get("expected_abstain"):
                questions.append(item["question"])
    if not questions:
        raise RuntimeError(f"no questions in {path}")
    return questions


QUESTIONS = load_questions()
# Time to the first stream event of each successful ask (ms): what the UI
# needs before it can show "accepted". Kept apart from the request stats so
# it never inflates the request counts; the runner drains it per stage.
FIRST_EVENT_MS: list[float] = []


class HttpFailure(Exception):
    def __init__(self, status: int, body: str) -> None:
        super().__init__(f"HTTP {status}: {body[:120]}")
        self.status = status


class ClinicalUser(User):
    """One clinician: mostly asking, sometimes reading."""

    wait_time = between(0.5, 2.0)
    abstract = False

    def on_start(self) -> None:
        self.session = requests.Session()
        key = os.environ.get("CC_LOAD_API_KEY")
        if not key:
            raise RuntimeError("CC_LOAD_API_KEY is not set (python -m evals.load.run sets it)")
        self.headers = {"X-API-Key": key, "Accept": "text/event-stream"}

    def _fire(
        self,
        method: str,
        name: str,
        started: float,
        length: int,
        exc: BaseException | None,
        context: dict[str, Any] | None = None,
    ) -> None:
        self.environment.events.request.fire(
            request_type=method,
            name=name,
            response_time=(time.perf_counter() - started) * 1000,
            response_length=length,
            exception=exc,
            context=context or {},
        )

    @task(4)
    def ask(self) -> None:
        question = random.choice(QUESTIONS)
        started = time.perf_counter()
        first_event_at: float | None = None
        length = 0
        result: dict[str, Any] | None = None
        blocked = False
        exc: BaseException | None = None
        try:
            with self.session.post(
                f"{self.host}/api/v1/queries",
                json={"query": question},
                headers=self.headers,
                stream=True,
                timeout=STREAM_TIMEOUT,
            ) as response:
                if response.status_code != 200:
                    raise HttpFailure(response.status_code, response.text)
                for raw in response.iter_lines():
                    length += len(raw) + 1
                    if not raw.startswith(b"data: "):
                        continue
                    if first_event_at is None:
                        first_event_at = time.perf_counter()
                    event = json.loads(raw[len(b"data: ") :])
                    stage = event.get("stage")
                    if stage == "result":
                        result = event
                    elif stage == "blocked":
                        blocked = True
                    elif stage == "error":
                        raise RuntimeError(f"stream error: {event.get('message')}")
        except Exception as error:  # every failure mode is a failed request
            exc = error
        if exc is None and result is None and not blocked:
            exc = RuntimeError("stream ended without a result")
        if exc is not None:
            name = "ask (failed)"
        elif blocked:
            name = "ask (blocked)"
        elif result is not None and result.get("data", {}).get("cached"):
            name = "ask (cache)"
        else:
            name = "ask (pipeline)"
        self._fire("POST", name, started, length, exc)
        if first_event_at is not None and exc is None:
            FIRST_EVENT_MS.append((first_event_at - started) * 1000)

    @task(1)
    def history(self) -> None:
        started = time.perf_counter()
        exc: BaseException | None = None
        length = 0
        try:
            response = self.session.get(
                f"{self.host}/api/v1/sessions",
                params={"limit": "8", "mine": "true"},
                headers=self.headers,
                timeout=(5.0, 30.0),
            )
            length = len(response.content)
            if response.status_code != 200:
                raise HttpFailure(response.status_code, response.text)
        except Exception as error:
            exc = error
        self._fire("GET", "history", started, length, exc)
