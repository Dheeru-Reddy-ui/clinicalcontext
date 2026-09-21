"""The full ablation: the golden set through every configuration, one
stage added at a time, writing evals/results/ablation.json.

    #   configuration
    1   fixed chunking, dense-only, no rerank          (the baseline)
    2   + structural chunking
    3   + hybrid retrieval (BM25 + RRF)
    4   + reranking
    5   + recency & evidence-grade boosts
    6   + query decomposition
    7   + retrieval grading & rewrite
    8   + contradiction detection
    9   + grounding verification
    10  + PICO-structured decomposition   (scored on the PICO-eligible subset,
                                           next to row 9 on the same subset)

Each row is a real run of the real pipeline (``evals.golden.run.run_config``)
with the stage switches on ``RetrievalConfig`` and ``GraphFeatures``. Rows
1-5 change what is retrieved; rows 6-10 change what the graph does with it.
recall@10 is scored against the golden set's labelled chunks (document level
too); faithfulness is the LLM judge's when a key is configured, otherwise
the lexical grounding verifier's support rate, and the report says which.
Row 1 needs the corpus chunked under ``fixed`` as well:

    uv run python -m app.ingestion.cli rechunk --strategy fixed --embedder local
    uv run python -m evals.ablation.full

The numbers are whatever they are. If a stage does not help on this backend,
that is the finding; the table is never edited by hand.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import asyncpg

from app.config import get_settings
from app.graph.graph import GraphFeatures
from evals.golden.run import (
    RESULTS_DIR,
    ItemRecord,
    RunConfig,
    backend_description,
    corpus_description,
    run_config,
    select_items,
    summarize,
)
from evals.golden.schema import SET_PATH, load_set

DEFAULT_OUT = RESULTS_DIR / "ablation.json"

_OFF = GraphFeatures(decompose=False, grade_and_rewrite=False, contradiction=False, grounding=False)


@dataclass(frozen=True, slots=True)
class Row:
    number: int
    description: str
    config: RunConfig
    subset: str | None = None  # None → every item; "pico" → PICO-eligible only


CONFIGS: list[Row] = [
    Row(
        1,
        "Fixed chunking, dense-only, no rerank",
        RunConfig(
            label="1",
            strategy="fixed",
            lexical=False,
            rerank=False,
            boosts=False,
            features=_OFF,
            pico=False,
        ),
    ),
    Row(
        2,
        "+ structural chunking",
        RunConfig(label="2", lexical=False, rerank=False, boosts=False, features=_OFF, pico=False),
    ),
    Row(
        3,
        "+ hybrid (BM25 + RRF)",
        RunConfig(label="3", rerank=False, boosts=False, features=_OFF, pico=False),
    ),
    Row(4, "+ rerank", RunConfig(label="4", boosts=False, features=_OFF, pico=False)),
    Row(5, "+ recency & evidence boosts", RunConfig(label="5", features=_OFF, pico=False)),
    Row(
        6,
        "+ query decomposition",
        RunConfig(
            label="6",
            features=GraphFeatures(grade_and_rewrite=False, contradiction=False, grounding=False),
            pico=False,
        ),
    ),
    Row(
        7,
        "+ retrieval grading & rewrite",
        RunConfig(
            label="7", features=GraphFeatures(contradiction=False, grounding=False), pico=False
        ),
    ),
    Row(
        8,
        "+ contradiction detection",
        RunConfig(label="8", features=GraphFeatures(grounding=False), pico=False),
    ),
    Row(9, "+ grounding verification", RunConfig(label="9", pico=False)),
    Row(
        10,
        "+ PICO-structured decomposition (PICO-eligible subset)",
        RunConfig(label="10", pico=True),
        subset="pico",
    ),
]


def _faithfulness(summary: dict[str, Any], judge: dict[str, Any]) -> tuple[float | None, str]:
    if judge.get("ran"):
        return judge["means"].get("faithfulness"), "judge"
    return summary["generation"]["grounding_support_mean"], "lexical grounding support"


def row_report(row: Row, records: list[ItemRecord], judge: dict[str, Any]) -> dict[str, Any]:
    summary = summarize(records, judge)
    faithfulness, source = _faithfulness(summary, judge)
    generation = summary["generation"]
    return {
        "config": row.number,
        "description": row.description,
        "subset": row.subset or "all",
        "items": len(records),
        "settings": row.config.as_dict(),
        "recall_at_10": summary["retrieval"]["chunks"]["recall_at_10"],
        "recall_at_10_documents": summary["retrieval"]["documents"]["recall_at_10"],
        "mrr": summary["retrieval"]["chunks"]["mrr"],
        "ndcg_at_10": summary["retrieval"]["chunks"]["ndcg_at_10"],
        "faithfulness": faithfulness,
        "faithfulness_source": source,
        "abstained_when_answerable": generation["abstained_when_answerable"],
        "cited_gold_rate": generation["cited_gold_rate"],
        "contradiction": generation["contradiction"],
        "retrieval_ms_p50": summary["retrieval"]["latency_ms"]["p50"],
        "graph_ms_p50": generation["latency_ms"]["p50"],
        "errors": generation["errors"],
        "judge": {k: v for k, v in judge.items() if k != "per_item"},
    }


async def run_all(args: argparse.Namespace) -> dict[str, Any]:
    items = select_items(
        load_set(args.set),
        categories=set(args.category) if args.category else None,
        ids=None,
        limit=args.limit,
    )
    pico_items = [i for i in items if i.pico_eligible]
    pool = await asyncpg.create_pool(get_settings().database_url, min_size=2, max_size=6)
    started = time.monotonic()
    rows: list[dict[str, Any]] = []
    try:
        corpus = await corpus_description(pool)
        async with pool.acquire() as conn:
            fixed_chunks = await conn.fetchval(
                "SELECT count(*) FROM public.chunks WHERE strategy = 'fixed' AND org_id IS NULL"
            )
        chosen = [r for r in CONFIGS if not args.config or r.number in set(args.config)]
        for row in chosen:
            if row.number == 1 and not fixed_chunks:
                rows.append(
                    {
                        "config": 1,
                        "description": row.description,
                        "skipped": "no fixed-strategy chunks: run "
                        "`python -m app.ingestion.cli rechunk --strategy fixed`",
                    }
                )
                continue
            subset = pico_items if row.subset == "pico" else items
            if not subset:
                rows.append(
                    {
                        "config": row.number,
                        "description": row.description,
                        "skipped": "empty subset",
                    }
                )
                continue
            print(f"config {row.number}: {row.description} ({len(subset)} items)", file=sys.stderr)
            records, judge = await run_config(
                subset, pool=pool, config=row.config, judge_disabled=args.no_judge
            )
            rows.append(row_report(row, records, judge))
            if row.subset == "pico":
                # The comparison row 10 is read against: row 9 on the same subset.
                reference = next((r for r in CONFIGS if r.number == 9), None)
                if reference is not None:
                    print(f"config 9 on the PICO subset ({len(subset)} items)", file=sys.stderr)
                    ref_records, ref_judge = await run_config(
                        subset, pool=pool, config=reference.config, judge_disabled=args.no_judge
                    )
                    rows.append(
                        row_report(
                            Row(9, reference.description, reference.config, subset="pico"),
                            ref_records,
                            ref_judge,
                        )
                    )
    finally:
        await pool.close()
    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "duration_s": round(time.monotonic() - started, 1),
        "backend": backend_description(),
        "corpus": corpus | {"fixed_chunks": fixed_chunks},
        "golden": {
            "set": args.set.name,
            "items": len(items),
            "pico_eligible": len(pico_items),
            "by_category": dict(Counter(i.category for i in items)),
        },
        "rows": rows,
    }


def render_markdown(report: dict[str, Any]) -> str:
    source = next(
        (r["faithfulness_source"] for r in report["rows"] if "faithfulness_source" in r), ""
    )
    lines = [
        f"Ablation · {report['golden']['items']} golden items · "
        f"{report['backend']['embedder']} embedder, {report['backend']['reranker']} reranker, "
        f"{report['backend']['reasoner']} reasoner · faithfulness = {source}",
        "",
        "| # | Configuration | recall@10 (chunks) | recall@10 (docs) | MRR | faithfulness | "
        "abstained (answerable) | cited gold |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for r in report["rows"]:
        if "skipped" in r:
            lines.append(
                f"| {r['config']} | {r['description']} | skipped: {r['skipped']} | | | | | |"
            )
            continue
        label = r["description"] + (f" [{r['subset']}]" if r["subset"] != "all" else "")
        lines.append(
            f"| {r['config']} | {label} | {r['recall_at_10']} | {r['recall_at_10_documents']} | "
            f"{r['mrr']} | {r['faithfulness']} | {r['abstained_when_answerable']} | "
            f"{r['cited_gold_rate']} |"
        )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--set", type=Path, default=SET_PATH)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--category", action="append")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--config", type=int, action="append", help="only these row numbers")
    parser.add_argument("--no-judge", action="store_true")
    args = parser.parse_args(argv)
    report = asyncio.run(run_all(args))
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    print(render_markdown(report))
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())


__all__ = ["CONFIGS", "Row", "render_markdown", "row_report", "run_all"]
