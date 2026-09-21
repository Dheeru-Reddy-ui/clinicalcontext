"""The LangGraph agent: classify → (decompose) → retrieve → grade →
(rewrite ↺) → detect-contradiction → generate → verify → assess, with an
abstain path.

    classify_query ─(multi-hop?)─► [decompose] ─► retrieve ─► grade_retrieval
      grade == sufficient           ─► detect_contradiction ─► generate
      grade == insufficient/irrelevant:
          rewrites left             ─► rewrite_query ─► retrieve   (loop, cap 2)
          no rewrites left          ─► abstain
      generate ─► verify_grounding:
          accepted                  ─► assess_confidence ─► END
          rejected                  ─► abstain ─► assess_confidence ─► END

Reasoning is delegated to a :class:`Reasoner` (LLM or heuristic); retrieval is
an injected async callable, so the graph is fully unit-testable without a DB.
Progress is streamed as :class:`GraphEvent`s; LangSmith tracing is enabled via
env when a key is present.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import date
from typing import Any, cast

import structlog
from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph

from app.core.telemetry import span
from app.graph.reasoner import LLM_PROMPTS, GenerationOutput, Reasoner, StreamingReasoner
from app.graph.state import GraphEvent, GraphState
from app.guardrails.grounding import GroundingVerifier, verify_grounding
from app.retrieval.types import RetrievedChunk
from app.schemas.answer import (
    AnswerResult,
    Citation,
    Confidence,
    Contradiction,
    EvidenceGrade,
)

logger = structlog.stdlib.get_logger("app.graph")

RetrieveFn = Callable[[str], Awaitable[list[RetrievedChunk]]]
EmitFn = Callable[[GraphEvent], Awaitable[None]]

MAX_REWRITES = 2
_RECURSION_LIMIT = 25
_GRADE_ORDER = {"A": 3, "B": 2, "C": 1, "D": 0}


@dataclass(frozen=True, slots=True)
class GraphFeatures:
    """Which reasoning stages run. Production runs all of them; the ablation
    runner (``evals/ablation/full.py``) adds them one at a time to measure
    what each buys. With a stage off the graph behaves as if it never
    existed: no decomposition means one retrieval; no grading means every
    retrieval is taken as sufficient; no contradiction check means none is
    ever surfaced; no grounding means the generated answer ships as is."""

    decompose: bool = True
    grade_and_rewrite: bool = True
    contradiction: bool = True
    grounding: bool = True


async def _emit(config: RunnableConfig | None, event: GraphEvent) -> None:
    emit: EmitFn | None = (config or {}).get("configurable", {}).get("emit")
    if emit is not None:
        await emit(event)


class AgentGraph:
    def __init__(
        self,
        retrieve_fn: RetrieveFn,
        reasoner: Reasoner,
        *,
        grounding_verifier: GroundingVerifier | None = None,
        pico: dict[str, Any] | None = None,
        stream_tokens: bool = False,
        features: GraphFeatures | None = None,
    ) -> None:
        self._retrieve = retrieve_fn
        self._reasoner = reasoner
        self._grounding_verifier = grounding_verifier
        self._pico = pico
        self._features = features if features is not None else GraphFeatures()
        # Voice path: emit generation deltas as ``token`` events *during* the
        # generate node (the sentence pipeline gates each one on grounding
        # before it is spoken). The text path keeps its verified-only stream.
        self._stream_tokens = stream_tokens
        self._compiled = self._build()

    # -- nodes -----------------------------------------------------------------

    async def classify_query(self, state: GraphState, config: RunnableConfig) -> GraphState:
        query = state["query"]
        query_type, multi_hop = await self._reasoner.classify(query)
        # Structured PICO always implies a decomposed, multi-hop plan.
        if self._pico:
            multi_hop = True
        if not self._features.decompose:
            multi_hop = False
        await _emit(
            config,
            GraphEvent(
                "classifying",
                "Understanding the question…",
                {"query_type": query_type, "multi_hop": multi_hop},
            ),
        )
        return {
            "query_type": query_type,
            "is_multi_hop": multi_hop,
            "current_query": query,
            "rewrite_count": 0,
        }

    async def decompose(self, state: GraphState, config: RunnableConfig) -> GraphState:
        # PICO fields, when present, seed decomposition directly (Phase-9 PICO).
        subs = _pico_subquestions(self._pico) if self._pico else []
        if not subs:
            subs = await self._reasoner.decompose(state["query"])
        await _emit(
            config,
            GraphEvent(
                "decomposing",
                f"Breaking this into {len(subs)} sub-questions…",
                {"sub_questions": subs},
            ),
        )
        return {"sub_questions": subs}

    async def retrieve(self, state: GraphState, config: RunnableConfig) -> GraphState:
        # Multi-hop retrieves per sub-question; otherwise the (possibly
        # rewritten) current query. Results are merged, deduped by chunk id,
        # keeping the best score.
        # Use sub-questions only on the first (pre-rewrite) multi-hop pass; a
        # rewrite retrieves the single rewritten query.
        current = state.get("current_query", state["query"])
        use_subs = (
            state.get("is_multi_hop", False)
            and state.get("rewrite_count", 0) == 0
            and bool(state.get("sub_questions"))
        )
        questions = state["sub_questions"] if use_subs else [current]
        await _emit(
            config, GraphEvent("searching", "Searching the literature…", {"queries": questions})
        )
        merged: dict[str, RetrievedChunk] = {}
        per_sub: dict[str, list[str]] = {}
        for question in questions:
            chunks = await self._retrieve(question)
            per_sub[question] = [str(c.chunk_id) for c in chunks]
            for chunk in chunks:
                key = str(chunk.chunk_id)
                if key not in merged or chunk.score > merged[key].score:
                    merged[key] = chunk
        ranked = sorted(merged.values(), key=lambda c: c.score, reverse=True)
        await _emit(
            config, GraphEvent("searched", f"Found {len(ranked)} sources.", {"count": len(ranked)})
        )
        return {"retrieved": ranked, "per_subquestion": per_sub}

    async def grade_retrieval(self, state: GraphState, config: RunnableConfig) -> GraphState:
        if not self._features.grade_and_rewrite:
            # Ungraded: whatever came back is used, and nothing is retried.
            return {"retrieval_grade": "sufficient"}
        grade = await self._reasoner.grade_retrieval(state["query"], state.get("retrieved", []))
        await _emit(
            config,
            GraphEvent(
                "grading", "Assessing whether the evidence is sufficient…", {"grade": grade}
            ),
        )
        return {"retrieval_grade": grade}

    async def rewrite_query(self, state: GraphState, config: RunnableConfig) -> GraphState:
        attempt = state.get("rewrite_count", 0)
        rewritten = await self._reasoner.rewrite_query(
            state["query"], attempt, state.get("retrieved", [])
        )
        await _emit(
            config,
            GraphEvent(
                "rewriting",
                "Evidence was thin — refining the search and retrying…",
                {"attempt": attempt + 1, "rewritten_query": rewritten},
            ),
        )
        return {"current_query": rewritten, "rewrite_count": attempt + 1}

    async def detect_contradiction(self, state: GraphState, config: RunnableConfig) -> GraphState:
        if not self._features.contradiction:
            return {"contradiction": Contradiction(detected=False)}
        await _emit(config, GraphEvent("checking_conflict", "Checking for conflicting evidence…"))
        contradiction = await self._reasoner.detect_contradiction(
            state["query"], state.get("retrieved", [])
        )
        if contradiction.detected:
            await _emit(
                config,
                GraphEvent(
                    "conflict_found",
                    "The sources disagree — surfacing the conflict.",
                    {"axis": contradiction.axis, "contradiction": contradiction.model_dump()},
                ),
            )
        return {"contradiction": contradiction}

    async def generate(self, state: GraphState, config: RunnableConfig) -> GraphState:
        contradiction = state.get("contradiction") or Contradiction(detected=False)
        retrieved = state.get("retrieved", [])
        # The marker order the generator will use ([1] = the first retrieved
        # chunk): a streaming consumer maps markers to passages from this, in
        # event order, rather than from retrieval callbacks it may see late.
        await _emit(
            config,
            GraphEvent(
                "generating",
                "Composing the answer with citations…",
                {"chunk_ids": [str(c.chunk_id) for c in retrieved]},
            ),
        )
        output: GenerationOutput | None = None
        if self._stream_tokens and isinstance(self._reasoner, StreamingReasoner):
            async for item in self._reasoner.generate_stream(
                state["query"], retrieved, contradiction
            ):
                if isinstance(item, str):
                    await _emit(config, GraphEvent("token", "", {"text": item}))
                else:
                    output = item
        if output is None:
            output = await self._reasoner.generate(state["query"], retrieved, contradiction)
        citations = [_citation(i + 1, c) for i, c in enumerate(output.ordered_chunks)]
        return {
            "answer": output.text,
            "citations": citations,
            "ordered_chunks": output.ordered_chunks,
            "generation_mode": output.mode,
            "model": self._reasoner.name,
        }

    async def verify_grounding(self, state: GraphState, config: RunnableConfig) -> GraphState:
        if not self._features.grounding:
            return {"abstained": False}
        await _emit(config, GraphEvent("verifying", "Verifying every claim against its source…"))
        citation_map = {i + 1: c.content for i, c in enumerate(state.get("ordered_chunks", []))}
        result = await verify_grounding(
            state["answer"], citation_map, verifier=self._grounding_verifier
        )
        if not result.accepted:
            await _emit(
                config,
                GraphEvent("grounding_failed", "The answer could not be grounded — abstaining."),
            )
            return {"abstained": True, "answer": ""}
        pruned = len(result.removed_sentences)
        if pruned:
            await _emit(
                config,
                GraphEvent(
                    "grounding_pruned",
                    f"Removed {pruned} unsupported sentence(s).",
                    {"removed": pruned},
                ),
            )
        return {"answer": result.kept_answer, "abstained": False}

    async def abstain(self, state: GraphState, config: RunnableConfig) -> GraphState:
        await _emit(config, GraphEvent("abstaining", "Not enough solid evidence — abstaining."))
        retrieved = state.get("retrieved", [])
        searched = state.get("sub_questions") or [state.get("current_query", state["query"])]
        grade = state.get("retrieval_grade", "insufficient")
        answer = _abstention_text(
            state["query"], searched, retrieved, grade, state.get("rewrite_count", 0)
        )
        # Preserve a contradiction if one was already detected (don't clobber it).
        return {
            "abstained": True,
            "answer": answer,
            "citations": [_citation(i + 1, c) for i, c in enumerate(retrieved[:5])],
            "ordered_chunks": retrieved[:5],
            "contradiction": state.get("contradiction") or Contradiction(detected=False),
        }

    async def assess_confidence(self, state: GraphState, config: RunnableConfig) -> GraphState:
        chunks = state.get("ordered_chunks", []) or state.get("retrieved", [])
        contradiction = state.get("contradiction") or Contradiction(detected=False)
        abstained = state.get("abstained", False)
        confidence, evidence_grade = _assess(
            chunks, contradiction, abstained, state.get("retrieval_grade", "insufficient")
        )
        prompt_versions = (
            {name: f"{name}.v{v}" for name, v in LLM_PROMPTS.items()}
            if self._reasoner.name == "llm"
            else {}
        )
        await _emit(
            config,
            GraphEvent(
                "done",
                "Done.",
                {
                    "confidence": confidence,
                    "evidence_grade": evidence_grade,
                    "abstained": abstained,
                },
            ),
        )
        return {
            "confidence": confidence,
            "evidence_grade": evidence_grade,
            "prompt_versions": prompt_versions,
        }

    # -- edges -----------------------------------------------------------------

    def _route_after_classify(self, state: GraphState) -> str:
        return "decompose" if state.get("is_multi_hop") else "retrieve"

    def _route_after_grade(self, state: GraphState) -> str:
        if state.get("retrieval_grade") == "sufficient":
            return "detect_contradiction"
        if state.get("rewrite_count", 0) < MAX_REWRITES:
            return "rewrite_query"
        return "abstain"

    def _route_after_grounding(self, state: GraphState) -> str:
        return "abstain" if state.get("abstained") else "assess_confidence"

    def _traced(self, name: str, node: Any) -> Any:
        """Each node runs inside a span named after it, so a trace reads as the
        graph's path: classify → retrieve → grade → … → assess."""

        async def run(state: GraphState, config: RunnableConfig) -> GraphState:
            with span(f"graph.{name}", rewrite_count=state.get("rewrite_count", 0)) as current:
                result: GraphState = await node(state, config)
                for key in ("retrieval_grade", "is_multi_hop", "abstained"):
                    if key in result:
                        current.set_attribute(f"graph.{key}", str(result[key]))
                if "retrieved" in result:
                    current.set_attribute("graph.retrieved", len(result["retrieved"]))
                return result

        return run

    def _build(self) -> Any:
        graph: Any = StateGraph(GraphState)
        for name, node in (
            ("classify_query", self.classify_query),
            ("decompose", self.decompose),
            ("retrieve", self.retrieve),
            ("grade_retrieval", self.grade_retrieval),
            ("rewrite_query", self.rewrite_query),
            ("detect_contradiction", self.detect_contradiction),
            ("generate", self.generate),
            ("verify_grounding", self.verify_grounding),
            ("abstain", self.abstain),
            ("assess_confidence", self.assess_confidence),
        ):
            graph.add_node(name, self._traced(name, node))

        graph.add_edge(START, "classify_query")
        graph.add_conditional_edges(
            "classify_query",
            self._route_after_classify,
            {"decompose": "decompose", "retrieve": "retrieve"},
        )
        graph.add_edge("decompose", "retrieve")
        graph.add_edge("retrieve", "grade_retrieval")
        graph.add_conditional_edges(
            "grade_retrieval",
            self._route_after_grade,
            {
                "detect_contradiction": "detect_contradiction",
                "rewrite_query": "rewrite_query",
                "abstain": "abstain",
            },
        )
        graph.add_edge("rewrite_query", "retrieve")
        graph.add_edge("detect_contradiction", "generate")
        graph.add_edge("generate", "verify_grounding")
        graph.add_conditional_edges(
            "verify_grounding",
            self._route_after_grounding,
            {"abstain": "abstain", "assess_confidence": "assess_confidence"},
        )
        graph.add_edge("abstain", "assess_confidence")
        graph.add_edge("assess_confidence", END)
        return graph.compile()

    # -- run -------------------------------------------------------------------

    async def run(
        self, query: str, *, emit: EmitFn | None = None, tags: dict[str, str] | None = None
    ) -> AnswerResult:
        from langchain_core.tracers.context import collect_runs

        from app.graph.tracing import langsmith_enabled, run_metadata

        config: dict[str, Any] = {
            "recursion_limit": _RECURSION_LIMIT,
            "configurable": {"emit": emit},
            "tags": [f"{k}:{v}" for k, v in (tags or {}).items()],
            "metadata": run_metadata(tags),
        }
        with collect_runs() as runs:
            final: GraphState = await self._compiled.ainvoke({"query": query}, config=config)
        result = _to_result(query, final)
        # The run id is the LangSmith run id when tracing is on — the handle a
        # score or a thumbs verdict is attached to later. Without tracing it
        # would be a number nobody can look up, so it stays None.
        if langsmith_enabled() and runs.traced_runs:
            result.langsmith_run_id = str(runs.traced_runs[0].id)
        return result

    async def astream(self, query: str, *, tags: dict[str, str] | None = None) -> Any:
        """Yield GraphEvents as they occur, then a final ('result', AnswerResult)."""
        queue: asyncio.Queue[GraphEvent | None] = asyncio.Queue()

        async def emit(event: GraphEvent) -> None:
            await queue.put(event)

        run_task = asyncio.create_task(self.run(query, emit=emit, tags=tags))
        run_task.add_done_callback(lambda _t: queue.put_nowait(None))

        while True:
            event = await queue.get()
            if event is None:
                break
            yield ("event", event)
        result = await run_task  # re-raises any error from the run
        yield ("result", result)


# -- pure helpers --------------------------------------------------------------------


def _pico_subquestions(pico: dict[str, Any]) -> list[str]:
    """Seed sub-questions directly from PICO fields."""
    population = str(pico.get("population", "")).strip()
    intervention = str(pico.get("intervention", "")).strip()
    comparison = str(pico.get("comparison", "")).strip()
    outcome = str(pico.get("outcome", "")).strip()
    pop = f" in {population}" if population else ""
    out = f" for {outcome}" if outcome else ""
    subs: list[str] = []
    if intervention:
        subs.append(f"{intervention}{pop}{out}".strip())
    if comparison:
        subs.append(f"{comparison}{pop}{out}".strip())
    if intervention and comparison:
        subs.append(f"{intervention} versus {comparison}{pop}{out}".strip())
    return subs


def citation_from_chunk(marker: int, chunk: RetrievedChunk) -> Citation:
    """Public form of the graph's citation builder (the voice renderer needs
    citations for attribution before the result event exists)."""
    return _citation(marker, chunk)


def _citation(marker: int, chunk: RetrievedChunk) -> Citation:
    return Citation(
        marker=marker,
        chunk_id=chunk.chunk_id,
        document_id=chunk.document_id,
        title=chunk.title,
        section=chunk.section,
        publication_date=chunk.publication_date.isoformat() if chunk.publication_date else None,
        evidence_grade=chunk.evidence_grade,
        study_type=chunk.study_type,
        journal=chunk.journal,
        pmid=chunk.pmid,
        doi=chunk.doi,
        url=chunk.url,
        passage=chunk.content,
    )


def _assess(
    chunks: list[RetrievedChunk],
    contradiction: Contradiction,
    abstained: bool,
    grade: str,
) -> tuple[Confidence, EvidenceGrade | None]:
    if abstained or not chunks:
        return "low", None
    grades = [c.evidence_grade for c in chunks if c.evidence_grade in _GRADE_ORDER]
    best_grade: EvidenceGrade | None = (
        cast(EvidenceGrade, max(grades, key=lambda g: _GRADE_ORDER[g])) if grades else None
    )
    top_score = max((c.score for c in chunks), default=0.0)
    this_year = date.today().year
    recent = any(c.publication_date and (this_year - c.publication_date.year) <= 5 for c in chunks)

    if contradiction.detected:
        # A surfaced conflict caps confidence at moderate — we are confident
        # about the disagreement, not about a single answer.
        return "moderate", best_grade
    strong_grade = best_grade in ("A", "B")
    if grade == "sufficient" and strong_grade and recent and top_score >= 0.5:
        return "high", best_grade
    if grade == "sufficient" and (strong_grade or recent):
        return "moderate", best_grade
    return "low", best_grade


def _abstention_text(
    query: str, searched: list[str], retrieved: list[RetrievedChunk], grade: str, rewrites: int
) -> str:
    found = (
        f"{len(retrieved)} passages were retrieved but graded '{grade}'"
        if retrieved
        else "no relevant passages were found"
    )
    searched_note = f" across {len(searched)} sub-questions" if len(searched) > 1 else ""
    retries = f" after {rewrites} query refinement(s)" if rewrites else ""
    suggestion = (
        "Consider narrowing to a specific population, intervention, and outcome "
        "(a PICO-style question), or checking whether the topic is covered by the corpus."
    )
    return (
        f"I can't answer this confidently from the available literature. "
        f"I searched the corpus{searched_note}{retries}, and {found}, which is not enough "
        f"to support a grounded answer. Rather than guess, I'm abstaining. {suggestion}"
    )


def _to_result(query: str, state: GraphState) -> AnswerResult:
    return AnswerResult(
        query=query,
        query_type=state.get("query_type", "other"),
        is_multi_hop=state.get("is_multi_hop", False),
        sub_questions=state.get("sub_questions", []),
        abstained=state.get("abstained", False),
        answer=state.get("answer", ""),
        citations=state.get("citations", []),
        contradiction=state.get("contradiction") or Contradiction(detected=False),
        confidence=state.get("confidence", "low"),
        evidence_grade=state.get("evidence_grade"),
        retrieval_grade=state.get("retrieval_grade", "insufficient"),
        rewrite_count=state.get("rewrite_count", 0),
        model=state.get("model", "heuristic"),
        prompt_versions=state.get("prompt_versions", {}),
        generation_mode=state.get("generation_mode", "extractive"),
    )
