"""Study-type classification and evidence grading.

Two-stage: deterministic rules over PubMed publication types / MeSH terms
first (free, exact, covers most of the corpus), an LLM classifier only where
metadata is silent. Every decision records its method, its signals, and its
reasoning — a grade without provenance is worthless in a clinical tool.

Grade mapping (per the engineering brief):
  A: systematic review / meta-analysis (and large RCTs — size is not
     reliably derivable from metadata, so RCTs default to B with the
     limitation stated in the reasoning); clinical guidelines (evidence
     syntheses) also grade A.
  B: RCT / cohort study
  C: case-control / case series (and non-randomized trials)
  D: case report / expert opinion (narrative reviews, editorials)
"""

from __future__ import annotations

import json
import re

import structlog
from anthropic import AsyncAnthropic, AuthenticationError

from app.config import get_settings
from app.ingestion.models import Classification, EvidenceGrade, RawDocument, StudyType
from app.prompts.loader import load_prompt

logger = structlog.stdlib.get_logger("app.ingestion.classifier")

CLASSIFIER_MODEL = "claude-sonnet-4-6"

GRADE_BY_STUDY_TYPE: dict[StudyType, EvidenceGrade] = {
    "meta_analysis": "A",
    "systematic_review": "A",
    "clinical_guideline": "A",
    "randomized_controlled_trial": "B",
    "cohort_study": "B",
    "case_control_study": "C",
    "case_series": "C",
    "case_report": "D",
    "narrative_review": "D",
    "other": "D",
}

# Publication types / MeSH descriptors → study type, in priority order:
# the strongest design wins when a record carries several labels
# (e.g. "Randomized Controlled Trial" + "Meta-Analysis" → meta_analysis).
_RULES: list[tuple[str, StudyType]] = [
    ("meta-analysis", "meta_analysis"),
    ("systematic review", "systematic_review"),
    ("randomized controlled trial", "randomized_controlled_trial"),
    ("practice guideline", "clinical_guideline"),
    ("guideline", "clinical_guideline"),
    ("consensus development conference", "clinical_guideline"),
    ("observational study", "cohort_study"),
    ("cohort studies", "cohort_study"),
    ("case-control studies", "case_control_study"),
    ("case reports", "case_report"),
    ("clinical trial", "other"),  # non-randomized trial: graded C below
    ("review", "narrative_review"),
    ("editorial", "narrative_review"),
    ("comment", "narrative_review"),
    ("letter", "narrative_review"),
]

_RCT_SIZE_NOTE = (
    " Sample size is not derivable from metadata, so the RCT defaults to grade B "
    "rather than A (reserved for large RCTs)."
)


def classify_from_metadata(document: RawDocument) -> Classification | None:
    """Deterministic classification from publication types + MeSH. None when silent."""
    signals = [s.lower() for s in (*document.publication_types, *document.mesh_terms)]
    if not signals:
        return None
    for needle, study_type in _RULES:
        matched = [s for s in signals if needle in s]
        if not matched:
            continue
        grade: EvidenceGrade = (
            "C" if needle == "clinical trial" else GRADE_BY_STUDY_TYPE[study_type]
        )
        reasoning = (
            f"Publication metadata contains {matched[0]!r}, which maps to "
            f"{study_type} (grade {grade})."
        )
        if study_type == "randomized_controlled_trial":
            reasoning += _RCT_SIZE_NOTE
        if needle == "clinical trial":
            reasoning = (
                f"Publication metadata contains {matched[0]!r} without randomization "
                "markers — treated as a non-randomized trial (grade C)."
            )
        return Classification(
            study_type=study_type,
            evidence_grade=grade,
            method="publication_types",
            reasoning=reasoning,
            signals=matched[:5],
        )
    return None


async def classify_with_llm(document: RawDocument) -> Classification:
    """LLM fallback for records without design metadata."""
    prompt = load_prompt("classify_study_type", 1)
    settings = get_settings()
    client = AsyncAnthropic(api_key=settings.anthropic_api_key.get_secret_value())
    excerpt = (document.abstract or document.full_text())[:4000]
    response = await client.messages.create(
        model=CLASSIFIER_MODEL,
        max_tokens=300,
        temperature=0.0,
        system=prompt.text,
        messages=[
            {
                "role": "user",
                "content": f"Title: {document.title}\n\nAbstract:\n{excerpt or '(none)'}",
            }
        ],
    )
    raw = "".join(block.text for block in response.content if block.type == "text")
    study_type, reasoning = _parse_llm_response(raw)
    return Classification(
        study_type=study_type,
        evidence_grade=GRADE_BY_STUDY_TYPE[study_type],
        method="llm",
        reasoning=reasoning,
        signals=[],
        model=CLASSIFIER_MODEL,
        prompt_version=prompt.version_tag,
    )


def _parse_llm_response(raw: str) -> tuple[StudyType, str]:
    match = re.search(r"\{.*\}", raw, re.DOTALL)
    if match:
        try:
            payload = json.loads(match.group())
            candidate = str(payload.get("study_type", "")).strip()
            if candidate in GRADE_BY_STUDY_TYPE:
                reasoning = str(payload.get("reasoning", "")).strip() or "LLM classification."
                return candidate, reasoning
        except json.JSONDecodeError:
            pass
    logger.warning("llm_classification_unparseable", raw=raw[:200])
    return "other", f"LLM response was unparseable; defaulted to 'other'. Raw: {raw[:120]}"


async def backfill_classifications(pool: object, *, limit: int | None = None) -> int:
    """Re-classify documents left ungraded when the LLM was unavailable.

    Resumable and idempotent, mirroring the embedding backfill: reconstructs a
    minimal RawDocument from the stored row, runs the LLM classifier, and
    writes the study_type / evidence_grade / reasoning back. Returns the count
    reclassified (records that stay 'other'/ungraded are not counted).
    """
    import asyncpg  # local import keeps this module import-light

    from app.repositories.corpus import CorpusRepository

    assert isinstance(pool, asyncpg.Pool)
    repo = CorpusRepository()
    reclassified = 0
    batch = 32
    async with pool.acquire() as conn:
        while True:
            remaining = batch if limit is None else min(batch, limit - reclassified)
            if remaining <= 0:
                break
            rows = await repo.fetch_documents_pending_classification(conn, limit=remaining)
            if not rows:
                break
            progress_before = reclassified
            for row in rows:
                metadata = row["metadata"]
                if isinstance(metadata, str):
                    metadata = json.loads(metadata)
                document = RawDocument(
                    source_type="pubmed",
                    title=str(row["title"]),
                    sections=[],
                    abstract=row["abstract"],
                    publication_types=list(metadata.get("publication_types", [])),
                    mesh_terms=list(metadata.get("mesh_terms", [])),
                )
                result = await classify_document(document, allow_llm=True)
                await repo.update_classification(
                    conn,
                    document_id=row["id"],
                    study_type=result.study_type,
                    evidence_grade=result.evidence_grade,
                    classification={
                        "study_type": result.study_type,
                        "evidence_grade": result.evidence_grade,
                        "method": result.method,
                        "reasoning": result.reasoning,
                        "signals": result.signals,
                        "model": result.model,
                        "prompt_version": result.prompt_version,
                    },
                )
                if result.study_type is not None:
                    reclassified += 1
            if reclassified == progress_before:
                # A whole batch made no progress → the LLM is unavailable (or
                # every record is genuinely unclassifiable). Stop rather than
                # re-fetching the same still-NULL rows forever.
                logger.warning("classification_backfill_no_progress", pending=len(rows))
                break
            if len(rows) < remaining:
                break
    return reclassified


async def classify_document(document: RawDocument, *, allow_llm: bool = True) -> Classification:
    """Full classification: rules first, LLM fallback, honest 'unclassified' last."""
    if document.source_type == "drug_label":
        # The regulator's label: authoritative on dosing and safety, and no
        # evidence of efficacy at all — so its own type, and no grade.
        return Classification(
            study_type="drug_label",
            evidence_grade=None,
            method="publication_types",
            reasoning="FDA structured product label (openFDA); regulatory text, not graded.",
            signals=["source:drug_label"],
        )
    from_rules = classify_from_metadata(document)
    if from_rules is not None:
        return from_rules
    if allow_llm:
        try:
            return await classify_with_llm(document)
        except AuthenticationError:
            logger.warning("llm_classifier_unavailable", reason="invalid ANTHROPIC_API_KEY")
    return Classification(
        study_type=None,
        evidence_grade=None,
        method="unclassified",
        reasoning=(
            "No design metadata present and the LLM classifier was unavailable; "
            "left ungraded rather than guessed."
        ),
        signals=[],
    )
