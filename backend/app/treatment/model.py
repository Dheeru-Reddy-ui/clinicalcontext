"""The shapes a symptom check is made of: questions, options, and a result."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Literal

from app.treatment.formulary import MedicineAdvice, Profile

Urgency = Literal["self_care", "soon", "urgent", "emergency"]
URGENCY_RANK: dict[Urgency, int] = {"self_care": 0, "soon": 1, "urgent": 2, "emergency": 3}

# answers: question id → the chosen option ids ("none" for "none of these").
Answers = Mapping[str, Sequence[str]]


@dataclass(frozen=True, slots=True)
class Option:
    id: str
    label: str
    # Choosing this option raises the urgency to at least this level, for
    # this reason (shown in the result, with its source).
    urgency: Urgency | None = None
    reason: str | None = None
    source: str | None = None
    # Shown only for some people (a baby's signs are not asked of an adult).
    only: Callable[[Profile], bool] | None = None


@dataclass(frozen=True, slots=True)
class Question:
    id: str
    text: str
    kind: Literal["single", "multi"]
    options: tuple[Option, ...]
    help: str | None = None
    when: Callable[[Profile, Answers], bool] | None = None

    def options_for(self, profile: Profile) -> tuple[Option, ...]:
        return tuple(o for o in self.options if o.only is None or o.only(profile))


@dataclass(slots=True)
class Reason:
    text: str
    urgency: Urgency
    source: str | None = None


@dataclass(slots=True)
class DoctorOption:
    """What a doctor may prescribe — information, never a dose for the person."""

    text: str
    source: str


@dataclass(slots=True)
class Assessment:
    urgency: Urgency
    reasons: list[Reason] = field(default_factory=list)
    possible_causes: list[str] = field(default_factory=list)
    self_care: list[str] = field(default_factory=list)
    medicines: list[MedicineAdvice] = field(default_factory=list)
    doctor_may: list[DoctorOption] = field(default_factory=list)
    tests: list[str] = field(default_factory=list)
    see_doctor_if: list[str] = field(default_factory=list)
    source_keys: list[str] = field(default_factory=list)

    def raise_to(self, urgency: Urgency, text: str, source: str | None) -> None:
        self.reasons.append(Reason(text=text, urgency=urgency, source=source))
        if URGENCY_RANK[urgency] > URGENCY_RANK[self.urgency]:
            self.urgency = urgency


@dataclass(frozen=True, slots=True)
class Protocol:
    id: str
    name: str
    summary: str
    questions: tuple[Question, ...]
    # Complaint-specific rules and advice, applied after the options' own
    # urgencies have been counted.
    assess: Callable[[Profile, Answers, Assessment], None]
    source_keys: tuple[str, ...] = ()
    # Who needs help before any question is asked (a baby under 3 months
    # with a fever): returns the reason, or None.
    precheck: Callable[[Profile], Reason | None] | None = None


def chosen(answers: Answers, question_id: str) -> set[str]:
    return {a for a in answers.get(question_id, ()) if a != "none"}


def is_child(profile: Profile) -> bool:
    return profile.age_years < 16


def is_under_five(profile: Profile) -> bool:
    return profile.age_years < 5
