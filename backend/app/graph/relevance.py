"""Is this passage about the question? And which of its sentences says so?

Shared by every path that turns retrieved passages into an answer — the
evidence-search graph, the chat assistant and the quoted (no-model) answers —
so "on topic" means the same thing everywhere. Pure text functions: no
database, no model, no dependency on the reasoners that use them.

The rule, learned the hard way on the live site: a passage is on topic when
it carries most of the question's *topic words* — what is left once the
asking ("what is the first-line treatment for…"), the boilerplate
("recommended", "evidence") and the population ("adults", "children") are
removed. Retrieval scores alone let a clozapine trial answer a question about
beta-blockers after myocardial infarction, and a psychiatry paper answer
"scrub typhus in adults" because both said "adults".
"""

from __future__ import annotations

import re
from collections.abc import Sequence

from app.guardrails.grounding import split_sentences
from app.knowledge.terms import search_term
from app.retrieval.types import RetrievedChunk

WORD = re.compile(r"[a-z0-9]+")

STOPWORDS = frozenset(
    """
    the a an of to in and or for with is are was were be on at by as that this does do what
    which how much many should i my patient given these vs versus
    """.split()  # noqa: SIM905 — a word list reads as prose
)

# The topic of a clinical question is what is left once its boilerplate is
# removed: "What antibiotics are recommended for Lyme disease?" is about
# antibiotics and Lyme, not about being recommended or being a disease.
GENERIC = frozenset(
    """
    treatment treatments treat treated treating therapy therapies therapeutic management
    managed manage managing recommended recommend recommendation recommendations guideline
    guidelines current first line first-line second second-line effective effectiveness
    efficacy efficacious evidence safe safety risk risks patient patients disease diseases
    disorder disorders condition conditions prognosis prognostic outcome outcomes diagnosed
    diagnosis diagnostic diagnose prevention prevented prevent preventing use used using given
    give dose doses dosing role accurate accuracy compare compared comparison better best diagnosing
    improve improves improved common factor factors predict predicts predicting syndrome
    acute chronic clinical study studies trial trials evidence-based indicated should
    versus recommended options option choice preferred benefit benefits increase increases
    reduce reduces reduced reduction rate rates level levels care primary secondary
    """.split()  # noqa: SIM905 — a word list reads as prose
)


def stem(token: str) -> str:
    """Just enough to let "antibiotics" meet "antibiotic"."""
    if len(token) > 4 and token.endswith("ies"):
        return token[:-3] + "y"
    if len(token) > 4 and token.endswith("es") and token[-3] in "sxz":
        return token[:-2]
    if len(token) > 4 and token.endswith("s") and not token.endswith("ss"):
        return token[:-1]
    return token


GENERIC_STEMS = frozenset(stem(w) for w in GENERIC)


def is_generic_word(token: str) -> bool:
    """A word that says how a question is asked, not what it is about."""
    lowered = token.lower()
    return lowered in STOPWORDS or stem(lowered) in GENERIC_STEMS


# British and American spellings meet ("diarrhoea" finds "diarrhea").
_SPELLINGS = (
    ("oea", "ea"),
    ("haem", "hem"),
    ("paed", "ped"),
    ("oedem", "edem"),
    ("oesoph", "esoph"),
    ("oestr", "estr"),
    ("ischaem", "ischem"),
    ("anaem", "anem"),
    ("leukaem", "leukem"),
    ("tumour", "tumor"),
    ("foet", "fet"),
)


def us_spelling(text: str) -> str:
    lowered = text.lower()
    for british, american in _SPELLINGS:
        lowered = lowered.replace(british, american)
    return lowered


def _normalized(text: str) -> str:
    """Spelling-, hyphen- and symbol-blind text for word matching:
    "β-blockers", "beta-blockers" and "beta blockers" read the same."""
    return us_spelling(text).replace("β", "beta").replace("-", " ")


# Who a question is about, not what: "in adults", "for children". Every
# second abstract mentions adults, so these must not make a passage count
# as on-topic.
POPULATION = frozenset(
    """
    adult adults child children kid kids infant infants baby babies newborn newborns neonate
    neonates elderly older old aged age young adolescent adolescents teen teens teenager
    teenagers women woman men man male males female females people person persons patient
    patients individual individuals subject subjects population populations year years
    """.split()  # noqa: SIM905 — a word list reads as prose
)


# Words that join a question together rather than name its subject.
FUNCTION_WORDS = frozenset(
    """
    after before over under during without within between against among across into onto
    from than then their there these those when where while whether about also other such
    some more most less least very only same both each either neither have has had been being
    will would could can may might must shall not all any per via like its it's her his them
    they you your our who whom whose why because since until upon versus
    """.split()  # noqa: SIM905 — a word list reads as prose
)

# Clinical abbreviations a question uses and a paper spells out, or the other
# way round: "AF with CKD" is about atrial fibrillation and chronic kidney
# disease. A passage carries the word if it has the abbreviation as a whole
# word or any of its spellings (normalized: hyphens as spaces, US spelling).
ALIASES: dict[str, tuple[str, ...]] = {
    "af": ("atrial fibrillation",),
    "ckd": ("chronic kidney",),
    "aki": ("acute kidney injury",),
    "mi": ("myocardial infarction",),
    "ami": ("myocardial infarction",),
    "hf": ("heart failure",),
    "hfref": ("reduced ejection fraction",),
    "hfpef": ("preserved ejection fraction",),
    "acs": ("acute coronary syndrome",),
    "cad": ("coronary artery disease", "coronary heart disease"),
    "chd": ("coronary heart disease", "coronary artery disease"),
    "stemi": ("st elevation myocardial", "st segment elevation"),
    "nstemi": ("non st elevation", "non st segment"),
    "htn": ("hypertension",),
    "copd": ("chronic obstructive pulmonary", "chronic obstructive lung"),
    "ards": ("acute respiratory distress",),
    "cap": ("community acquired pneumonia",),
    "uti": ("urinary tract infection",),
    "utis": ("urinary tract infection",),
    "tb": ("tuberculosis",),
    "hiv": ("human immunodeficiency virus",),
    "t2dm": ("type 2 diabetes",),
    "t2d": ("type 2 diabetes",),
    "t1dm": ("type 1 diabetes",),
    "t1d": ("type 1 diabetes",),
    "dm": ("diabetes",),
    "pe": ("pulmonary embolism",),
    "dvt": ("deep vein thrombosis", "deep venous thrombosis"),
    "vte": ("venous thromboembolism",),
    "tia": ("transient ischemic attack",),
    "ra": ("rheumatoid arthritis",),
    "sle": ("systemic lupus",),
    "ibd": ("inflammatory bowel",),
    "ibs": ("irritable bowel",),
    "gerd": ("gastroesophageal reflux", "gastro esophageal reflux"),
    "pcos": ("polycystic ovary",),
    "nafld": ("fatty liver",),
    "masld": ("fatty liver",),
    "bph": ("benign prostatic",),
    "ptsd": ("post traumatic stress", "posttraumatic stress"),
    "adhd": ("attention deficit",),
    "ocd": ("obsessive compulsive",),
    "mdd": ("major depressive",),
    "ssri": ("serotonin reuptake",),
    "ssris": ("serotonin reuptake",),
    "nsaid": ("nonsteroidal anti inflammatory", "non steroidal anti inflammatory"),
    "nsaids": ("nonsteroidal anti inflammatory", "non steroidal anti inflammatory"),
    "ppi": ("proton pump",),
    "ppis": ("proton pump",),
    "doac": ("direct oral anticoagulant", "direct acting oral anticoagulant"),
    "doacs": ("direct oral anticoagulant", "direct acting oral anticoagulant"),
    "lmwh": ("low molecular weight heparin",),
    "ace": ("angiotensin converting",),
    "arb": ("angiotensin receptor",),
    "arbs": ("angiotensin receptor",),
    "rsv": ("respiratory syncytial",),
    "hpv": ("human papillomavirus",),
    "ivf": ("in vitro fertili",),
}

# Words about the evidence rather than the subject: "do the guidelines
# disagree", "what is the latest". A passage need not say them to be on topic.
META = frozenset(
    """
    disagree disagreement disagreements differ differs difference differences different
    conflict conflicts conflicting contradict contradicts contradictory agree agreement
    consensus latest newest recent recently update updates updated new
    workup work-up evaluation evaluate approach assessment assess investigation investigations
    left right bilateral
    """.split()  # noqa: SIM905 — a word list reads as prose
)


def core_words(term: str) -> list[str]:
    """The words of ``term`` that carry its topic: not boilerplate
    ("treatment", "first-line", "recommended"), not the population, not
    words about the evidence ("disagree", "latest") and not bare numbers
    (years belong to the sources' dates, not their text).
    A hyphenated word counts as its parts ("beta-blockers" is beta and
    blockers), so a passage about aspirin after an infarction cannot pass
    as one about beta-blockers after it on the strength of the infarction."""
    words: list[str] = []
    for raw in term.lower().split():
        token = raw.strip(".,;:?!()\"'")
        parts = [p for p in token.split("-") if p] if "-" in token else [token]
        min_length = 3 if len(parts) > 1 else 2  # "non-", "pre-" are not topics
        for part in parts:
            if part in ALIASES:
                if part not in words:
                    words.append(part)
                continue
            if (
                len(part) <= min_length
                or part.isdigit()
                or part in POPULATION
                or part in META
                or part in FUNCTION_WORDS
                or is_generic_word(part)
            ):
                continue
            spelled = _normalized(part)
            if spelled not in words:
                words.append(spelled)
    return words


def required_hits(n: int) -> int:
    """How many topic words a passage must carry: all of one or two, and
    three in five of a longer topic."""
    return n if n <= 2 else -(-n * 3 // 5)


# Endings a question and a paper spell the same word with: "anticoagulation"
# and "anticoagulant", "ischemic" and "ischemia", "antidepressants" and
# "antidepressive". Stripped only while at least five letters remain, so the
# stem stays specific.
_ENDINGS = tuple(
    sorted(
        (
            "ations",
            "ation",
            "ants",
            "ant",
            "ents",
            "ent",
            "ives",
            "ive",
            "ically",
            "ical",
            "ics",
            "ic",
            "ias",
            "ia",
            "ies",
            "es",
            "s",
            "ed",
            "ing",
            "ers",
            "er",
        ),
        key=len,
        reverse=True,
    )
)


def match_stem(word: str) -> str:
    """The part of a topic word that a passage must contain."""
    for ending in _ENDINGS:
        if word.endswith(ending) and len(word) - len(ending) >= 5:
            return word[: -len(ending)]
    return word


def _hits(words: list[str], chunk: RetrievedChunk) -> int:
    """How many topic words the passage carries. Matched on the stem, so
    "thresholds" finds "threshold" and "anticoagulation" finds
    "anticoagulant"; an abbreviation matches as a whole word ("AF", not the
    "af" in "after") or by any of its spellings."""
    return _text_hits(words, f"{chunk.title or ''} {chunk.content}")


def topic_hits(question: str, text: str) -> int:
    """How many of the question's topic words a piece of text carries."""
    return _text_hits(core_words(search_term(question, max_words=10)), text)


def _text_hits(words: list[str], text: str) -> int:
    haystack = _normalized(text)
    tokens: set[str] | None = None
    hits = 0
    for word in words:
        spellings = ALIASES.get(word)
        if spellings is None and len(word) > 3:
            hits += match_stem(word) in haystack
            continue
        if tokens is None:
            tokens = set(WORD.findall(haystack))
        if spellings is None:
            hits += word in tokens  # "arm" as a word, not inside "harm"
            continue
        hits += word in tokens or any(spelling in haystack for spelling in spellings)
    return hits


def word_relevance(term: str, chunk: RetrievedChunk) -> float:
    """The share of ``term``'s topic words the passage contains,
    spelling-blind (0.0 when the term has none)."""
    words = core_words(term)
    if not words:
        return 0.0
    return _hits(words, chunk) / len(words)


def about(term: str, chunk: RetrievedChunk) -> bool:
    """Does the passage carry enough of ``term``'s topic to be used?"""
    words = core_words(term)
    if not words:
        return True  # nothing to judge by: keep the retrieval's own ranking
    return _hits(words, chunk) >= required_hits(len(words))


#: When at least this many passages carry every topic word, only those are
#: used: "vitamin D supplementation and fractures" is then answered from
#: papers on fractures, not from a vitamin D paper on urinary infections that
#: happens to share two of the three words.
COMPLETE_ENOUGH = 3


def topic_query(question: str) -> str | None:
    """The question's topic words alone ("aspirin cardiovascular") — a
    search that finds the papers about the subject when the full question's
    wording ranks general ones first. None when there is nothing to add."""
    words = core_words(search_term(question, max_words=10))
    return " ".join(words) if words else None


def covers_topic(question: str, chunk: RetrievedChunk) -> bool:
    """Does the passage carry every one of the question's topic words?"""
    words = core_words(search_term(question, max_words=10))
    return not words or _hits(words, chunk) == len(words)


def complete_count(question: str, chunks: Sequence[RetrievedChunk]) -> int:
    """How many passages carry every topic word."""
    words = core_words(search_term(question, max_words=10))
    if not words:
        return len(chunks)
    return sum(1 for chunk in chunks if _hits(words, chunk) == len(words))


def on_topic(question: str, chunks: Sequence[RetrievedChunk]) -> list[RetrievedChunk]:
    """The passages about the question, in their retrieval order: those that
    carry every topic word when there are enough of them, otherwise those
    that carry most (``required_hits``)."""
    words = core_words(search_term(question, max_words=10))
    if not words:
        return list(chunks)
    scored = [(_hits(words, chunk), chunk) for chunk in chunks]
    complete = [chunk for hits, chunk in scored if hits == len(words)]
    if len(complete) >= COMPLETE_ENOUGH:
        return complete
    needed = required_hits(len(words))
    kept = [chunk for hits, chunk in scored if hits >= needed]
    if len(kept) >= 2 or len(words) < 4:
        return kept
    # A long, descriptive question ("crushing chest pain radiating to the
    # left arm — workup?") has more topic words than any passage repeats.
    # Its main concept leads the search term (search_term puts recognised
    # clinical terms first): fall back to passages about that.
    head = words[:2]
    about_head = [chunk for chunk in chunks if _hits(head, chunk) == len(head)]
    return about_head if len(about_head) > len(kept) else kept


# -- the one sentence worth quoting --------------------------------------------------------

_FINDING = re.compile(
    r"\b(?:conclu\w*|recommend\w*|first[-\s]?line|effective|efficacy|reduc\w*|improv\w*|"
    r"should|superior|non-?inferior|preferred|treated\s+with|treatment\s+of\s+choice|"
    r"dose|dosage|mg\b|associated\s+with|increase\w*|decrease\w*|safe|well[-\s]tolerated|"
    r"indicated|is\s+(?:a|the)\s+(?:common|leading|major))\b",
    re.I,
)
_METHOD = re.compile(
    r"\b(?:we\s+(?:conducted|searched|performed|aimed|included|analy[sz]ed|investigated)|"
    r"this\s+(?:study|review|trial|analysis)\s+(?:aims|aimed|was|assessed|evaluated|examined|"
    r"investigated|compared|explored)|were\s+(?:included|searched|enrolled|randomi[sz]ed)|"
    r"databases?|methods?:|objective\w*:|background:|aim\w*:|"
    r"met\s+the\s+inclusion\s+criteria)\b",
    re.I,
)
# An objective, stated as one: "To evaluate the efficacy of …".
_OBJECTIVE = re.compile(
    r"^(?:to|we\s+aimed\s+to)\s+(?:evaluate|assess|examine|investigate|determine|compare|"
    r"explore|describe|identify|review|summari[sz]e)\b",
    re.I,
)
# A section label an abstract glues onto its first sentence ("Objective
# Optimal medical therapy…", "Background: …").
LABEL = re.compile(
    r"^(?:background(?:\s+and\s+objectives?)?|objectives?|aims?|purpose|methods?|results?|"
    r"conclusions?|findings|interpretation|importance|context|introduction)\s*[:.\-]?\s+",
    re.I,
)


def _topic_words(question: str) -> set[str]:
    return {
        _normalized(w)
        for w in WORD.findall(search_term(question).lower().replace("-", " "))
        if len(w) > 2
    }


def best_sentence(question: str, chunk: RetrievedChunk) -> str | None:
    """The passage's sentence that says most about the question: it carries
    the topic and reads like a finding or a recommendation, not a method.
    None when no sentence does."""
    topic = _topic_words(question)
    best: tuple[float, str] | None = None
    for sentence in split_sentences(chunk.content):
        text = LABEL.sub("", sentence.strip())
        # An aim is not a finding, however many topic words it carries.
        if len(text) < 40 or text.endswith("?") or _OBJECTIVE.search(text):
            continue
        words = set(WORD.findall(_normalized(text)))
        overlap = len(topic & words)
        score = (
            overlap
            + (1.5 if _FINDING.search(text) else 0.0)
            - (2.0 if _METHOD.search(text) else 0.0)
        )
        if overlap == 0 and not _FINDING.search(text):
            continue
        if best is None or score > best[0]:
            best = (score, text)
    return best[1] if best and best[0] > 0 else None
