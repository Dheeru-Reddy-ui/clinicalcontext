"""Comparison mode — a structured entity-by-outcome table.

Decomposes the question per entity and per outcome, retrieves per cell, and
builds a table where **every cell carries its own citations and evidence
grade**. A cell whose retrieval is graded insufficient renders honestly as
"Insufficient evidence." — never guessed.
"""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
from typing import Any

from app.graph.reasoner import Reasoner
from app.retrieval.types import RetrievedChunk
from app.schemas.answer import AnswerResult, Citation, Contradiction

RetrieveFn = Callable[[str], Awaitable[list[RetrievedChunk]]]

_DEFAULT_OUTCOMES = ["efficacy", "safety"]
_GRADE_ORDER = {"A": 3, "B": 2, "C": 1, "D": 0}


def _lead_sentence(text: str) -> str:
    parts = re.split(r"(?<=[.!?])\s+", text.strip(), maxsplit=1)
    return (parts[0] if parts else text)[:300].strip()


class ComparisonBuilder:
    def __init__(self, retrieve_fn: RetrieveFn, reasoner: Reasoner) -> None:
        self._retrieve = retrieve_fn
        self._reasoner = reasoner
        self._citations: list[Citation] = []

    async def build(self, query: str, entities: list[str], pico: dict[str, Any] | None) -> Any:
        outcomes = self._outcomes(pico)
        context = _context_clause(query, entities)
        yield {
            "stage": "comparing",
            "message": f"Comparing {len(entities)} options…",
            "data": {"entities": entities, "outcomes": outcomes},
        }

        cells: list[dict[str, Any]] = []
        marker = 0
        for entity in entities:
            for outcome in outcomes:
                sub_query = f"{entity} {outcome} {context}".strip()
                yield {
                    "stage": "searching",
                    "message": f"{entity} — {outcome}…",
                    "data": {"cell_query": sub_query},
                }
                chunks = await self._retrieve(sub_query)
                # A cell may only claim evidence about THIS entity. Retrieval
                # scores the whole sub-query, so a passage can rank highly on
                # the outcome word alone ("efficacy") while never mentioning the
                # entity — citing it here would attribute evidence to a drug the
                # source never discusses. Keep only passages that name it.
                on_entity = [c for c in chunks if _mentions(c.content, entity)]
                grade = await self._reasoner.grade_retrieval(sub_query, on_entity)
                if grade != "sufficient" or not on_entity:
                    cells.append(_insufficient_cell(entity, outcome))
                    continue
                top = on_entity[0]
                marker += 1
                self._citations.append(_citation(marker, top))
                cells.append(
                    {
                        "entity": entity,
                        "outcome": outcome,
                        "summary": _lead_sentence(top.content),
                        "citations": [marker],
                        "evidence_grade": top.evidence_grade,
                        "sufficient": True,
                    }
                )

        table = {"entities": entities, "outcomes": outcomes, "cells": cells}
        yield {
            "stage": "comparison_result",
            "message": "Comparison complete.",
            "data": {"comparison_table": table},
        }

    def _outcomes(self, pico: dict[str, Any] | None) -> list[str]:
        if pico and pico.get("outcome"):
            return [str(pico["outcome"])]
        return _DEFAULT_OUTCOMES

    def to_answer_result(
        self, query: str, entities: list[str], table: dict[str, Any], model: str
    ) -> AnswerResult:
        cells = table["cells"]
        sufficient = [c for c in cells if c["sufficient"]]
        grades = [c["evidence_grade"] for c in sufficient if c.get("evidence_grade")]
        best = max(grades, key=lambda g: _GRADE_ORDER.get(g, -1)) if grades else None
        answer = (
            f"Structured comparison of {', '.join(entities)} across "
            f"{', '.join(table['outcomes'])}. See the comparison table; "
            f"{len(cells) - len(sufficient)} cell(s) had insufficient evidence."
        )
        return AnswerResult(
            query=query,
            query_type="guideline_comparison",
            is_multi_hop=True,
            sub_questions=[f"{c['entity']} — {c['outcome']}" for c in cells],
            abstained=not sufficient,
            answer=answer if sufficient else "Insufficient evidence to compare these options.",
            citations=self._citations,
            contradiction=Contradiction(detected=False),
            confidence="moderate" if sufficient else "low",
            evidence_grade=best,
            retrieval_grade="sufficient" if sufficient else "insufficient",
            model=model,
            generation_mode="extractive",
        )


def _mentions(text: str, entity: str) -> bool:
    """Does this passage actually discuss the entity?

    Every meaningful token of the entity name must appear (prefix match, so
    "anticoagulant" satisfies "anticoagulation"). Deliberately strict: a false
    negative downgrades a cell to "insufficient evidence", which is the honest
    direction to err in; a false positive would put someone else's evidence
    under this entity's name.
    """
    terms = [t for t in re.findall(r"[a-z0-9]+", entity.lower()) if len(t) > 2]
    if not terms:
        return False
    lowered = text.lower()
    return all(re.search(rf"\b{re.escape(term)}", lowered) for term in terms)


def _context_clause(query: str, entities: list[str]) -> str:
    """Strip the compared entities from the query, leaving the clinical context."""
    context = query
    for entity in entities:
        context = re.sub(re.escape(entity), "", context, flags=re.I)
    context = re.sub(r"\b(?:compare|comparison|versus|vs\.?|and|or)\b", " ", context, flags=re.I)
    return re.sub(r"\s+", " ", context).strip(" ,.?")


def _insufficient_cell(entity: str, outcome: str) -> dict[str, Any]:
    return {
        "entity": entity,
        "outcome": outcome,
        "summary": "Insufficient evidence.",
        "citations": [],
        "evidence_grade": None,
        "sufficient": False,
    }


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
