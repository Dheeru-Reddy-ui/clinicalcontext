"""Agent graph — the four Phase-8 gates, deterministic (heuristic reasoner, no DB).

Retrieval is an injected fixture callable, so these exercise the real graph
(routing, loop cap, contradiction structuring, abstention, streaming) without
a database or any API key.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import date
from uuid import uuid4

from app.graph.graph import MAX_REWRITES, AgentGraph, GraphFeatures
from app.graph.reasoner import HeuristicReasoner
from app.retrieval.types import RetrievedChunk
from app.schemas.answer import Contradiction

RetrieveFn = Callable[[str], Awaitable[list[RetrievedChunk]]]


def _chunk(
    content: str, *, year: int = 2020, grade: str = "B", score: float = 0.6
) -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id=uuid4(),
        document_id=uuid4(),
        content=content,
        section=None,
        title="Doc",
        publication_date=date(year, 1, 1),
        evidence_grade=grade,
        study_type="randomized_controlled_trial",
        score=score,
    )


def _fixed(chunks: list[RetrievedChunk]) -> RetrieveFn:
    async def retrieve(_query: str) -> list[RetrievedChunk]:
        return chunks

    return retrieve


def _graph(retrieve: RetrieveFn) -> AgentGraph:
    return AgentGraph(retrieve, HeuristicReasoner())


_CONFLICT = [
    _chunk(
        "The 2019 guideline recommends a statin threshold of 10% risk; statins are "
        "first-line and reduce cardiovascular mortality in primary prevention.",
        year=2019,
        grade="A",
        score=0.7,
    ),
    _chunk(
        "The 2023 guideline does not recommend statins at a 10% threshold; there is "
        "insufficient evidence of benefit and they should not be used routinely for "
        "primary prevention at that risk level.",
        year=2023,
        grade="A",
        score=0.68,
    ),
    _chunk(
        "Statin thresholds in primary prevention remain debated across guidelines.",
        year=2022,
        score=0.5,
    ),
    _chunk("Primary prevention statin recommendations depend on estimated risk.", score=0.46),
]


# -- Gate 1: known guideline conflict must be surfaced, not smoothed -----------------


async def test_guideline_conflict_is_surfaced() -> None:
    graph = _graph(_fixed(_CONFLICT))
    result = await graph.run(
        "Do the 2019 and 2023 guidelines disagree on statin thresholds for primary prevention?"
    )
    assert result.contradiction.detected
    assert not result.abstained
    assert len(result.contradiction.positions) == 2
    # Both years appear as distinct positions — the disagreement is explicit.
    years = {p.year for p in result.contradiction.positions}
    assert 2019 in years and 2023 in years
    # A surfaced conflict caps confidence at moderate (not a false "high").
    assert result.confidence == "moderate"
    # The answer names both positions rather than picking one.
    assert "one position" in result.answer.lower()


# -- Gate 2: unanswerable question must abstain, not confabulate ----------------------


async def test_unanswerable_question_abstains() -> None:
    graph = _graph(_fixed([]))  # nothing retrieved
    result = await graph.run("What is the treatment for xyzzy-syndrome-9000?")
    assert result.abstained
    assert result.confidence == "low"
    assert result.citations == []
    assert "abstain" in result.answer.lower() or "can't answer" in result.answer.lower()


# -- Gate 3: multi-hop question decomposes and retrieves per sub-question -------------


async def test_multihop_decomposes_and_retrieves_per_subquestion() -> None:
    seen_queries: list[str] = []

    async def retrieve(query: str) -> list[RetrievedChunk]:
        seen_queries.append(query)
        return [
            _chunk(
                "Apixaban is preferred over warfarin for stroke prevention in atrial "
                "fibrillation with chronic kidney disease stage 4.",
                grade="A",
                score=0.65,
            )
            for _ in range(4)
        ]

    graph = AgentGraph(retrieve, HeuristicReasoner())
    result = await graph.run("Is apixaban preferred over warfarin in AF with CKD stage 4?")
    assert result.is_multi_hop
    assert len(result.sub_questions) >= 3
    # Retrieval ran once per sub-question (not once for the whole query).
    assert len(seen_queries) == len(result.sub_questions)
    assert not result.abstained


async def test_multihop_trace_records_per_subquestion() -> None:
    graph = _graph(_fixed([_chunk("apixaban warfarin atrial fibrillation kidney", score=0.6)]))
    events = []
    async for kind, payload in graph.astream(
        "Is apixaban preferred over warfarin in AF with CKD stage 4?"
    ):
        if kind == "event":
            events.append(payload.stage)
    assert "decomposing" in events
    assert "searching" in events


# -- Gate 4: retrieval failure rewrites, retries once, abstains — never loops --------


async def test_retrieval_failure_rewrites_then_abstains_without_looping() -> None:
    calls = {"n": 0}

    async def failing_retrieve(_query: str) -> list[RetrievedChunk]:
        calls["n"] += 1
        return [_chunk("Unrelated passage about plant biology.", score=0.1)]

    graph = AgentGraph(failing_retrieve, HeuristicReasoner())
    result = await graph.run("What is the treatment for community-acquired pneumonia?")

    assert result.abstained
    assert result.rewrite_count == MAX_REWRITES
    # initial retrieval + exactly MAX_REWRITES retries — bounded, no infinite loop.
    assert calls["n"] == MAX_REWRITES + 1


async def test_rewrite_events_are_streamed() -> None:
    async def weak(_query: str) -> list[RetrievedChunk]:
        return [_chunk("irrelevant", score=0.05)]

    graph = AgentGraph(weak, HeuristicReasoner())
    rewrite_events = 0
    async for kind, payload in graph.astream("treatment for something obscure"):
        if kind == "event" and payload.stage == "rewriting":
            rewrite_events += 1
    assert rewrite_events == MAX_REWRITES


# -- grounding integration: unsupported generation routes to abstain -----------------


async def test_grounding_failure_routes_to_abstain() -> None:
    # A reasoner whose generation is ungrounded (claims absent from passages).
    class UngroundedReasoner(HeuristicReasoner):
        async def generate(self, query, chunks, contradiction):  # type: ignore[no-untyped-def]
            from app.graph.reasoner import GenerationOutput

            return GenerationOutput(
                text="Apixaban cures every cancer within days [1].",
                ordered_chunks=chunks,
                mode="llm",
            )

    graph = AgentGraph(
        _fixed([_chunk("Apixaban reduces stroke in atrial fibrillation.", score=0.6)] * 4),
        UngroundedReasoner(),
    )
    result = await graph.run("Does apixaban help in atrial fibrillation?")
    assert result.abstained


# -- confidence calibration ----------------------------------------------------------


async def test_high_confidence_requires_strong_recent_grounded_evidence() -> None:
    strong = [
        _chunk(
            "Apixaban reduces stroke in atrial fibrillation with kidney disease.",
            year=date.today().year,
            grade="A",
            score=0.7,
        )
        for _ in range(4)
    ]
    graph = _graph(_fixed(strong))
    result = await graph.run("Does apixaban reduce stroke in atrial fibrillation?")
    assert not result.abstained
    assert result.confidence == "high"
    assert result.evidence_grade == "A"


# -- ablation switches (Phase 12) ----------------------------------------------------


async def test_features_off_make_each_stage_disappear() -> None:
    """The ablation adds one stage at a time; with a stage off the graph must
    behave as if it were never written, not as a degraded version of it."""
    seen: list[str] = []

    async def retrieve(query: str) -> list[RetrievedChunk]:
        seen.append(query)
        return list(_CONFLICT)

    bare = GraphFeatures(
        decompose=False, grade_and_rewrite=False, contradiction=False, grounding=False
    )
    result = await AgentGraph(retrieve, HeuristicReasoner(), features=bare).run(
        "Is apixaban preferred over warfarin in AF with CKD stage 4?"
    )
    assert not result.is_multi_hop and len(seen) == 1, "no decomposition: one retrieval"
    assert result.retrieval_grade == "sufficient" and result.rewrite_count == 0
    assert not result.contradiction.detected, "the conflict in _CONFLICT is not looked for"
    assert not result.abstained

    graph = AgentGraph(retrieve, HeuristicReasoner())  # everything on
    full = await graph.run("Should statins be used for primary prevention?")
    assert full.contradiction.detected

    # No grading: an empty retrieval is still "sufficient" and never rewritten,
    # so the answer is generated from nothing rather than abstained.
    ungraded = GraphFeatures(grade_and_rewrite=False)
    empty = await AgentGraph(_fixed([]), HeuristicReasoner(), features=ungraded).run(
        "What is the treatment for xyzzy-syndrome-9000?"
    )
    assert empty.rewrite_count == 0 and empty.retrieval_grade == "sufficient"

    # No grounding: an ungrounded generation ships instead of abstaining.
    class UngroundedReasoner(HeuristicReasoner):
        async def generate(self, query, chunks, contradiction):  # type: ignore[no-untyped-def]
            from app.graph.reasoner import GenerationOutput

            return GenerationOutput(
                text="Apixaban cures every cancer within days [1].",
                ordered_chunks=chunks,
                mode="llm",
            )

    unverified = AgentGraph(
        _fixed([_chunk("Apixaban reduces stroke in atrial fibrillation.", score=0.6)] * 4),
        UngroundedReasoner(),
        features=GraphFeatures(grade_and_rewrite=False, grounding=False),
    )
    shipped = await unverified.run("Does apixaban help in atrial fibrillation?")
    assert not shipped.abstained and "cures every cancer" in shipped.answer


async def test_extractive_citations_attach_to_their_own_sentence() -> None:
    """A marker written after the full stop reads as the start of the next
    sentence to the grounding verifier, which then checks every sentence
    against the wrong passage and abstains. Markers sit inside the stop."""
    from app.guardrails.grounding import verify_grounding

    chunks = [
        _chunk(
            "Apixaban reduces stroke risk in atrial fibrillation compared with warfarin.", score=0.7
        ),
        _chunk(
            "Rivaroxaban is associated with more gastrointestinal bleeding than apixaban.",
            score=0.65,
        ),
        _chunk("Dabigatran requires dose adjustment in renal impairment.", score=0.6),
    ]
    output = await HeuristicReasoner().generate(
        "Which anticoagulant is preferred in atrial fibrillation?",
        chunks,
        Contradiction(detected=False),
    )
    assert "[1]." in output.text and "[2]." in output.text and "[3]." in output.text
    verdict = await verify_grounding(
        output.text, {i + 1: c.content for i, c in enumerate(output.ordered_chunks)}
    )
    clinical = [v for v in verdict.sentence_verdicts if v.is_clinical_claim]
    assert [v.cited_markers for v in clinical] == [[1], [2], [3]]
    assert all(v.support == "supported" for v in clinical)
    assert verdict.accepted and verdict.removed_fraction == 0.0


async def test_grader_rejects_passages_that_share_only_boilerplate() -> None:
    """A confident-looking retrieval about the wrong topic must not be
    graded sufficient: the passages have to carry the question's topic
    terms, not just "treatment" and "recommended"."""
    reasoner = HeuristicReasoner()
    off_topic = [
        _chunk(
            "Dual antiplatelet therapy is the recommended treatment of choice after "
            "acute coronary syndrome; guidelines recommend 12 months.",
            score=0.7,
        )
    ] * 4
    assert await reasoner.grade_retrieval("What is the treatment for scurvy?", off_topic) == (
        "irrelevant"
    )
    assert (
        await reasoner.grade_retrieval(
            "How should rabies post-exposure prophylaxis be given?", off_topic
        )
        == "irrelevant"
    )
    on_topic = [
        _chunk(
            "Metformin remains the first-line treatment for type 2 diabetes and reduces "
            "HbA1c by about 1%.",
            score=0.7,
        )
    ] * 4
    assert await reasoner.grade_retrieval(
        "Is metformin effective for type 2 diabetes?", on_topic
    ) == ("sufficient")
    # Half the topic present: not enough to answer, worth a rewrite.
    partial = [
        _chunk("Antibiotics are widely used in community-acquired pneumonia.", score=0.7)
    ] * 4
    assert (
        await reasoner.grade_retrieval(
            "What antibiotics are recommended for Lyme disease?", partial
        )
        == "insufficient"
    )
    # End to end: the off-topic retrieval ends in an abstention, not an answer.
    result = await AgentGraph(_fixed(off_topic), reasoner).run("What is the treatment for scurvy?")
    assert result.abstained and result.retrieval_grade == "irrelevant"
