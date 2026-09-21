"""Confidence calibration: is "high" confidence right more often than
"moderate", and by how much?

Two reliability curves, one per outcome source, written to
evals/results/calibration.json and rendered on the dashboard and the
methodology page:

* **golden** — stated confidence against the judge's correctness verdict on
  the golden set when an LLM key is configured, otherwise against whether
  the answer cited a gold passage (``golden.json`` records which).
* **production** — stated confidence against thumbs feedback on real
  answers (up = right, down = wrong), read from the database.

Both report per-level buckets (predicted vs observed accuracy), the Brier
score against the nominal probabilities in
:data:`evals.golden.metrics.NOMINAL_CONFIDENCE`, and the expected
calibration error.

The retuning loop, documented on the methodology page: a level whose
observed accuracy sits more than :data:`DRIFT` below its nominal value on
at least :data:`MIN_BUCKET` answers is flagged, with the observed values
proposed as the next nominal table and the tightening to make in
``app.graph.graph._assess`` named. Thresholds are changed by a person in a
reviewed change, never by this script.

    uv run python -m evals.golden.calibrate
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import asyncpg

from app.config import get_settings
from evals.golden.metrics import NOMINAL_CONFIDENCE, calibrate
from evals.golden.run import DEFAULT_OUT as GOLDEN_RESULTS
from evals.golden.run import RESULTS_DIR

DEFAULT_OUT = RESULTS_DIR / "calibration.json"
DRIFT = 0.15
MIN_BUCKET = 10

_TIGHTEN = {
    "high": "require a top reranker score above the current 0.5 floor, or grade A only",
    "moderate": "require grade A/B *and* a recent source rather than either",
    "low": "no threshold to tighten: low is the floor",
}


async def production_outcomes(dsn: str) -> list[tuple[str, bool]]:
    """(stated confidence, thumbs up?) for every rated, non-abstained answer."""
    pool = await asyncpg.create_pool(dsn, min_size=1, max_size=1)
    try:
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT a.confidence::text AS confidence, f.rating::text AS rating
                FROM public.feedback f
                JOIN public.answers a ON a.id = f.answer_id
                WHERE a.confidence IS NOT NULL AND NOT a.abstained
                """
            )
    finally:
        await pool.close()
    return [(str(r["confidence"]), str(r["rating"]) == "up") for r in rows]


def recommendation(curves: dict[str, dict[str, Any]]) -> dict[str, Any]:
    reasons: list[str] = []
    proposed = dict(NOMINAL_CONFIDENCE)
    for source, curve in curves.items():
        for bucket in curve.get("buckets", []):
            observed = bucket.get("observed")
            if observed is None or bucket["n"] < MIN_BUCKET:
                continue
            if bucket["predicted"] - observed > DRIFT:
                reasons.append(
                    f"{source}: '{bucket['level']}' is stated as {bucket['predicted']:.0%} but "
                    f"observed {observed:.0%} on {bucket['n']} answers — "
                    f"{_TIGHTEN[bucket['level']]}"
                )
                proposed[bucket["level"]] = min(proposed[bucket["level"]], observed)
    return {
        "retune": bool(reasons),
        "drift_threshold": DRIFT,
        "min_bucket": MIN_BUCKET,
        "reasons": reasons,
        "nominal": dict(NOMINAL_CONFIDENCE),
        "proposed_nominal": {k: round(v, 4) for k, v in proposed.items()},
    }


async def build_report(golden_path: Path, *, dsn: str | None) -> dict[str, Any]:
    curves: dict[str, dict[str, Any]] = {}
    golden_note: str | None = None
    if golden_path.is_file():
        golden = json.loads(golden_path.read_text(encoding="utf-8"))
        curves["golden"] = dict(golden["calibration"]) | {
            "generated_at": golden.get("generated_at"),
            "backend": golden.get("backend"),
        }
    else:
        golden_note = f"{golden_path.name} not found — run evals.golden.run first"
    production_note: str | None = None
    if dsn:
        try:
            outcomes = await production_outcomes(dsn)
            curves["production"] = calibrate(outcomes).as_dict() | {"outcome": "thumbs_up"}
        except Exception as exc:  # the report still says what it could not read
            production_note = f"feedback unavailable: {type(exc).__name__}"
    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "curves": curves,
        "notes": [n for n in (golden_note, production_note) if n],
        "recommendation": recommendation(curves),
    }


def render(report: dict[str, Any]) -> str:
    lines = []
    for source, curve in report["curves"].items():
        lines.append(
            f"{source} (outcome={curve.get('outcome')}, n={curve.get('n')}): "
            f"brier {curve.get('brier')}  ece {curve.get('ece')}"
        )
        for b in curve.get("buckets", []):
            lines.append(
                f"   {b['level']:9s} predicted {b['predicted']:.2f}  observed "
                f"{'—' if b['observed'] is None else f'{b["observed"]:.2f}'}  n={b['n']}"
            )
    rec = report["recommendation"]
    lines.append("retune: " + ("YES" if rec["retune"] else "no"))
    lines.extend(f"   - {r}" for r in rec["reasons"])
    lines.extend(f"   note: {n}" for n in report["notes"])
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--golden", type=Path, default=GOLDEN_RESULTS)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--no-db", action="store_true", help="skip the production curve")
    args = parser.parse_args(argv)
    dsn = None if args.no_db else get_settings().database_url
    report = asyncio.run(build_report(args.golden, dsn=dsn))
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    print(render(report))
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())


__all__ = ["DRIFT", "MIN_BUCKET", "build_report", "production_outcomes", "recommendation"]
