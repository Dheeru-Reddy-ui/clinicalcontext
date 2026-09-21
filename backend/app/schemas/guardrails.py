"""Typed guardrail verdicts.

A guardrail never leaks what it caught: PHI findings carry entity *types and
counts*, never the matched values — so logs, audit rows, and
``queries.guardrail_verdict`` can be persisted freely without re-introducing
the PHI the guardrail just blocked.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

GuardrailAction = Literal["allow", "block", "escalate"]
GuardrailStage = Literal["phi", "scope", "red_flag", "grounding"]
# Persisted outcome of the pre-retrieval guardrails for a query.
GuardrailStatus = Literal["clean", "blocked", "escalated"]

# Stable machine codes (surfaced to the UI and recorded in the audit trail).
GuardrailCode = Literal[
    "clean",
    "phi_detected",
    "refuse_diagnosis",
    "refuse_dosing",
    "refuse_personal_medical",
    "refuse_prompt_injection",
    "refuse_out_of_scope",
    "red_flag_emergency",
    "grounding_ok",
    "grounding_pruned",
    "grounding_rejected",
]


class GuardrailFinding(BaseModel):
    """One guardrail's decision about a request (or a generated answer)."""

    model_config = ConfigDict(frozen=True)

    stage: GuardrailStage
    action: GuardrailAction
    code: GuardrailCode
    message: str
    # Structured, PHI-safe: entity types/counts, matched category names,
    # sentence tallies — never raw user text or detected values.
    detail: dict[str, Any] = Field(default_factory=dict)


class PreRetrievalVerdict(BaseModel):
    """Composite of the pre-retrieval guardrails (PHI → scope → red-flag).

    ``allowed`` is false iff a blocking guardrail (PHI or scope) fired. A
    red-flag is non-blocking: the query still runs, but ``escalation_banner``
    must be rendered before any retrieved content.
    """

    allowed: bool
    blocked_by: GuardrailStage | None = None
    message: str | None = None
    escalation_banner: str | None = None
    findings: list[GuardrailFinding] = Field(default_factory=list)

    def as_verdict_json(self) -> dict[str, Any]:
        """Shape stored in queries.guardrail_verdict / audit_log payloads."""
        return {
            "allowed": self.allowed,
            "blocked_by": self.blocked_by,
            "escalation": self.escalation_banner is not None,
            "findings": [f.model_dump() for f in self.findings],
        }


SentenceSupport = Literal["supported", "partially_supported", "unsupported", "no_citation"]


class SentenceVerdict(BaseModel):
    model_config = ConfigDict(frozen=True)

    index: int
    text: str
    is_clinical_claim: bool
    support: SentenceSupport
    cited_markers: list[int] = Field(default_factory=list)


class GroundingVerdict(BaseModel):
    """Post-generation grounding outcome over a whole answer."""

    accepted: bool
    finding: GuardrailFinding
    kept_answer: str
    removed_sentences: list[SentenceVerdict] = Field(default_factory=list)
    sentence_verdicts: list[SentenceVerdict] = Field(default_factory=list)
    removed_fraction: float = 0.0
