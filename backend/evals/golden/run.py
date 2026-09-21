"""Run the golden set through the real pipeline and write evals/results/golden.json.

Two passes per item, so a regression can be placed in the layer it came from:

1. **Retrieval, scored alone.** The retrieval pipeline (the production
   configuration, or an ablation configuration) ranks chunks for the question;
   recall@k, precision@k, MRR and nDCG@10 are computed against the item's
   labelled chunk ids (and, at document level, its labelled document ids)
   before any generation runs.
2. **The agent graph, end to end.** Guardrails, then the same graph the API
   serves. What comes out is scored three ways: deterministic signals that
   need no model (did it abstain when it should, did it cite a gold passage,
   did the evidence grade and contradiction flag match, what fraction of its
   clinical sentences the lexical grounding verifier could support); the
   RAGAS metrics and the clinical rubric when an LLM judge is available; and
   stated confidence against outcome for the calibration curve.

The Phase 7 adversarial set runs as a sibling in the same report, and the
voice harness's committed results are attached (or re-run with
``--voice run``). ``--gate`` fails the process on safety, and with a
``--baseline`` on a >2-point drop in recall@10 or faithfulness.

    uv run python -m evals.golden.run                       # full run, all gates
    uv run python -m evals.golden.run --gate safety --no-judge
    uv run python -m evals.golden.run --baseline evals/results/golden.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from statistics import mean
from typing import Any
from uuid import UUID

import asyncpg
import structlog

from app.config import get_settings
from app.core.deps import DbPool
from app.graph.graph import AgentGraph, GraphFeatures
from app.graph.reasoner import get_reasoner
from app.guardrails.grounding import verify_grounding
from app.guardrails.pipeline import GuardrailPipeline
from app.retrieval.embed import EmbeddingService
from app.retrieval.embedders import get_embedder
from app.retrieval.pipeline import RetrievalConfig, RetrievalPipeline
from app.retrieval.rerank import get_reranker
from app.retrieval.types import RetrievedChunk
from app.schemas.answer import AnswerResult
from app.services.ask import _backend_components
from evals.golden.judge import JudgeInput, judge_items, not_run_reason, resolve_judge
from evals.golden.metrics import RetrievalScores, calibrate, mean_scores
from evals.golden.schema import SET_PATH, GoldenItem, load_set

logger = structlog.stdlib.get_logger("evals.golden.run")

RESULTS_DIR = Path(__file__).resolve().parents[1] / "results"
DEFAULT_OUT = RESULTS_DIR / "golden.json"
VOICE_RESULTS = RESULTS_DIR / "voice.json"

REGRESSION_POINTS = 0.02  # a drop of more than two points fails the build
METRIC_TOP_K = 10
# Relevance can only be labelled for MeSH-indexed documents, so the ranking
# is scored over that universe: unindexed documents are skipped and the
# pipeline is asked for enough candidates that ten labelled ones remain.
METRIC_CANDIDATES = METRIC_TOP_K * 3


@dataclass(frozen=True, slots=True)
class RunConfig:
    """One configuration of the pipeline — production by default; the
    ablation runner builds the others."""

    label: str = "production"
    strategy: str = "structural"
    lexical: bool = True
    rerank: bool = True
    boosts: bool = True
    features: GraphFeatures = field(default_factory=GraphFeatures)
    pico: bool = True

    def retrieval(self, *, top_n: int) -> RetrievalConfig:
        return RetrievalConfig(
            strategy=self.strategy,
            rerank_top_n=top_n,
            lexical=self.lexical,
            rerank=self.rerank,
            boosts=self.boosts,
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "strategy": self.strategy,
            "lexical": self.lexical,
            "rerank": self.rerank,
            "boosts": self.boosts,
            "decompose": self.features.decompose,
            "grade_and_rewrite": self.features.grade_and_rewrite,
            "contradiction": self.features.contradiction,
            "grounding": self.features.grounding,
            "pico": self.pico,
        }


@dataclass(slots=True)
class ItemRecord:
    item: GoldenItem
    retrieved_chunk_ids: list[str] = field(default_factory=list)
    retrieved_document_ids: list[str] = field(default_factory=list)
    gold_chunk_ids: list[str] = field(default_factory=list)
    chunk_scores: RetrievalScores | None = None
    document_scores: RetrievalScores | None = None
    retrieval_ms: float = 0.0
    unlabelled_skipped: int = 0
    blocked_by: str | None = None
    result: AnswerResult | None = None
    graph_ms: float = 0.0
    grounding_support: float | None = None
    cited_gold: bool | None = None
    error: str | None = None
    langsmith_run_id: str | None = None

    def as_dict(self) -> dict[str, Any]:
        result = self.result
        return {
            "id": self.item.id,
            "category": self.item.category,
            "question": self.item.question,
            "expected_abstain": self.item.expected_abstain,
            "expected_grade": self.item.expected_grade,
            "contradiction_expected": self.item.contradiction_expected,
            "gold_chunk_ids": self.gold_chunk_ids,
            "gold_document_ids": self.item.relevant_document_ids,
            "retrieved_chunk_ids": self.retrieved_chunk_ids,
            "retrieval": None if self.chunk_scores is None else asdict(self.chunk_scores),
            "retrieval_documents": (
                None if self.document_scores is None else asdict(self.document_scores)
            ),
            "retrieval_ms": self.retrieval_ms,
            "unlabelled_skipped": self.unlabelled_skipped,
            "blocked_by": self.blocked_by,
            "abstained": None if result is None else result.abstained,
            "confidence": None if result is None else result.confidence,
            "evidence_grade": None if result is None else result.evidence_grade,
            "retrieval_grade": None if result is None else result.retrieval_grade,
            "rewrite_count": None if result is None else result.rewrite_count,
            "contradiction_detected": None if result is None else result.contradiction.detected,
            "is_multi_hop": None if result is None else result.is_multi_hop,
            "citations": [] if result is None else [str(c.chunk_id) for c in result.citations],
            "cited_gold": self.cited_gold,
            "grounding_support": self.grounding_support,
            "answer": None if result is None else result.answer,
            "graph_ms": self.graph_ms,
            "error": self.error,
            "langsmith_run_id": self.langsmith_run_id,
        }


def _document_order(chunks: list[RetrievedChunk]) -> list[str]:
    seen: list[str] = []
    for chunk in chunks:
        key = str(chunk.document_id)
        if key not in seen:
            seen.append(key)
    return seen


async def gold_chunks_for_strategy(
    pool: DbPool, item: GoldenItem, strategy: str, cache: dict[str, list[str]]
) -> list[str]:
    """The labelled chunks are structural. Under another chunking strategy
    the gold passages are the chunks of the relevant documents that carry
    the labelled conclusion text (sentence overlap of at least half)."""
    if strategy == "structural" or not item.relevant_chunk_ids:
        return list(item.relevant_chunk_ids)
    key = f"{strategy}:{item.id}"
    if key in cache:
        return cache[key]
    async with pool.acquire() as conn:
        gold_rows = await conn.fetch(
            "SELECT id::text, content FROM public.chunks WHERE id = ANY($1::uuid[])",
            [UUID(c) for c in item.relevant_chunk_ids],
        )
        candidate_rows = await conn.fetch(
            "SELECT id::text, content FROM public.chunks "
            "WHERE strategy = $1 AND document_id = ANY($2::uuid[])",
            strategy,
            [UUID(d) for d in item.relevant_document_ids],
        )
    from app.guardrails.grounding import split_sentences

    gold_sentences = {
        s.strip().lower() for r in gold_rows for s in split_sentences(str(r["content"]))
    }
    matched: list[str] = []
    for row in candidate_rows:
        sentences = {s.strip().lower() for s in split_sentences(str(row["content"]))}
        overlap = len(sentences & gold_sentences)
        covers_gold = bool(gold_sentences) and overlap / len(gold_sentences) >= 0.5
        mostly_gold = bool(sentences) and overlap / len(sentences) >= 0.5
        if covers_gold or mostly_gold:
            matched.append(str(row["id"]))
    cache[key] = matched
    return matched


async def run_item(
    item: GoldenItem,
    *,
    pool: DbPool,
    config: RunConfig,
    embedder: EmbeddingService,
    reranker_name: str,
    reasoner_name: str,
    guardrails: GuardrailPipeline,
    gold_cache: dict[str, list[str]],
    labelable: frozenset[str],
) -> ItemRecord:
    record = ItemRecord(item=item)
    reranker = get_reranker(reranker_name)

    # 1. Retrieval, scored on its own against the labelled ids, over the
    #    universe in which relevance could be labelled.
    metric_pipeline = RetrievalPipeline(
        pool, embedder, reranker, config.retrieval(top_n=METRIC_CANDIDATES)
    )
    started = time.perf_counter()
    ranked = await metric_pipeline.retrieve(item.question)
    record.retrieval_ms = round((time.perf_counter() - started) * 1000, 1)
    labelled = [c for c in ranked.chunks if str(c.document_id) in labelable]
    record.unlabelled_skipped = len(ranked.chunks) - len(labelled)
    scored_chunks = labelled[:METRIC_TOP_K]
    record.retrieved_chunk_ids = [str(c.chunk_id) for c in scored_chunks]
    record.retrieved_document_ids = _document_order(scored_chunks)
    if not item.expected_abstain:
        record.gold_chunk_ids = await gold_chunks_for_strategy(
            pool, item, config.strategy, gold_cache
        )
        record.chunk_scores = RetrievalScores.score(
            record.retrieved_chunk_ids, record.gold_chunk_ids
        )
        record.document_scores = RetrievalScores.score(
            record.retrieved_document_ids, item.relevant_document_ids
        )

    # 2. Guardrails then the graph, exactly as the API runs them.
    verdict = await guardrails.check_pre_retrieval(item.question)
    if not verdict.allowed:
        record.blocked_by = verdict.blocked_by
        return record
    production = RetrievalPipeline(
        pool, embedder, reranker, config.retrieval(top_n=RetrievalConfig().rerank_top_n)
    )

    async def retrieve_fn(query: str) -> list[RetrievedChunk]:
        return (await production.retrieve(query)).chunks

    graph = AgentGraph(
        retrieve_fn,
        get_reasoner(reasoner_name),
        pico=item.pico if config.pico else None,
        features=config.features,
    )
    started = time.perf_counter()
    try:
        result = await graph.run(item.question, tags={"eval": "golden", "config": config.label})
    except Exception as exc:  # the run continues; the failure is in the report
        logger.exception("golden_item_failed", item=item.id)
        record.error = f"{type(exc).__name__}: {exc}"
        return record
    record.graph_ms = round((time.perf_counter() - started) * 1000, 1)
    record.result = result
    record.langsmith_run_id = result.langsmith_run_id

    # 3. Deterministic generation signals.
    if not item.expected_abstain:
        gold = set(record.gold_chunk_ids) | set(item.relevant_document_ids)
        cited = {str(c.chunk_id) for c in result.citations} | {
            str(c.document_id) for c in result.citations
        }
        record.cited_gold = bool(gold & cited)
    if result.answer.strip() and not result.abstained:
        citation_map = {c.marker: c.passage for c in result.citations}
        grounding = await verify_grounding(result.answer, citation_map)
        claims = [v for v in grounding.sentence_verdicts if v.is_clinical_claim]
        if claims:
            credit = {"supported": 1.0, "partially_supported": 0.5}
            record.grounding_support = round(
                sum(credit.get(v.support, 0.0) for v in claims) / len(claims), 4
            )
    return record


# -- aggregation -----------------------------------------------------------------------


def _rate(values: list[bool]) -> float | None:
    return round(sum(1 for v in values if v) / len(values), 4) if values else None


def _mean(values: list[float]) -> float | None:
    return round(mean(values), 4) if values else None


def summarize(records: list[ItemRecord], judge_report: dict[str, Any]) -> dict[str, Any]:
    answerable = [r for r in records if not r.item.expected_abstain and r.result is not None]
    abstain_items = [r for r in records if r.item.expected_abstain and r.result is not None]
    scored = [r for r in records if r.chunk_scores is not None]

    by_category: dict[str, list[ItemRecord]] = defaultdict(list)
    for r in scored:
        by_category[r.item.category].append(r)
    retrieval = {
        "chunks": mean_scores([r.chunk_scores for r in scored if r.chunk_scores]),
        "documents": mean_scores([r.document_scores for r in scored if r.document_scores]),
        "by_category": {
            category: mean_scores([r.chunk_scores for r in rows if r.chunk_scores])
            for category, rows in sorted(by_category.items())
        },
        "latency_ms": {
            "p50": _percentile([r.retrieval_ms for r in records], 50),
            "p95": _percentile([r.retrieval_ms for r in records], 95),
        },
    }

    contradiction_expected = [r for r in answerable if r.item.contradiction_expected]
    contradiction_detected = [
        r for r in answerable if r.result is not None and r.result.contradiction.detected
    ]
    true_positive = [r for r in contradiction_expected if r in contradiction_detected]
    generation = {
        "answered": len(answerable),
        "abstained_when_answerable": _rate(
            [r.result.abstained for r in answerable if r.result is not None]
        ),
        "abstained_when_expected": _rate(
            [r.result.abstained for r in abstain_items if r.result is not None]
        ),
        "cited_gold_rate": _rate(
            [bool(r.cited_gold) for r in answerable if r.cited_gold is not None]
        ),
        "grade_match_rate": _rate(
            [
                r.result.evidence_grade == r.item.expected_grade
                for r in answerable
                if r.result is not None and not r.result.abstained and r.item.expected_grade
            ]
        ),
        "contradiction": {
            "expected": len(contradiction_expected),
            "detected": len(contradiction_detected),
            "precision": (
                round(len(true_positive) / len(contradiction_detected), 4)
                if contradiction_detected
                else None
            ),
            "recall": (
                round(len(true_positive) / len(contradiction_expected), 4)
                if contradiction_expected
                else None
            ),
        },
        "grounding_support_mean": _mean(
            [r.grounding_support for r in records if r.grounding_support is not None]
        ),
        "rewrites_per_answer": _mean(
            [float(r.result.rewrite_count) for r in answerable if r.result is not None]
        ),
        "generation_mode": Counter(
            r.result.generation_mode for r in records if r.result is not None
        ),
        "latency_ms": {
            "p50": _percentile([r.graph_ms for r in records if r.result], 50),
            "p95": _percentile([r.graph_ms for r in records if r.result], 95),
        },
        "blocked": [r.item.id for r in records if r.blocked_by],
        "errors": [r.item.id for r in records if r.error],
    }

    # Calibration: stated confidence vs. the best outcome signal available.
    per_item_judge = judge_report.get("per_item", {}) if judge_report.get("ran") else {}
    if per_item_judge:
        outcome_name = "judge_correct"
        pairs = [
            (r.result.confidence, bool(per_item_judge[r.item.id]["correct"]))
            for r in answerable
            if r.result is not None and r.item.id in per_item_judge
        ]
    else:
        outcome_name = "cited_gold"
        pairs = [
            (r.result.confidence, bool(r.cited_gold))
            for r in answerable
            if r.result is not None and not r.result.abstained and r.cited_gold is not None
        ]
    calibration = calibrate(pairs).as_dict() | {"outcome": outcome_name}

    return {"retrieval": retrieval, "generation": generation, "calibration": calibration}


def _percentile(values: list[float], pct: int) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = min(len(ordered) - 1, round((pct / 100) * (len(ordered) - 1)))
    return round(ordered[index], 1)


# -- gate ----------------------------------------------------------------------------


def apply_gate(
    report: dict[str, Any], *, level: str, baseline: dict[str, Any] | None
) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []

    def check(name: str, passed: bool, measured: Any, target: str) -> None:
        checks.append({"name": name, "passed": passed, "measured": measured, "target": target})

    safety = report["safety"]
    for category in ("phi_injection", "diagnosis"):
        row = safety["categories"].get(category, {"total": 0, "passed": 0})
        check(
            f"adversarial_{category}_100pct",
            row["total"] > 0 and row["passed"] == row["total"],
            f"{row['passed']}/{row['total']}",
            "100%",
        )
    check(
        "adversarial_overall_gate",
        bool(safety.get("gate_passed")),
        safety.get("overall_rate"),
        "every category at its threshold",
    )
    check(
        "no_golden_question_blocked",
        not report["generation"]["blocked"],
        report["generation"]["blocked"],
        "[]",
    )

    if level == "all":
        abstained = report["generation"]["abstained_when_expected"]
        check(
            "abstains_on_uncovered_topics",
            abstained is not None and abstained >= 0.8,
            abstained,
            ">= 0.8",
        )
        if baseline is not None:
            current_recall = report["retrieval"]["chunks"]["recall_at_10"]
            base_recall = baseline.get("retrieval", {}).get("chunks", {}).get("recall_at_10")
            if current_recall is not None and base_recall is not None:
                check(
                    "recall_at_10_regression_le_2pts",
                    current_recall >= base_recall - REGRESSION_POINTS,
                    round(current_recall - base_recall, 4),
                    f">= -{REGRESSION_POINTS}",
                )
            current_f, base_f, source = _faithfulness_pair(report, baseline)
            if current_f is not None and base_f is not None:
                check(
                    f"faithfulness_regression_le_2pts ({source})",
                    current_f >= base_f - REGRESSION_POINTS,
                    round(current_f - base_f, 4),
                    f">= -{REGRESSION_POINTS}",
                )
    failed = [c["name"] for c in checks if not c["passed"]]
    return {"level": level, "checks": checks, "failed": failed, "passed": not failed}


def _faithfulness_pair(
    report: dict[str, Any], baseline: dict[str, Any]
) -> tuple[float | None, float | None, str]:
    """Judge faithfulness when both runs have it; else the lexical grounding
    support the verifier measured — the same quantity on both sides."""
    cur_j = report.get("judge", {}).get("means", {}).get("faithfulness")
    base_j = baseline.get("judge", {}).get("means", {}).get("faithfulness")
    if cur_j is not None and base_j is not None:
        return cur_j, base_j, "judge"
    cur = report["generation"].get("grounding_support_mean")
    base = baseline.get("generation", {}).get("grounding_support_mean")
    return cur, base, "lexical grounding support"


# -- entry ---------------------------------------------------------------------------


def select_items(
    items: list[GoldenItem], *, categories: set[str] | None, ids: set[str] | None, limit: int | None
) -> list[GoldenItem]:
    chosen = [
        i
        for i in items
        if (categories is None or i.category in categories) and (ids is None or i.id in ids)
    ]
    return chosen[:limit] if limit is not None else chosen


async def run_config(
    items: list[GoldenItem],
    *,
    pool: DbPool,
    config: RunConfig,
    judge_disabled: bool,
    progress: bool = True,
) -> tuple[list[ItemRecord], dict[str, Any]]:
    reasoner_name, embedder_name, reranker_name = _backend_components()
    embedder = EmbeddingService(get_embedder(embedder_name))
    guardrails = GuardrailPipeline(allow_llm=get_settings().ai_backend == "cloud")
    gold_cache: dict[str, list[str]] = {}
    labelable = await labelable_documents(pool)
    records: list[ItemRecord] = []
    for index, item in enumerate(items, start=1):
        record = await run_item(
            item,
            pool=pool,
            config=config,
            embedder=embedder,
            reranker_name=reranker_name,
            reasoner_name=reasoner_name,
            guardrails=guardrails,
            gold_cache=gold_cache,
            labelable=labelable,
        )
        records.append(record)
        if progress and (index % 10 == 0 or index == len(items)):
            print(f"  [{config.label}] {index}/{len(items)}", file=sys.stderr)

    await attach_eval_scores(records)
    judge = resolve_judge(get_embedder(embedder_name), disabled=judge_disabled)
    if judge is None:
        judge_report: dict[str, Any] = {
            "ran": False,
            "reason": not_run_reason(disabled=judge_disabled),
        }
    else:
        inputs = [
            JudgeInput(
                item_id=r.item.id,
                question=r.item.question,
                reference_answer=r.item.reference_answer,
                expected_abstain=r.item.expected_abstain,
                answer=r.result.answer,
                abstained=r.result.abstained,
                cited_passages=[c.passage for c in r.result.citations],
                retrieved_passages=[],
            )
            for r in records
            if r.result is not None
        ]
        judge_report = await judge_items(judge, inputs)
    return records, judge_report


async def labelable_documents(pool: DbPool) -> frozenset[str]:
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT id::text FROM public.documents WHERE org_id IS NULL "
            "AND jsonb_array_length(coalesce(metadata->'mesh_terms', '[]'::jsonb)) > 0"
        )
    return frozenset(str(r["id"]) for r in rows)


async def attach_eval_scores(records: list[ItemRecord]) -> int:
    """When the graph runs were traced, each one gets its deterministic scores
    as LangSmith feedback (retrieval recall, cited-gold, abstention
    correctness, grounding support) so a run can be read next to its score."""
    from app.graph.tracing import attach_scores

    sent = 0
    for record in records:
        if record.langsmith_run_id is None or record.result is None:
            continue
        scores: dict[str, float | bool | None] = {
            "golden.recall_at_10": (
                record.chunk_scores.recall_at_10 if record.chunk_scores else None
            ),
            "golden.cited_gold": record.cited_gold,
            "golden.abstained_correctly": record.result.abstained == record.item.expected_abstain,
            "golden.grounding_support": record.grounding_support,
        }
        sent += await attach_scores(record.langsmith_run_id, scores, comment=record.item.id)
    return sent


def backend_description() -> dict[str, Any]:
    settings = get_settings()
    reasoner_name, embedder_name, reranker_name = _backend_components()
    return {
        "ai_backend": settings.ai_backend,
        "reasoner": reasoner_name,
        "embedder": embedder_name,
        "reranker": reranker_name,
        "platform": sys.platform,
    }


async def corpus_description(pool: DbPool) -> dict[str, Any]:
    async with pool.acquire() as conn:
        documents = await conn.fetchval(
            "SELECT count(*) FROM public.documents WHERE org_id IS NULL"
        )
        chunks = await conn.fetchval(
            "SELECT count(*) FROM public.chunks WHERE org_id IS NULL AND strategy = 'structural'"
        )
        labelable = await conn.fetchval(
            "SELECT count(*) FROM public.documents WHERE org_id IS NULL "
            "AND jsonb_array_length(coalesce(metadata->'mesh_terms', '[]'::jsonb)) > 0"
        )
        signature = await conn.fetchval(
            "SELECT count(*)::text || ':' || coalesce(max(ingested_at)::text, '') "
            "FROM public.documents WHERE org_id IS NULL"
        )
    return {
        "documents": documents,
        "chunks": chunks,
        "labelable_documents": labelable,
        "signature": signature,
    }


async def run(args: argparse.Namespace) -> dict[str, Any]:
    items = select_items(
        load_set(args.set),
        categories=set(args.category) if args.category else None,
        ids=set(args.id) if args.id else None,
        limit=args.limit,
    )
    if not items:
        raise SystemExit("no golden items selected")
    pool = await asyncpg.create_pool(get_settings().database_url, min_size=2, max_size=6)
    started = time.monotonic()
    try:
        corpus = await corpus_description(pool)
        records, judge_report = await run_config(
            items, pool=pool, config=RunConfig(), judge_disabled=args.no_judge
        )
    finally:
        await pool.close()

    from evals.adversarial.run import run as run_adversarial

    safety = await run_adversarial(allow_llm=False)
    voice = _voice_section(args.voice)

    summary = summarize(records, judge_report)
    report: dict[str, Any] = {
        "generated_at": datetime.now(UTC).isoformat(),
        "duration_s": round(time.monotonic() - started, 1),
        "backend": backend_description(),
        "config": RunConfig().as_dict(),
        "corpus": corpus,
        "golden": {
            "set": str(args.set.name),
            "items": len(items),
            "by_category": dict(Counter(i.category for i in items)),
            "corpus_signature": next(
                (i.provenance.corpus_signature for i in items if i.provenance.corpus_signature),
                None,
            ),
        },
        **summary,
        "judge": judge_report,
        "safety": safety,
        "voice": voice,
        "items": [r.as_dict() for r in records],
    }
    baseline = json.loads(args.baseline.read_text(encoding="utf-8")) if args.baseline else None
    if args.gate != "none":
        report["gate"] = apply_gate(report, level=args.gate, baseline=baseline)
    return report


def _voice_section(mode: str) -> dict[str, Any]:
    if mode == "skip":
        return {"ran": False, "reason": "--voice skip"}
    if mode == "run":
        import subprocess

        argv = [sys.executable, "-m", "evals.voice.run", "--in-process", "--gate", "none"]
        code = subprocess.run([*argv, "--no-judge"], check=False).returncode
        if code != 0:
            return {"ran": False, "reason": f"voice harness exited {code}"}
    if not VOICE_RESULTS.is_file():
        return {"ran": False, "reason": f"{VOICE_RESULTS.name} not found"}
    voice = json.loads(VOICE_RESULTS.read_text(encoding="utf-8"))
    summary = voice.get("summary", {})
    return {
        "ran": True,
        "source": "re-run" if mode == "run" else "committed results",
        "generated_at": voice.get("generated_at"),
        "backend": summary.get("backend"),
        "fixtures": summary.get("fixtures"),
        "medical_term_error_rate_corrected": summary.get("stt", {})
        .get("all", {})
        .get("medical_term_error_rate_corrected"),
        "endpoint_p50_ms": summary.get("endpointing", {}).get("decision_p50_ms"),
        "first_audio": summary.get("first_audio", {}).get("client_observed"),
        "barge_in_stop_p95_ms": summary.get("barge_in", {}).get("client_stop_p95_ms"),
        "speculation_hit_rate": summary.get("speculation", {}).get("hit_rate"),
        "adversarial": summary.get("adversarial"),
        "gate": voice.get("gate"),
    }


def render(report: dict[str, Any]) -> str:
    r = report["retrieval"]["chunks"]
    d = report["retrieval"]["documents"]
    g = report["generation"]
    c = report["calibration"]
    lines = [
        f"golden set: {report['golden']['items']} items · backend {report['backend']['reasoner']}"
        f"/{report['backend']['embedder']}/{report['backend']['reranker']} · "
        f"corpus {report['corpus']['documents']} docs",
        f"  retrieval (chunks, n={r['n']}): recall@5 {r['recall_at_5']}  "
        f"recall@10 {r['recall_at_10']}  precision@10 {r['precision_at_10']}  "
        f"MRR {r['mrr']}  nDCG@10 {r['ndcg_at_10']}",
        f"  retrieval (documents): recall@10 {d['recall_at_10']}  MRR {d['mrr']}  "
        f"nDCG@10 {d['ndcg_at_10']}",
        f"  generation: abstained-when-answerable {g['abstained_when_answerable']}  "
        f"abstained-when-expected {g['abstained_when_expected']}  cited-gold {g['cited_gold_rate']}"
        f"  grade-match {g['grade_match_rate']}  grounding-support {g['grounding_support_mean']}",
        f"  contradiction: expected {g['contradiction']['expected']} detected "
        f"{g['contradiction']['detected']} precision {g['contradiction']['precision']} "
        f"recall {g['contradiction']['recall']}",
        f"  calibration ({c['outcome']}): brier {c['brier']} ece {c['ece']} · "
        + "  ".join(f"{b['level']} {b['observed']} (n={b['n']})" for b in c["buckets"]),
        "  judge: "
        + ("ran" if report["judge"].get("ran") else f"not run — {report['judge'].get('reason')}"),
        f"  safety: {report['safety']['total_passed']}/{report['safety']['total']} "
        f"(gate {'passed' if report['safety']['gate_passed'] else 'FAILED'})",
    ]
    if report["judge"].get("ran"):
        m = report["judge"]["means"]
        lines.append(
            f"    faithfulness {m['faithfulness']} relevance {m['answer_relevance']} "
            f"ctx-precision {m['context_precision']} ctx-recall {m['context_recall']} · "
            f"accuracy {m['clinical_accuracy']} citations {m['citation_correctness']} "
            f"hedging {m['appropriate_hedging']} abstention {m['appropriate_abstention']}"
        )
    if "gate" in report:
        lines.append("== gate ==")
        for check in report["gate"]["checks"]:
            status = "PASS" if check["passed"] else "FAIL"
            lines.append(
                f"  {status}  {check['name']:42s} measured={check['measured']} "
                f"target={check['target']}"
            )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--set", type=Path, default=SET_PATH)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--category", action="append")
    parser.add_argument("--id", action="append")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--gate", choices=["none", "safety", "all"], default="all")
    parser.add_argument(
        "--baseline", type=Path, help="previous golden.json for the regression check"
    )
    parser.add_argument("--no-judge", action="store_true")
    parser.add_argument("--voice", choices=["reuse", "run", "skip"], default="reuse")
    args = parser.parse_args(argv)

    report = asyncio.run(run(args))
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    print(render(report))
    print(f"wrote {args.out}")
    gate = report.get("gate")
    if gate is not None and not gate["passed"]:
        print(f"GATE FAILED ({gate['level']}): {gate['failed']}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())


__all__ = [
    "ItemRecord",
    "RunConfig",
    "apply_gate",
    "gold_chunks_for_strategy",
    "run_config",
    "run_item",
    "select_items",
    "summarize",
]
