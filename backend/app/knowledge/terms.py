"""From a question as people ask it to a literature search term.

PubMed's automatic term mapping turns plain words into MeSH well ("kidney
stones" finds nephrolithiasis), but it matches every word it is given, so the
conversational part of a question has to go first: "what should I take for
sugar, doctor?" must search for diabetes, not for "take" and "doctor". The
lay-term table covers the everyday names that do not map by themselves —
common in India, where the words people use ("sugar", "loose motions",
"piles") are not the ones indexers use.
"""

from __future__ import annotations

import re

_WORD = re.compile(r"[a-z0-9][a-z0-9'\-]*")

# Words that carry the asking, not the topic.
_CONVERSATIONAL = frozenset(
    """
    a an the of to in on at by for with from and or but if then so as is are was were be been
    being am do does did doing have has had having can could would should shall will may might
    must what which who whom whose why how when where whether there here this that these those
    it its i me my mine myself we us our you your yours he him his she her they them their
    please tell explain describe know want need help about any some much many more most very
    really just also get got give given take taking took use using used doctor doctors dr sir
    madam hi hello hey thanks thank okay ok kindly suggest suggestion advice advise let lets
    im i'm ive i've dont don't cant can't whats what's hows how's is it anything something
    thing things best good better right correct proper normal ideal okay safe way ways
    currently now today day days since feel feeling felt suffering suffer having
    latest recent recently new newest update updates updated research studies study evidence
    """.split()  # noqa: SIM905 — a word list reads as prose
)

# Everyday names → the terms the literature is indexed under.
LAY_TERMS: dict[str, str] = {
    "sugar": "diabetes mellitus",
    "sugar disease": "diabetes mellitus",
    "high sugar": "hyperglycemia",
    "low sugar": "hypoglycemia",
    "bp": "hypertension",
    "high bp": "hypertension",
    "low bp": "hypotension",
    "heart attack": "myocardial infarction",
    "brain stroke": "stroke",
    "paralysis": "stroke",
    "loose motion": "diarrhea",
    "loose motions": "diarrhea",
    "motions": "diarrhea",
    "piles": "hemorrhoids",
    "fits": "seizures",
    "jaundice": "jaundice hepatitis",
    "cold": "common cold",
    "flu": "influenza",
    "tb": "tuberculosis",
    "sugar patient": "diabetes mellitus",
    "thyroid": "thyroid disease",
    "gas": "dyspepsia",
    "acidity": "gastroesophageal reflux",
    "heartburn": "gastroesophageal reflux",
    "kidney stone": "nephrolithiasis",
    "kidney stones": "nephrolithiasis",
    "stone in kidney": "nephrolithiasis",
    "urine infection": "urinary tract infection",
    "burning urine": "urinary tract infection dysuria",
    "burning while urinating": "urinary tract infection dysuria",
    "chest pain": "chest pain",
    "body pain": "myalgia",
    "body ache": "myalgia",
    "joint pain": "arthralgia",
    "back pain": "low back pain",
    "white discharge": "vaginal discharge",
    "periods": "menstruation",
    "irregular periods": "menstrual irregularity",
    "pcod": "polycystic ovary syndrome",
    "pcos": "polycystic ovary syndrome",
    "dengue fever": "dengue",
    "typhoid fever": "typhoid fever",
    "worms": "helminthiasis",
    "hair fall": "alopecia",
    "pimples": "acne vulgaris",
    "dandruff": "seborrheic dermatitis",
    "motion sickness": "motion sickness",
    "sinus": "sinusitis",
    "allergy": "hypersensitivity",
    "vomiting": "vomiting",
    "fever": "fever",
    "cough": "cough",
    "headache": "headache",
    "migraine": "migraine",
}

_LAY_PATTERNS: list[tuple[re.Pattern[str], str]] = sorted(
    ((re.compile(rf"\b{re.escape(lay)}\b", re.I), term) for lay, term in LAY_TERMS.items()),
    key=lambda pair: -len(pair[0].pattern),  # longest phrase first
)

# "What should I do / take", "how to treat": the answer wanted is treatment,
# a word the conversational filter would otherwise throw away.
_WANTS_TREATMENT = re.compile(
    r"\b(?:what\s+(?:to|should\s+i|can\s+i)\s+(?:do|take|give)|how\s+(?:to|do\s+i|can\s+i)\s+"
    r"(?:treat|cure|manage|get\s+rid)|treat\w*|cure|remed\w*|medicine|medication)\b",
    re.I,
)
_RECENT = re.compile(r"\b(?:latest|recent|recently|new|newest|updated?|20[2-3]\d)\b", re.I)


def with_medical_terms(question: str) -> str:
    """The question with the indexed names of its lay terms added, so the
    stored corpus is searched for "diarrhea" when someone wrote "loose
    motions" (the words themselves stay: they may matter too)."""
    added = [
        term
        for pattern, term in _LAY_PATTERNS
        if pattern.search(question) and term.lower() not in question.lower()
    ]
    return f"{question} {' '.join(dict.fromkeys(added))}".strip() if added else question


def asks_for_recent(question: str) -> bool:
    """Does the question ask what is new, rather than what is established?"""
    return bool(_RECENT.search(question))


def search_term(question: str, *, max_words: int = 8) -> str:
    """The topic of ``question`` as a PubMed term ("" when nothing is left).

    Lay phrases are replaced by their indexed names first; what remains is
    the question's content words in their original order, capped so a long
    message does not become an over-specific search that finds nothing."""
    text = question.lower()
    mapped: list[str] = []
    for pattern, term in _LAY_PATTERNS:
        if pattern.search(text):
            mapped.append(term)
            text = pattern.sub(" ", text)
    words: list[str] = []
    for word in _WORD.findall(text):
        word = word.strip("'-")
        if len(word) < 3 or word in _CONVERSATIONAL or word.isdigit():
            continue
        if word not in words:
            words.append(word)
    parts: list[str] = []
    for piece in [*mapped, *words]:
        if piece not in parts:
            parts.append(piece)
    if (
        parts
        and _WANTS_TREATMENT.search(question)
        and not any(p.startswith(("treat", "therap", "manag")) for p in parts)
    ):
        parts.append("treatment")
    return " ".join(" ".join(parts).split()[:max_words])
