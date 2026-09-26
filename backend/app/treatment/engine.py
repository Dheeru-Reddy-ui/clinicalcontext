"""Run a symptom check: the next question, or the assessment.

Stateless — the page sends the profile and every answer so far, and gets
back either the next question or the finished assessment. An answer that
raises the urgency to an emergency ends the questions at once: nobody in
danger should be asked five more things before being told to get help.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.treatment.formulary import Profile
from app.treatment.model import URGENCY_RANK, Answers, Assessment, Protocol, Question
from app.treatment.protocols import PROTOCOLS
from app.treatment.sources import SOURCES

HEADLINES = {
    "emergency": "Get emergency help now",
    "urgent": "See a doctor today",
    "soon": "See a doctor in the next day or two",
    "self_care": "This can usually be looked after at home",
}
ACTIONS = {
    "emergency": "Call 112 or 108 for an ambulance (India), or your local emergency number, "
    "or go to the nearest emergency department now. Don't wait, and don't drive yourself.",
    "urgent": "Contact a doctor or visit a clinic or hospital today. If things get worse "
    "before then, treat it as an emergency.",
    "soon": "Book to see a doctor in the next 1 to 2 days. Until then, the advice below can "
    "help — and go sooner if a warning sign appears.",
    "self_care": "Most people get better with the care below. See a doctor if it isn't "
    "improving, or straight away if a warning sign appears.",
}


@dataclass(slots=True)
class Step:
    """Either a question to ask or a finished assessment."""

    question: Question | None = None
    assessment: Assessment | None = None
    answered: int = 0
    total: int = 0


class UnknownComplaint(ValueError):
    pass


def get_protocol(complaint: str) -> Protocol:
    try:
        return PROTOCOLS[complaint]
    except KeyError as exc:
        raise UnknownComplaint(complaint) from exc


def _from_options(protocol: Protocol, profile: Profile, answers: Answers) -> Assessment:
    result = Assessment(urgency="self_care")
    if protocol.precheck is not None:
        reason = protocol.precheck(profile)
        if reason is not None:
            result.raise_to(reason.urgency, reason.text, reason.source)
    for question in protocol.questions:
        picked = set(answers.get(question.id, ()))
        for option in question.options_for(profile):
            if option.id in picked and option.urgency is not None:
                result.raise_to(option.urgency, option.reason or option.label, option.source)
    return result


def _asked(protocol: Protocol, profile: Profile, answers: Answers) -> list[Question]:
    return [q for q in protocol.questions if q.when is None or q.when(profile, answers)]


def step(complaint: str, profile: Profile, answers: Answers) -> Step:
    protocol = get_protocol(complaint)
    questions = _asked(protocol, profile, answers)
    so_far = _from_options(protocol, profile, answers)
    answered = sum(1 for q in questions if q.id in answers)
    if so_far.urgency != "emergency":
        for question in questions:
            if question.id not in answers:
                return Step(question=question, answered=answered, total=len(questions))
    return Step(
        assessment=assess(protocol, profile, answers), answered=answered, total=len(questions)
    )


def _emergency(result: Assessment) -> bool:
    return result.urgency == "emergency"


def assess(protocol: Protocol, profile: Profile, answers: Answers) -> Assessment:
    result = _from_options(protocol, profile, answers)
    if result.urgency == "emergency":
        # Nothing but the way to help: no home remedies to delay it.
        result.source_keys = sorted({r.source for r in result.reasons if r.source})
        return result
    protocol.assess(profile, answers, result)
    if _emergency(result):
        # A rule of the complaint's (an age, a duration) made it one.
        result.self_care, result.medicines, result.doctor_may, result.tests = [], [], [], []
    unmatched = profile.unmatched_conditions
    if unmatched:
        # Something the person told us that no rule here knows about: never
        # ignored in silence — every medicine offered says to check first.
        named = ", ".join(unmatched)
        pronoun = "it" if len(unmatched) == 1 else "them"
        for medicine in result.medicines:
            if medicine.suitable:
                medicine.notes.append(
                    f"You also mentioned {named}. These checks don't cover {pronoun}, so ask a "
                    "pharmacist or doctor before taking this."
                )
    # Strongest reason first.
    result.reasons.sort(key=lambda r: -URGENCY_RANK[r.urgency])
    keys: list[str] = []
    for key in (
        *[r.source for r in result.reasons if r.source],
        *[k for m in result.medicines for k in m.source_keys],
        *[d.source for d in result.doctor_may],
        *protocol.source_keys,
    ):
        if key and key in SOURCES and key not in keys:
            keys.append(key)
    result.source_keys = keys
    return result
