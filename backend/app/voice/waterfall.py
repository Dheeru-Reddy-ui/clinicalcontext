"""Per-turn latency accounting — the waterfall (11.1, 11C.4, 11F.5).

A ``TurnClock`` records monotonic timestamps at each milestone; ``legs()``
turns them into the spec's waterfall legs (ms), measured from the moment the
user stopped speaking. Every value is either a real measurement or ``None`` —
a leg that did not happen (no LLM call in the offline backend, a blocked
turn) is reported as absent, never as zero.

    speech_end          the last speech frame (VAD) — t0 of the waterfall
    endpoint_commit     the turn-commit decision
    transcript_final    the final (corrected) transcript in hand
    guardrail_verdict   PHI/scope/red-flag verdict landed
    retrieval_done      chunks in hand (speculated or not)
    llm_first_token     first generation delta
    first_sentence      first speakable sentence complete (rendered)
    tts_first_byte      first TTS audio byte received from the provider
    first_audio_sent    first audio frame sent to the client
    client_first_audio  client-reported first audio out of the speaker

``aggregate`` produces p50/p95 per leg for the dashboard and voice.json.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

LEG_ORDER: tuple[str, ...] = (
    "endpoint_decision",
    "transcript_final",
    "guardrails",
    "retrieval",
    "llm_first_token",
    "first_sentence",
    "tts_ttfb",
    "client_playback",
)


def now_ms() -> float:
    return time.monotonic() * 1000.0


@dataclass(slots=True)
class TurnClock:
    marks: dict[str, float] = field(default_factory=dict)

    def mark(self, name: str, at: float | None = None) -> float:
        stamp = now_ms() if at is None else at
        # First mark wins: a milestone happens once per turn.
        self.marks.setdefault(name, stamp)
        return stamp

    def has(self, name: str) -> bool:
        return name in self.marks

    def since(self, start: str, end: str) -> float | None:
        if start in self.marks and end in self.marks:
            return round(self.marks[end] - self.marks[start], 1)
        return None

    def legs(self) -> dict[str, float | None]:
        """Sequential legs: each milestone measured from the latest earlier
        milestone *by the time it landed*, so parallel work (guardrails next
        to retrieval) is charged once and the legs add up to first audio."""
        m = self.marks
        mark_to_leg = {
            "endpoint_commit": "endpoint_decision",
            "transcript_final": "transcript_final",
            "guardrail_verdict": "guardrails",
            "retrieval_done": "retrieval",
            "llm_first_token": "llm_first_token",
            "first_sentence": "first_sentence",
            "tts_first_byte": "tts_ttfb",
        }
        legs: dict[str, float | None] = dict.fromkeys(LEG_ORDER)
        start = m.get("speech_end")
        if start is None:
            return legs
        stop = m.get("first_audio_sent")
        landed = sorted(
            (stamp, mark)
            for mark, stamp in m.items()
            if mark in mark_to_leg and stamp >= start and (stop is None or stamp <= stop)
        )
        previous = start
        for stamp, mark in landed:
            legs[mark_to_leg[mark]] = round(stamp - previous, 1)
            previous = stamp
        if "client_first_audio_ms" in m and stop is not None:
            legs["client_playback"] = round(m["client_first_audio_ms"], 1)
        return legs

    def total_first_audio_ms(self) -> float | None:
        return self.since("speech_end", "first_audio_sent")

    def client_first_audio_ms(self) -> float | None:
        server = self.total_first_audio_ms()
        client = self.marks.get("client_first_audio_ms")
        if server is None or client is None:
            return None
        return round(server + client, 1)


def percentile(values: list[float], pct: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return round(ordered[0], 1)
    rank = (len(ordered) - 1) * pct
    low = int(rank)
    high = min(low + 1, len(ordered) - 1)
    frac = rank - low
    return round(ordered[low] + (ordered[high] - ordered[low]) * frac, 1)


def aggregate(turns: list[dict[str, Any]]) -> dict[str, Any]:
    """p50/p95 per leg + totals over a list of waterfall dicts."""
    per_leg: dict[str, list[float]] = {leg: [] for leg in LEG_ORDER}
    totals: list[float] = []
    client_totals: list[float] = []
    for turn in turns:
        legs = turn.get("legs") or {}
        for leg in LEG_ORDER:
            value = legs.get(leg)
            if isinstance(value, int | float):
                per_leg[leg].append(float(value))
        total = turn.get("total_first_audio_ms")
        if isinstance(total, int | float):
            totals.append(float(total))
        client = turn.get("client_first_audio_ms")
        if isinstance(client, int | float):
            client_totals.append(float(client))
    return {
        "turns": len(turns),
        "legs": {
            leg: {"p50": percentile(v, 0.5), "p95": percentile(v, 0.95), "n": len(v)}
            for leg, v in per_leg.items()
        },
        "total_first_audio": {
            "p50": percentile(totals, 0.5),
            "p95": percentile(totals, 0.95),
            "n": len(totals),
        },
        "client_first_audio": {
            "p50": percentile(client_totals, 0.5),
            "p95": percentile(client_totals, 0.95),
            "n": len(client_totals),
        },
    }
