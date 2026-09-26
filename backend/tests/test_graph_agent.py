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
from app.graph.state import GraphEvent
from app.retrieval.types import RetrievedChunk
from app.schemas.answer import AnswerResult, Contradiction

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
    # Retrieval ran once per sub-question (not once for the whole query),
    # plus one search on the question's topic words alone (graph.retrieve).
    assert len(seen_queries) == len(result.sub_questions) + 1
    assert seen_queries[-1] not in result.sub_questions
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
    # The initial retrieval, one search on the topic words alone (nothing on
    # the first pass was on topic), then exactly MAX_REWRITES retries —
    # bounded, no infinite loop.
    assert calls["n"] == MAX_REWRITES + 2


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


class _UngroundedReasoner(HeuristicReasoner):
    """Writes a claim its passages do not make, as ``mode`` would."""

    def __init__(self, mode: str) -> None:
        super().__init__()
        self._mode = mode

    async def generate(self, query, chunks, contradiction):  # type: ignore[no-untyped-def]
        from app.graph.reasoner import GenerationOutput

        return GenerationOutput(
            text="Apixaban cures every cancer within days [1].",
            ordered_chunks=chunks,
            mode=self._mode,
        )


# Distinct passages: copies of one chunk collapse into a single passage at
# retrieval, which is graded too thin to answer from — the graph would then
# abstain before it ever wrote (and checked) an answer.
_APIXABAN = [
    _chunk("Apixaban reduces stroke in atrial fibrillation compared with warfarin.", score=0.7),
    _chunk("In atrial fibrillation, apixaban lowered the risk of stroke or embolism.", score=0.66),
    _chunk("Apixaban reduced major bleeding in atrial fibrillation versus warfarin.", score=0.62),
    _chunk("Atrial fibrillation patients on apixaban had fewer strokes.", score=0.58),
]


async def _run_recording(graph: AgentGraph, query: str) -> tuple[AnswerResult, list[str]]:
    stages: list[str] = []

    async def emit(event: GraphEvent) -> None:
        stages.append(event.stage)

    return await graph.run(query, emit=emit), stages


async def test_an_ungrounded_model_answer_is_replaced_by_its_passages_words() -> None:
    """A model's claim its passages don't make is never served. The passages
    were good enough to answer from, so the answer quotes them instead of
    abstaining over the model's wording."""
    result, stages = await _run_recording(
        AgentGraph(_fixed(_APIXABAN), _UngroundedReasoner("llm")),
        "Does apixaban reduce stroke in atrial fibrillation?",
    )
    assert "verifying" in stages and "grounding_fallback" in stages
    assert not result.abstained
    assert "cures every cancer" not in result.answer
    assert "stroke" in result.answer and result.generation_mode == "extractive"
    assert result.citations and all(c.marker >= 1 for c in result.citations)


async def test_grounding_failure_with_nothing_to_fall_back_on_is_withheld() -> None:
    """Quoted answers are the fallback, so one that fails the check has none:
    it is withheld, and the reason given is the check — not thin evidence."""
    result, stages = await _run_recording(
        AgentGraph(_fixed(_APIXABAN), _UngroundedReasoner("extractive")),
        "Does apixaban reduce stroke in atrial fibrillation?",
    )
    assert "grounding_failed" in stages and "grounding_fallback" not in stages
    assert result.abstained and result.retrieval_grade == "sufficient"
    assert "withheld" in result.answer and "graded" not in result.answer


# -- confidence calibration ----------------------------------------------------------


class _WritingModel(HeuristicReasoner):
    """A model that writes ``text`` as its answer; the rest stays offline."""

    def __init__(self, text: str) -> None:
        super().__init__()
        self._text = text

    async def generate(self, query, chunks, contradiction):  # type: ignore[no-untyped-def]
        from app.graph.reasoner import GenerationOutput

        return GenerationOutput(text=self._text, ordered_chunks=chunks, mode="llm")


def _apixaban(grades: list[str]) -> list[RetrievedChunk]:
    this_year = date.today().year
    return [
        _chunk(c.content, year=this_year, grade=g, score=c.score)
        for c, g in zip(_APIXABAN, grades, strict=True)
    ]


async def test_a_model_answer_lists_and_is_graded_on_the_sources_it_cites() -> None:
    """The model is given every passage and cites some. On the live demo an
    answer citing two ungraded studies was labelled grade A for a
    meta-analysis it never cited, and listed every passage as a source."""
    graph = AgentGraph(
        _fixed(_apixaban(["A", "C", "A", "A"])),
        _WritingModel(
            "In atrial fibrillation, apixaban lowered the risk of stroke or embolism [2]."
        ),
    )
    result = await graph.run("Does apixaban reduce stroke in atrial fibrillation?")
    assert result.generation_mode == "llm" and not result.abstained
    assert [c.marker for c in result.citations] == [2]
    assert result.evidence_grade == "C", "graded on the source it cites, not the best retrieved"
    assert result.confidence == "moderate"


async def test_a_model_answer_that_says_its_sources_miss_the_question_is_low() -> None:
    """Saying the passages do not answer the question is the right thing to
    write, and it passes grounding; it must not carry a high-confidence,
    grade-A label computed from the retrieval."""
    graph = AgentGraph(
        _fixed(_apixaban(["A", "A", "A", "A"])),
        _WritingModel(
            "The cited studies evaluate apixaban in atrial fibrillation, but they do not "
            "provide data on stroke prevention [1][2]."
        ),
    )
    result = await graph.run("Does apixaban reduce stroke in atrial fibrillation?")
    assert result.generation_mode == "llm" and not result.abstained
    assert result.confidence == "low" and result.evidence_grade is None
    assert [c.marker for c in result.citations] == [1, 2]

    # The same passages answered from: high, as before.
    answered = await AgentGraph(
        _fixed(_apixaban(["A", "A", "A", "A"])),
        _WritingModel("Apixaban reduces stroke in atrial fibrillation compared with warfarin [1]."),
    ).run("Does apixaban reduce stroke in atrial fibrillation?")
    assert answered.confidence == "high" and answered.evidence_grade == "A"


async def test_a_model_answer_keeps_the_sources_of_a_disagreement_it_surfaces() -> None:
    """The conflict panel names both sides' sources; an answer citing one
    side still lists the other."""
    graph = AgentGraph(
        _fixed(_CONFLICT),
        _WritingModel(
            "The 2019 guideline recommends statins as first-line in primary prevention [1]."
        ),
    )
    result = await graph.run("Should statins be used for primary prevention?")
    assert result.contradiction.detected and result.generation_mode == "llm"
    sides = {m for p in result.contradiction.positions for m in p.markers}
    assert sides - {1}, "the other side of the disagreement has sources of its own"
    assert {c.marker for c in result.citations} == sides | {1}


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
        decompose=False,
        grade_and_rewrite=False,
        contradiction=False,
        grounding=False,
        topic_filter=False,
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
