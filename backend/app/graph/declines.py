"""Does a model's answer say its passages do not answer the question?

Given passages that miss the question, a model often says so rather than
answer it: "The cited literature does not address the role of metformin as
a first-line agent for type 2 diabetes…". That is the right thing to write,
and grounding passes it — it invents nothing. But confidence was computed
from retrieval alone (grades, dates, scores), so on the live demo answers
like that one were labelled "high confidence, grade A". An answer that
declines is marked low confidence, with no grade (graph.assess_confidence).

A sentence declines when three things hold together:

- the passages themselves are its subject or its setting — "the cited
  literature", "the provided passages", "no cited source", "the studies
  described" — so a finding such as "aspirin did not reduce events [2]" is
  not one;
- what they lack is coverage, not an effect — "does not address", "do not
  provide data on", "no source … supports", or, with nothing cited for it,
  "is not supported by … data in the cited literature" — so "the cited
  trials did not show a benefit" is not one either;
- it is about the question: it carries the question's topic words, as a
  passage must to be used (relevance.required_hits), so a caveat such as
  "the cited trials did not include patients over 80" is not one.

Only the lead is read — the first sentence, or the second after an uncited
opening — because a caveat further on does not turn an answer into a
refusal. A false alarm costs a label one step too modest, never a claim.
"""

from __future__ import annotations

import re

from app.graph.relevance import core_words, required_hits, topic_hits
from app.guardrails.grounding import split_sentences
from app.knowledge.terms import search_term
from app.services.stance import markers_in

# Models write a non-breaking hyphen in "first-line" and a curly apostrophe in "doesn't".
_PLAIN = str.maketrans(
    {
        "\N{HYPHEN}": "-",
        "\N{NON-BREAKING HYPHEN}": "-",
        "\N{RIGHT SINGLE QUOTATION MARK}": "'",
    }
)

_SOURCE = (
    r"(?:literature|stud(?:y|ies)|sources?|passages?|evidence|articles?|papers?|data|"
    r"trials?|investigations?|references?|excerpts?|abstracts?|information|publications?)"
)
# The passages as such, however the model refers to them.
_THE_PASSAGES = re.compile(
    r"\b(?:(?:cited|provided|retrieved|supplied|included|listed|numbered|available|above)\s+"
    r"(?:\w+\s+)?" + _SOURCE + "|" + _SOURCE + r"\s+(?:provided|cited|retrieved|supplied|"
    r"included|listed|reviewed|described|given)|(?:these|those|the)\s+(?:passages|excerpts|"
    r"abstracts)|these\s+" + _SOURCE + r")\b"
)

_NOT = (
    r"\b(?:do(?:es)?\s+not|did\s+not|don't|doesn't|didn't|cannot|can't|fail(?:s|ed)?\s+to)"
    r"\s+(?:\w+ly\s+)?"
)
# Coverage, not an effect: "provide data on" is about what a study looked
# at; "provide evidence of benefit" is what it found, so it is left out.
_COVERS = (
    r"(?:address|answer|cover|discuss|mention|examine|evaluate|assess|investigate|study|"
    r"describe|consider|speak\s+to|pertain\s+to|focus\s+on|report\s+on|include|contain|"
    r"(?:provide|report|offer|present|give)\s+(?:\w+\s+){0,2}?"
    r"(?:data|information|results|evidence|findings|outcomes?)\b(?!\s+(?:of|that)\b))"
)
_COVER_STEM = (
    r"(?:address|answer|cover|discuss|mention|examin|evaluat|assess|investigat|stud|describ|"
    r"consider|report|provid|includ|contain|focus)"
)
_LACKS = tuple(
    re.compile(pattern)
    for pattern in (
        # "does not address", "do not provide data on", "fail to report on"
        _NOT + _COVERS,
        # "is not addressed in the provided passages"
        r"\b(?:is|are|was|were|been)\s+not\s+(?:\w+ly\s+)?(?:addressed|answered|covered|"
        r"discussed|mentioned|examined|evaluated|assessed|investigated|studied|described|"
        r"considered|reported)\b",
        # "none of the passages addresses"
        r"\bnone\s+of\b[^.;]{0,80}?\b" + _COVER_STEM,
        # "no source among the provided passages supports", "no cited source provides"
        r"\bno\s+(?:\w+\s+){0,2}?(?:source|study|studies|passage|article|paper|reference|"
        r"publication|excerpt)s?\b[^.;]{0,120}?\b(?:" + _COVER_STEM + r"|support)",
        # "no data on fracture incidence in these passages"
        r"\bno\s+(?:\w+\s+){0,2}?(?:data|evidence|information)\s+(?:on|about|regarding|"
        r"concerning|addressing|in|from|among)\b",
        r"\bsilent\s+on\b|\binsufficient\s+to\s+(?:answer|address|determine)\b",
        r"\bcannot\s+be\s+(?:answered|determined|addressed|assessed)\b",
    )
)
# "is not supported by direct outcome data in the cited literature" declines
# when nothing is cited for it; "routine use is not supported by the trials
# [2]" is a conclusion drawn from them.
_UNSUPPORTED_BY = re.compile(r"\bnot\s+(?:\w+ly\s+)?(?:supported|substantiated)\s+by\b")


def says_passages_lack(sentence: str) -> bool:
    """Does ``sentence`` say the passages lack something (see module doc)?"""
    text = sentence.translate(_PLAIN).lower()
    if not _THE_PASSAGES.search(text):
        return False
    if any(pattern.search(text) for pattern in _LACKS):
        return True
    return not markers_in(sentence) and bool(_UNSUPPORTED_BY.search(text))


def declines(question: str, answer: str) -> bool:
    """Does ``answer`` open by saying its passages do not answer ``question``?"""
    needed = required_hits(len(core_words(search_term(question, max_words=10))))
    for sentence in split_sentences(answer)[:2]:
        on_question = topic_hits(question, sentence.translate(_PLAIN)) >= needed
        if on_question and says_passages_lack(sentence):
            return True
        if markers_in(sentence):
            break  # it opened with a cited statement: it answers
    return False
