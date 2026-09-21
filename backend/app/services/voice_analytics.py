"""Voice aggregates for the dashboard (11G.5) — computed from voice_turns rows.

Every figure is derived from what the sessions actually recorded; a metric
with no data is ``None``, never 0.0.
"""

from __future__ import annotations

from typing import Any

from app.schemas.voice import LegStats, VoiceAnalyticsOut
from app.voice.waterfall import LEG_ORDER, aggregate, percentile


def _leg(stats: dict[str, Any]) -> LegStats:
    return LegStats(p50=stats.get("p50"), p95=stats.get("p95"), n=int(stats.get("n", 0)))


def aggregate_voice(rows: list[dict[str, Any]], *, days: int) -> VoiceAnalyticsOut:
    waterfalls = [r.get("latency") or {} for r in rows]
    agg = aggregate([w for w in waterfalls if isinstance(w, dict)])

    outcomes = [str(r.get("outcome")) for r in rows]
    fired = hits = wasted = 0
    confirm_requested = by_voice = by_tap = 0
    barge_count = 0
    barge_stops: list[float] = []
    masks = 0
    wasted_tokens = 0
    wasted_audio = 0.0
    corrections = 0
    by_backend: dict[str, int] = {}
    for row in rows:
        by_backend[str(row.get("backend"))] = by_backend.get(str(row.get("backend")), 0) + 1
        spec = row.get("speculation") or {}
        if isinstance(spec, dict) and spec.get("fired"):
            fired += 1
            if spec.get("hit"):
                hits += 1
            if spec.get("wasted"):
                wasted += 1
        confirmation = row.get("confirmation") or {}
        if isinstance(confirmation, dict) and confirmation.get("requested"):
            if row.get("outcome") == "confirm_requested":
                confirm_requested += 1
            method = confirmation.get("method")
            if method == "voice":
                by_voice += 1
            elif method == "tap":
                by_tap += 1
        barge = row.get("barge_in") or {}
        if isinstance(barge, dict):
            barge_count += int(barge.get("count") or 0)
            barge_stops.extend(float(v) for v in barge.get("stop_ms") or [] if v is not None)
        if row.get("mask_used"):
            masks += 1
        waste = row.get("waste") or {}
        if isinstance(waste, dict):
            wasted_tokens += int(waste.get("wasted_output_tokens") or 0)
            wasted_audio += float(waste.get("wasted_audio_ms") or 0.0)
        fixes = row.get("corrections") or []
        if isinstance(fixes, list):
            corrections += len(fixes)

    return VoiceAnalyticsOut(
        days=days,
        turns=len(rows),
        answered=outcomes.count("answered"),
        blocked=outcomes.count("blocked_phi") + outcomes.count("blocked_scope"),
        abstained=outcomes.count("abstained"),
        legs={leg: _leg(agg["legs"][leg]) for leg in LEG_ORDER},
        total_first_audio=_leg(agg["total_first_audio"]),
        client_first_audio=_leg(agg["client_first_audio"]),
        speculation_fired=fired,
        speculation_hits=hits,
        speculation_wasted=wasted,
        speculation_hit_rate=round(hits / fired, 3) if fired else None,
        speculation_wasted_rate=round(wasted / fired, 3) if fired else None,
        confirmations_requested=confirm_requested,
        confirmations_resolved_by_voice=by_voice,
        confirmations_resolved_by_tap=by_tap,
        barge_ins=barge_count,
        barge_in_stop_ms=LegStats(
            p50=percentile(barge_stops, 0.5), p95=percentile(barge_stops, 0.95), n=len(barge_stops)
        ),
        masks_used=masks,
        wasted_output_tokens=wasted_tokens,
        wasted_audio_ms=round(wasted_audio, 1),
        corrections_made=corrections,
        by_backend=by_backend,
    )
