"""The AI tutor's quizzes and clinical cases.

Questions are written from evidence, not from memory: the topic is searched
the way the chat searches (the library first, PubMed live when it is thin),
and the model writes single-best-answer questions from the numbered passages
(prompts tutor_quiz / tutor_case). Each question is then checked before
anyone sees it:

- its shape — four distinct options, one answer, no "all of the above";
- its explanation — every cited statement against the passage it cites, with
  the grounding verifier the rest of ClinicalContext uses (a number the
  passage does not contain fails);
- that the explanation is about the answer it marks as right.

A question that fails is dropped, and the quiz says how many were. With no
model configured or answering, a quiz is still possible: fill-in-the-blank
questions made from the passages' own sentences (a medicine or a figure
blanked), each explained by the sentence it came from. A case needs the
model — an invented patient is not something to assemble from quotes.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Literal

import structlog
from pydantic import BaseModel, Field, ValidationError

from app.guardrails.grounding import invented_numbers, split_sentences, verify_grounding
from app.learn.deid import redact
from app.llm.chat import ChatMessage, ChatModel, LLMUnavailable
from app.prompts.loader import load_prompt
from app.retrieval.types import RetrievedChunk

logger = structlog.stdlib.get_logger("app.learn.quiz")

Mode = Literal["quiz", "case"]
Level = Literal["mbbs", "pg"]
PROMPTS: dict[Mode, tuple[str, int]] = {"quiz": ("tutor_quiz", 1), "case": ("tutor_case", 1)}
MAX_QUESTIONS = 10
SOURCES = 8
_MARKER = re.compile(r"\[(\d+)\]")
_BANNED_OPTION = re.compile(r"\b(?:all|none|both)\s+of\s+the\s+(?:above|following)\b", re.I)
_WORD = re.compile(r"[a-z0-9]+")


class QuizUnavailable(RuntimeError):
    """No quiz can be written right now; the message says why."""


@dataclass(slots=True)
class QuizQuestion:
    stem: str
    options: list[str]
    answer: int
    explanation: str
    # Markers into Quiz.sources (1-based) the explanation relies on.
    sources: list[int]
    stage: str | None = None


@dataclass(slots=True)
class Quiz:
    mode: Mode
    topic: str
    level: Level
    questions: list[QuizQuestion]
    sources: list[RetrievedChunk]
    generated_by: Literal["llm", "offline"]
    case: str | None = None
    model: str | None = None
    dropped: int = 0
    notices: list[str] = field(default_factory=list)


class _Question(BaseModel):
    stage: str | None = Field(default=None, max_length=40)
    stem: str = Field(min_length=10, max_length=1500)
    options: list[str] = Field(min_length=4, max_length=5)
    answer: int = Field(ge=0, le=4)
    explanation: str = Field(min_length=10, max_length=3000)
    sources: list[int] = Field(default_factory=list, max_length=8)


class _Paper(BaseModel):
    case: str | None = Field(default=None, max_length=2500)
    questions: list[_Question] = Field(max_length=MAX_QUESTIONS * 2)


def numbered_sources(chunks: Sequence[RetrievedChunk]) -> str:
    blocks = []
    for i, c in enumerate(chunks, start=1):
        year = c.publication_date.year if c.publication_date else "n.d."
        grade = f", grade {c.evidence_grade}" if c.evidence_grade else ""
        title = f"{c.title}: " if c.title else ""
        blocks.append(f"[{i}] ({year}{grade}) {title}{c.content}")
    return "\n\n".join(blocks)


def _json_payload(text: str) -> object:
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start == -1 or end <= start:
            raise
        return json.loads(text[start : end + 1])


def _content_words(text: str) -> set[str]:
    return {w for w in _WORD.findall(text.lower()) if len(w) > 3}


async def _checked(question: _Question, sources: Sequence[RetrievedChunk]) -> QuizQuestion | None:
    """The question if it passes every check, else None."""
    options = [" ".join(o.split()) for o in question.options]
    if len({o.lower() for o in options}) != len(options) or any(not o for o in options):
        return None
    if any(_BANNED_OPTION.search(o) for o in options) or question.answer >= len(options):
        return None
    # The explanation itself has to cite: a "sources" list beside an
    # explanation that names none of them vouches for nothing.
    cited = sorted(
        {int(m) for m in _MARKER.findall(question.explanation) if 1 <= int(m) <= len(sources)}
    )
    if not cited:
        return None
    passages = [sources[n - 1].content for n in cited]
    if invented_numbers(question.explanation, passages):
        return None  # a figure no cited source contains
    verdict = await verify_grounding(
        question.explanation, {n: sources[n - 1].content for n in cited}
    )
    if not verdict.accepted or not verdict.kept_answer:
        return None
    # The explanation has to be about the option it marks right.
    right = _content_words(options[question.answer])
    if right and not right & _content_words(verdict.kept_answer):
        return None
    return QuizQuestion(
        stem=" ".join(question.stem.split()),
        options=options,
        answer=question.answer,
        explanation=verdict.kept_answer,
        sources=cited,
        stage=question.stage,
    )


async def write_quiz(
    *,
    mode: Mode,
    topic: str,
    level: Level,
    count: int,
    specialty_name: str | None,
    sources: list[RetrievedChunk],
    model: ChatModel,
) -> Quiz:
    """A checked quiz or case on ``topic`` from ``sources``."""
    count = max(1, min(count, MAX_QUESTIONS))
    sources = sources[:SOURCES]
    if model.available and sources:
        try:
            return await _by_model(
                mode=mode,
                topic=topic,
                level=level,
                count=count,
                specialty_name=specialty_name,
                sources=sources,
                model=model,
            )
        except (LLMUnavailable, QuizUnavailable) as exc:
            logger.warning("quiz_model_failed", mode=mode, error=type(exc).__name__)
            if mode == "case":
                raise QuizUnavailable(
                    "The AI writer couldn't write this case right now. Try again in a minute, "
                    "or take a quiz on the topic instead."
                ) from exc
    if mode == "case":
        raise QuizUnavailable(
            "Case practice needs the AI writer, which isn't configured on this server. "
            "A quiz on the topic works without it."
        )
    return offline_quiz(topic=topic, level=level, count=count, sources=sources)


async def _by_model(
    *,
    mode: Mode,
    topic: str,
    level: Level,
    count: int,
    specialty_name: str | None,
    sources: list[RetrievedChunk],
    model: ChatModel,
) -> Quiz:
    context = [
        f"TOPIC: {topic}",
        f"LEVEL: {'PG' if level == 'pg' else 'MBBS'}",
        f"COUNT: {count}",
    ]
    if specialty_name:
        context.append(f"SPECIALTY: {specialty_name}")
    context.append(f"SOURCES:\n{numbered_sources(sources)}")
    messages = [
        ChatMessage("system", load_prompt(*PROMPTS[mode]).text),
        ChatMessage("user", "\n".join(context)),
    ]
    # A question is about 200 tokens; the budget leaves room without asking a
    # free tier (a few thousand tokens a minute) for more than a quiz needs.
    result = await model.complete(
        messages, max_tokens=600 + 300 * count, temperature=0.3, json_mode=True
    )
    try:
        paper = _Paper.model_validate(_json_payload(result.text))
    except (json.JSONDecodeError, ValidationError) as exc:
        raise QuizUnavailable("the model's questions could not be read") from exc
    questions: list[QuizQuestion] = []
    seen: set[str] = set()
    for raw in paper.questions[: count + 2]:
        checked = await _checked(raw, sources)
        if checked is None or checked.stem.lower() in seen:
            continue
        seen.add(checked.stem.lower())
        questions.append(checked)
        if len(questions) == count:
            break
    if not questions:
        raise QuizUnavailable("no question passed its checks")
    case: str | None = None
    if mode == "case":
        if not paper.case or len(paper.case) < 40:
            raise QuizUnavailable("the case had no presentation")
        # An invented patient still never carries a name, a place or a date.
        case = redact(" ".join(paper.case.split())).text
    dropped = max(0, min(count, len(paper.questions)) - len(questions))
    notices = (
        [
            f"{dropped} question{'s' if dropped != 1 else ''} left out: "
            f"{'their explanations' if dropped != 1 else 'its explanation'} "
            "didn't match the sources."
        ]
        if dropped
        else []
    )
    return Quiz(
        mode=mode,
        topic=topic,
        level=level,
        questions=questions,
        sources=sources,
        generated_by="llm",
        case=case,
        model=f"{result.provider}:{result.model}",
        dropped=dropped,
        notices=notices,
    )


# -- without a model: fill in the blank ---------------------------------------------------

_DRUG = re.compile(
    r"\b[a-z]{3,}(?:mab|nib|pril|sartan|olol|statin|gliflozin|gliptin|glutide|azole|cillin|"
    r"mycin|floxacin|dipine|parin|xaban|gatran|tidine|prazole|afil|triptan|lukast|cycline|"
    r"sone|semide|thiazide|zepam|zolam|oxetine|profen|coxib|formin|platin|taxel|rubicin|"
    r"vudine|navir|tegravir|buvir|amine|olone|asone)\b",
    re.I,
)
_FIGURE = re.compile(r"(?<![\w.])(\d{1,3}(?:\.\d)?)\s?%")
_FINDING = re.compile(
    r"\b(?:reduc|increas|decreas|lower|improv|associat|effective|efficac|recommend|"
    r"first[-\s]?line|superior|inferior|risk|mortality|benefit)\w*",
    re.I,
)
_COMMON_DRUGS = (
    "metformin",
    "amlodipine",
    "atorvastatin",
    "omeprazole",
    "salbutamol",
    "ceftriaxone",
    "enalapril",
    "furosemide",
    "prednisolone",
    "azithromycin",
)


def _stable_order(key: str, items: list[str]) -> list[str]:
    """The options in an order fixed by the question, so the right answer is
    not always first and the same question always looks the same."""
    return sorted(items, key=lambda item: hashlib.sha256(f"{key}|{item}".encode()).hexdigest())


def _figure_distractors(value: float) -> list[str]:
    candidates = [value / 2, value * 1.5, value + 10, value - 10, value * 2, value + 20]
    out: list[str] = []
    for candidate in candidates:
        if 0 < candidate < 100 and abs(candidate - value) >= 3:
            text = f"{candidate:.0f}%" if candidate >= 10 else f"{candidate:.1f}%"
            if text not in out and text != f"{value:g}%":
                out.append(text)
    return out[:3]


def offline_quiz(*, topic: str, level: Level, count: int, sources: list[RetrievedChunk]) -> Quiz:
    """Fill-in-the-blank questions from the passages' own sentences."""
    drugs_everywhere = {
        m.group(0).lower() for chunk in sources for m in _DRUG.finditer(chunk.content)
    }
    questions: list[QuizQuestion] = []
    used: set[str] = set()
    for number, chunk in enumerate(sources, start=1):
        for sentence in split_sentences(chunk.content):
            if len(questions) >= count:
                break
            if not 60 <= len(sentence) <= 320 or not _FINDING.search(sentence):
                continue
            drug = _DRUG.search(sentence)
            figure = _FIGURE.search(sentence)
            if drug and drug.group(0).lower() not in used:
                right = drug.group(0).lower()
                pool = sorted(drugs_everywhere - {right}) + [d for d in _COMMON_DRUGS if d != right]
                wrong = list(dict.fromkeys(pool))[:3]
                blanked = sentence[: drug.start()] + "_____" + sentence[drug.end() :]
            elif figure and figure.group(0) not in used:
                right = f"{float(figure.group(1)):g}%"
                wrong = _figure_distractors(float(figure.group(1)))
                blanked = sentence[: figure.start()] + "_____" + sentence[figure.end() :]
            else:
                continue
            if len(wrong) < 3:
                continue
            used.add(right if not figure or drug else figure.group(0))
            options = _stable_order(blanked, [right, *wrong[:3]])
            year = chunk.publication_date.year if chunk.publication_date else None
            where = (
                f"{chunk.title} ({year})" if chunk.title and year else (chunk.title or "a source")
            )
            questions.append(
                QuizQuestion(
                    stem=f"Fill in the blank, from {where}: \N{LEFT DOUBLE QUOTATION MARK}"
                    f"{blanked}\N{RIGHT DOUBLE QUOTATION MARK}",
                    options=options,
                    answer=options.index(right),
                    explanation=f"The source says: {sentence} [{number}]",
                    sources=[number],
                )
            )
            break  # one question per passage keeps them varied
        if len(questions) >= count:
            break
    if len(questions) < min(2, count):
        raise QuizUnavailable(
            "There isn't enough in the sources on this topic to write questions without the "
            "AI writer. Try a broader topic."
        )
    return Quiz(
        mode="quiz",
        topic=topic,
        level=level,
        questions=questions,
        sources=sources,
        generated_by="offline",
        model="offline",
        notices=[
            "No AI writer is available, so these are fill-in-the-blank questions taken from the "
            "sources' own sentences."
        ],
    )
