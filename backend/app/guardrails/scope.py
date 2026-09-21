"""Scope / refusal classifier.

Refuses framings the tool must not answer, while *allowing* general literature
questions that merely mention dosing or diagnosis. The line is
individualization: "what is the recommended dose of apixaban in CKD?" is a
literature question (allow); "how much apixaban should I give my patient?" is
individualized dosing (refuse).

Two layers:
- a fast deterministic keyword/pattern pre-filter — the backbone that makes the
  diagnosis-refusal gate pass with no LLM, and that cannot be disabled by
  injected instructions; and
- an optional LLM classifier (versioned prompt) for rephrasings the patterns
  miss. It can only *add* a refusal, never overturn one — fail-safe.

It also flags prompt-injection attempts ("ignore previous instructions…").
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

import structlog

from app.schemas.guardrails import GuardrailCode

logger = structlog.stdlib.get_logger("app.guardrails.scope")

ScopeVerdict = Literal[
    "allow",
    "refuse_diagnosis",
    "refuse_dosing",
    "refuse_personal_medical",
    "refuse_prompt_injection",
]

# Patient subject: an individualized reference. "my/this patient" is the strong
# marker; bare pronouns count only inside a diagnostic frame ("does he have…").
_SUBJ = r"(?:my\s+patient|this\s+patient|the\s+patient|he|she|they)"

# -- diagnosis (non-negotiable: must be caught deterministically) --------------------
# Specific diagnostic structures that are individualized by construction — a
# general literature question does not ask "does the patient have X".
_DIAGNOSIS_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\bdiagnose\s+(?:my|this|the)\s+patient\b", re.I),
    re.compile(rf"\b(?:does|do|could|might|would)\s+{_SUBJ}\s+have\b", re.I),
    re.compile(rf"\bwhat\s+(?:does|might|could)\s+{_SUBJ}\s+have\b", re.I),
    re.compile(rf"\bwhat\s+(?:condition|disease|illness)\s+does\s+{_SUBJ}\s+have\b", re.I),
    re.compile(rf"\bis\s+{_SUBJ}\s+(?:having|experiencing|suffering)\b", re.I),
    re.compile(rf"\bwhat(?:'s| is)?\s+wrong\s+with\s+{_SUBJ}\b", re.I),
    # patient subject within 40 chars of explicit diagnostic language (either order)
    re.compile(r"\b(?:my|this)\s+patient(?:'s)?\b.{0,60}\b(?:diagnos\w+|suffering\s+from)", re.I),
    re.compile(
        r"\b(?:diagnos\w+|suffering\s+from).{0,60}\b(?:for\s+)?(?:my|this)\s+patient\b", re.I
    ),
    re.compile(rf"\bwhat(?:'s| is)?\s+(?:the\s+)?diagnosis\b.{{0,40}}\b{_SUBJ}\b", re.I),
    re.compile(
        r"(?:based on|given)\s+these\s+symptoms.{0,40}"
        r"\b(?:diagnos|what\s+(?:is|does|condition|disease))",
        re.I,
    ),
)

# -- individualized dosing -----------------------------------------------------------
_PATIENT_OBJ = r"(?:my\s+patient|this\s+patient|him|her|them)"
_DOSING_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(
        r"\b(?:should|shall|do|can|could)\s+i\s+"
        r"(?:give|administer|prescribe|start|increase|decrease|dose|up|down)\b",
        re.I,
    ),
    re.compile(r"\b(?:what|which|how\s+much)\b.{0,30}\bdos(?:e|age)\b.{0,40}\bi\b", re.I),
    re.compile(r"\bdos(?:e|age)\b.{0,20}\b(?:do|should|shall)\s+i\b", re.I),
    re.compile(rf"\bhow\s+(?:much|many)\b.{{0,60}}\bfor\s+{_PATIENT_OBJ}\b", re.I),
    re.compile(rf"\b(?:dose|dosage|mg|units|tablets)\b.{{0,40}}\bfor\s+{_PATIENT_OBJ}\b", re.I),
    re.compile(rf"\bhow\s+(?:much|many)\b.{{0,40}}\b(?:give|administer)\s+{_PATIENT_OBJ}\b", re.I),
)

# -- patient asking about their own care (redirect to a clinician) -------------------
# "my <own medication>" (not "my patient" — that is the clinician case).
_PERSONAL_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(
        r"should\s+i\s+(?:stop|start|change|skip|double|halve)\s+(?:taking\s+)?my\s+(?!patient\b)",
        re.I,
    ),
    re.compile(r"(?:can|should)\s+i\s+stop\s+taking\b", re.I),
    re.compile(r"is\s+it\s+safe\s+for\s+me\s+to\s+(?:stop|take|combine|mix)\b", re.I),
    re.compile(r"\bmy\s+(?:doctor|physician)\s+(?:prescribed|gave|put)\s+me\b", re.I),
    re.compile(r"should\s+i\s+(?:be\s+)?(?:taking|worried\s+about)\s+my\s+(?!patient\b)", re.I),
)

# -- prompt injection ----------------------------------------------------------------
_INJECTION_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(
        r"ignore\s+(?:all\s+)?(?:your\s+|my\s+)?(?:previous|prior|above|the|earlier)\s+"
        r"instructions",
        re.I,
    ),
    re.compile(
        r"disregard\s+(?:your|the|all)\s+(?:rules|instructions|guardrails|guidelines)", re.I
    ),
    re.compile(r"you\s+are\s+now\s+(?:an?\s+)?(?:unrestricted|dan|jailbroken|uncensored)", re.I),
    re.compile(
        r"(?:reveal|show|print|repeat)\s+(?:your\s+)?(?:system\s+prompt|instructions)", re.I
    ),
    re.compile(
        r"\bpretend\s+(?:you\s+have\s+no|there\s+are\s+no)\s+(?:rules|restrictions|guardrails)",
        re.I,
    ),
    re.compile(
        r"\b(?:bypass|disable|turn\s+off|remove|ignore)\s+"
        r"(?:the\s+|your\s+|all\s+(?:your\s+|the\s+)?)?(?:safety\s+)?"
        r"(?:guardrails?|filters?|guidelines|rules|restrictions|instructions|guidance)\b",
        re.I,
    ),
    re.compile(
        r"\bwithout\s+(?:any\s+)?(?:safety\s+)?(?:restrictions|rules|filters|guardrails)\b", re.I
    ),
)

_REASONS: dict[ScopeVerdict, tuple[GuardrailCode, str]] = {
    "refuse_diagnosis": (
        "refuse_diagnosis",
        "ClinicalContext answers questions about the medical literature, not about "
        "specific individuals. It cannot diagnose a patient. Ask about the evidence "
        "for a condition or presentation in general, and apply clinical judgment to "
        "your patient yourself.",
    ),
    "refuse_dosing": (
        "refuse_dosing",
        "ClinicalContext does not provide individualized dosing for a specific "
        "patient. It can summarize what guidelines and trials report about dosing "
        "in a population; the prescribing decision for an individual must rest with "
        "the treating clinician.",
    ),
    "refuse_personal_medical": (
        "refuse_personal_medical",
        "ClinicalContext is a literature tool for clinicians and cannot advise you "
        "about your own treatment. Please do not stop, start, or change a medication "
        "based on this tool — contact your doctor or pharmacist, and call emergency "
        "services if this is urgent.",
    ),
    "refuse_prompt_injection": (
        "refuse_prompt_injection",
        "This request attempts to override the system's safety instructions, which "
        "is not permitted. Please ask a clinical question about the literature.",
    ),
}


@dataclass(slots=True)
class ScopeResult:
    verdict: ScopeVerdict
    reason_code: GuardrailCode
    message: str
    method: Literal["keyword", "llm", "allow"]


def _match_any(patterns: tuple[re.Pattern[str], ...], text: str) -> bool:
    return any(p.search(text) for p in patterns)


def classify_keyword(query: str) -> ScopeVerdict:
    """Deterministic pre-filter. Order matters: injection and diagnosis first."""
    if _match_any(_INJECTION_PATTERNS, query):
        return "refuse_prompt_injection"
    if _match_any(_DIAGNOSIS_PATTERNS, query):
        return "refuse_diagnosis"
    # Personal ("should I stop taking my inhaler") before dosing: both can match
    # "should I start…", but personal is about the user's own medication.
    if _match_any(_PERSONAL_PATTERNS, query):
        return "refuse_personal_medical"
    if _match_any(_DOSING_PATTERNS, query):
        return "refuse_dosing"
    return "allow"


def _result(verdict: ScopeVerdict, method: Literal["keyword", "llm", "allow"]) -> ScopeResult:
    if verdict == "allow":
        return ScopeResult("allow", "clean", "", "allow")
    code, message = _REASONS[verdict]
    return ScopeResult(verdict, code, message, method)


async def classify_scope(query: str, *, allow_llm: bool = True) -> ScopeResult:
    """Full scope classification: keyword pre-filter, then optional LLM.

    The LLM only runs when the keyword layer allows, and can only turn an
    allow into a refusal — it never overturns a deterministic refusal.
    """
    keyword_verdict = classify_keyword(query)
    if keyword_verdict != "allow":
        return _result(keyword_verdict, "keyword")

    if allow_llm:
        llm_verdict = await _classify_llm(query)
        if llm_verdict != "allow":
            return _result(llm_verdict, "llm")

    return _result("allow", "allow")


async def _classify_llm(query: str) -> ScopeVerdict:
    """LLM refusal classifier. Unavailable key → allow (keyword layer already
    caught the clear cases; this is the enhancement tier)."""
    from anthropic import AsyncAnthropic, AuthenticationError

    from app.config import get_settings
    from app.prompts.loader import load_prompt

    prompt = load_prompt("refusal_classifier", 1)
    try:
        client = AsyncAnthropic(api_key=get_settings().anthropic_api_key.get_secret_value())
        response = await client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=20,
            temperature=0.0,
            system=prompt.text,
            messages=[{"role": "user", "content": query}],
        )
    except AuthenticationError:
        logger.warning("scope_llm_unavailable", reason="invalid ANTHROPIC_API_KEY")
        return "allow"
    except Exception as exc:
        logger.warning("scope_llm_failed", error=f"{type(exc).__name__}: {exc}")
        return "allow"

    raw = "".join(block.text for block in response.content if block.type == "text").strip().lower()
    mapping: dict[str, ScopeVerdict] = {
        "diagnosis": "refuse_diagnosis",
        "dosing": "refuse_dosing",
        "personal": "refuse_personal_medical",
        "injection": "refuse_prompt_injection",
        "allow": "allow",
    }
    for key, verdict in mapping.items():
        if key in raw:
            return verdict
    return "allow"
