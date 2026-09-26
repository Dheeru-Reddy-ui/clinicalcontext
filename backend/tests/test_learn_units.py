"""Learn's tools, without a database: de-identification, the note summary and
its checks, the tutor's questions and their checks, reading a paper, and the
practice streak. The language model is stood in for by an OpenAI-compatible
MockTransport — exactly what Groq would return."""

from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx
import pytest
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

from app.learn.deid import redact
from app.learn.notes import extractive_summary, note_lines, summarize_note
from app.learn.papers import parse_paper_pdf, rank_paper_passages
from app.learn.progress import streak
from app.learn.quiz import QuizUnavailable, offline_quiz, write_quiz
from app.llm.chat import ChatModel, ChatProvider
from app.retrieval.types import RetrievedChunk

NOTE = """DISCHARGE SUMMARY
Name: Ramesh Kumar Reddy          Age/Sex: 58 Y / M
UHID: HYD-2024-004829   IP No: 245/2024
Address: 12-3-45, Banjara Hills, Hyderabad
Phone: +91 98480 12345
Date of admission: 12/03/2024    Date of discharge: 18 March 2024
Consultant: Dr. Anita Sharma (Cardiology)
Presenting complaint: Central chest pain radiating to the left arm for 2 hours with sweating.
Diagnosis: Acute anterior wall STEMI, thrombolysed with tenecteplase. Graves' disease.
Investigations:
Troponin I 12.4 ng/mL. Hb 11.2 g/dL. Creatinine 1.1 mg/dL.
Echo: LVEF 40%, anterior wall hypokinesia.
Discharge medications:
Tab. Aspirin 75 mg OD
Tab. Atorvastatin 80 mg HS
Advice: Review in OPD after 2 weeks with fasting lipid profile.
Aadhaar 1234 5678 9012.
"""


def _model(reply: str, seen: list[dict[str, Any]] | None = None) -> ChatModel:
    """A model whose one provider answers every request with ``reply``."""

    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": reply}}],
                "usage": {"prompt_tokens": 500, "completion_tokens": 200},
            },
        )

    provider = ChatProvider(
        "groq", "https://api.groq.com/openai/v1", "k-test", "openai/gpt-oss-120b"
    )
    return ChatModel([provider], transport=httpx.MockTransport(handler))


def _chunk(content: str, *, title: str = "A trial", section: str | None = None) -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id=uuid4(),
        document_id=uuid4(),
        content=content,
        section=section,
        title=title,
        publication_date=date(2024, 1, 1),
        evidence_grade="A",
        study_type="randomized_controlled_trial",
    )


# -- de-identification --------------------------------------------------------------------


def test_identifiers_are_replaced_and_the_clinical_content_kept() -> None:
    result = redact(NOTE)
    text = result.text
    for identifier in (
        "Ramesh",
        "Reddy",
        "HYD-2024-004829",
        "245/2024",
        "Banjara",
        "98480",
        "12/03/2024",
        "18 March 2024",
        "Anita",
        "1234 5678 9012",
    ):
        assert identifier not in text, identifier
    # What the summary needs stays: age and sex, results, medicines, eponyms.
    for kept in ("58 Y / M", "Troponin I 12.4 ng/mL", "Tab. Aspirin 75 mg OD", "Graves' disease"):
        assert kept in text, kept
    assert result.counts["NAME"] >= 2 and result.counts["DATE"] == 2
    assert result.counts["ID NUMBER"] == 1 and result.counts["PHONE"] == 1
    assert result.total == sum(result.counts.values())


def test_a_placeholder_is_never_counted_twice() -> None:
    once = redact("Name: Asha Rao")
    twice = redact(once.text)
    assert twice.text == once.text and twice.counts == {}


# -- the note summary ----------------------------------------------------------------------


def test_long_lines_are_split_into_sentences_for_numbering() -> None:
    long_line = " ".join(["The patient improved steadily on treatment."] * 8)
    lines = note_lines(f"Short line\n\n{long_line}")
    assert lines[0] == "Short line" and len(lines) == 9


def test_without_a_model_the_summary_is_the_notes_own_lines_under_its_headings() -> None:
    lines = note_lines(redact(NOTE).text)
    sections = {s.heading: s for s in extractive_summary(lines)}
    assert set(sections) >= {"Summary", "Problems", "Key findings", "Medications", "Plan"}
    medications = [p.text for p in sections["Medications"].points]
    assert "Tab. Aspirin 75 mg OD" in medications
    for section in sections.values():
        for point in section.points:
            assert point.support == "quoted"
            assert point.text in lines[point.lines[0] - 1]


async def test_a_models_summary_is_kept_point_by_point_when_it_matches_the_note() -> None:
    lines = note_lines(redact(NOTE).text)
    age = next(i for i, line in enumerate(lines, start=1) if "58 Y" in line)
    diagnosis = next(i for i, line in enumerate(lines, start=1) if "STEMI" in line)
    troponin = next(i for i, line in enumerate(lines, start=1) if "Troponin" in line)
    aspirin = next(i for i, line in enumerate(lines, start=1) if "Aspirin" in line)
    reply = (
        "Summary\n"
        f"- 58-year-old man with acute anterior wall STEMI [{age}][{diagnosis}].\n"
        "Key findings\n"
        f"- Troponin I 12.4 ng/mL, Hb 11.2 g/dL, creatinine 1.1 mg/dL [{troponin}].\n"
        "Medications\n"
        f"- Tab. Aspirin 75 mg OD. [{aspirin}]\n"
    )
    seen: list[dict[str, Any]] = []
    summary = await summarize_note(NOTE, model=_model(reply, seen))
    assert summary.mode == "llm" and summary.removed == 0
    points = {p.text: p for s in summary.sections for p in s.points}
    assert any("Troponin I 12.4" in text for text in points)
    assert all(p.lines for p in points.values())
    # The model only ever saw the redacted note.
    sent = json.dumps(seen)
    assert "Ramesh" not in sent and "HYD-2024-004829" not in sent and "[NAME]" in sent


async def test_a_summary_with_invented_numbers_falls_back_to_the_note() -> None:
    lines = note_lines(redact(NOTE).text)
    troponin = next(i for i, line in enumerate(lines, start=1) if "Troponin" in line)
    reply = (
        "Key findings\n"
        f"- Troponin I 99.9 ng/mL and Hb 6.1 g/dL [{troponin}].\n"
        "- Ejection fraction was 15% on echo.\n"
        "Plan\n"
        f"- Start warfarin 5 mg daily [{troponin}].\n"
    )
    summary = await summarize_note(NOTE, model=_model(reply))
    assert summary.mode == "extractive"
    assert summary.notice and "note's own lines" in summary.notice
    assert all(p.support == "quoted" for s in summary.sections for p in s.points)


# -- the tutor's questions ------------------------------------------------------------------

SOURCES = [
    _chunk(
        "In patients with atrial fibrillation, apixaban reduced the risk of stroke or "
        "systemic embolism compared with warfarin, with 31% less major bleeding.",
        title="ARISTOTLE",
    ),
    _chunk(
        "Dabigatran 150 mg twice daily was associated with lower rates of stroke than "
        "warfarin in non-valvular atrial fibrillation.",
        title="RE-LY",
    ),
    _chunk(
        "Rivaroxaban was noninferior to warfarin for the prevention of stroke in "
        "atrial fibrillation, and fatal bleeding was reduced by 50% with rivaroxaban.",
        title="ROCKET AF",
    ),
]


def _question(**overrides: Any) -> dict[str, Any]:
    question: dict[str, Any] = {
        "stem": "Which anticoagulant reduced stroke with less major bleeding than warfarin in AF?",
        "options": ["Apixaban", "Aspirin", "Heparin", "Clopidogrel"],
        "answer": 0,
        "explanation": "Apixaban reduced stroke or systemic embolism compared with warfarin, "
        "with less major bleeding [1].",
        "sources": [1],
    }
    question.update(overrides)
    return question


async def test_a_models_quiz_keeps_only_questions_that_pass_their_checks() -> None:
    reply = json.dumps(
        {
            "questions": [
                _question(),
                # Duplicate options.
                _question(stem="Duplicate options, first question?", options=["A", "A", "B", "C"]),
                # "All of the above" is not a single best answer.
                _question(
                    stem="Which drugs reduce stroke in atrial fibrillation?",
                    options=["Apixaban", "Warfarin", "Dabigatran", "All of the above"],
                ),
                # An explanation the sources do not support.
                _question(
                    stem="Which drug cures atrial fibrillation permanently?",
                    explanation="Apixaban permanently cures atrial fibrillation in 95% of "
                    "patients within a week [1].",
                ),
                # No citation at all.
                _question(stem="An uncited question here?", explanation="Because it is so."),
            ]
        }
    )
    quiz = await write_quiz(
        mode="quiz",
        topic="anticoagulation in atrial fibrillation",
        level="mbbs",
        count=5,
        specialty_name=None,
        sources=SOURCES,
        model=_model(reply),
    )
    assert quiz.generated_by == "llm"
    assert [q.stem for q in quiz.questions] == [_question()["stem"]]
    assert quiz.dropped == 4 and quiz.notices
    assert quiz.questions[0].sources == [1]


async def test_a_case_needs_the_model_and_its_patient_carries_no_identifiers() -> None:
    with pytest.raises(QuizUnavailable):
        await write_quiz(
            mode="case",
            topic="atrial fibrillation",
            level="mbbs",
            count=3,
            specialty_name=None,
            sources=SOURCES,
            model=ChatModel([]),
        )
    reply = json.dumps(
        {
            "case": "Mr. Arjun Mehta, a 72-year-old man, presents with palpitations and an "
            "irregularly irregular pulse. ECG shows no P waves.",
            "questions": [_question(stage="Management")],
        }
    )
    quiz = await write_quiz(
        mode="case",
        topic="atrial fibrillation",
        level="mbbs",
        count=1,
        specialty_name=None,
        sources=SOURCES,
        model=_model(reply),
    )
    assert quiz.case is not None and "Arjun" not in quiz.case and "[NAME]" in quiz.case
    assert quiz.questions[0].stage == "Management"


def test_without_a_model_a_quiz_is_fill_in_the_blank_from_the_sources() -> None:
    quiz = offline_quiz(topic="AF", level="mbbs", count=3, sources=SOURCES)
    assert quiz.generated_by == "offline" and len(quiz.questions) >= 2
    for question in quiz.questions:
        assert "_____" in question.stem and len(set(question.options)) == 4
        right = question.options[question.answer]
        source = SOURCES[question.sources[0] - 1].content
        assert right.rstrip("%") in source.lower() or right in source
        assert question.explanation.endswith(f"[{question.sources[0]}]")
    # The right answer is not always the first option.
    assert len({q.answer for q in quiz.questions}) > 1 or len(quiz.questions) == 1


def test_too_little_in_the_sources_says_so() -> None:
    with pytest.raises(QuizUnavailable):
        offline_quiz(topic="x", level="mbbs", count=5, sources=[_chunk("Too short.")])


# -- reading a paper ------------------------------------------------------------------------


def make_paper(path: Path) -> Path:
    """A three-page paper: title, abstract, methods, results on page 2,
    discussion and conclusions on page 3."""
    pdf = canvas.Canvas(str(path), pagesize=A4)
    pdf.setFont("Helvetica-Bold", 18)
    pdf.drawString(60, 790, "Apixaban versus Aspirin in Atrial Fibrillation")
    pdf.drawString(60, 768, "a Randomised Controlled Trial")
    pdf.setFont("Helvetica-Bold", 13)
    pdf.drawString(60, 730, "Abstract")
    pdf.setFont("Helvetica", 10)
    pdf.drawString(
        60, 712, "In this randomised trial, apixaban reduced stroke compared with aspirin"
    )
    pdf.drawString(60, 698, "in patients with atrial fibrillation unsuitable for warfarin.")
    pdf.setFont("Helvetica-Bold", 13)
    pdf.drawString(60, 660, "Methods")
    pdf.setFont("Helvetica", 10)
    pdf.drawString(60, 642, "We enrolled 5599 patients and followed them for a mean of 1.1 years.")
    pdf.showPage()
    pdf.setFont("Helvetica-Bold", 13)
    pdf.drawString(60, 790, "Results")
    pdf.setFont("Helvetica", 10)
    pdf.drawString(60, 772, "Stroke or systemic embolism occurred in 1.6% per year with apixaban")
    pdf.drawString(60, 758, "and 3.7% per year with aspirin (hazard ratio 0.45).")
    pdf.showPage()
    pdf.setFont("Helvetica-Bold", 13)
    pdf.drawString(60, 790, "Discussion")
    pdf.setFont("Helvetica", 10)
    pdf.drawString(60, 772, "The trial was stopped early because of a clear benefit of apixaban.")
    pdf.setFont("Helvetica-Bold", 13)
    pdf.drawString(60, 740, "Conclusions")
    pdf.setFont("Helvetica", 10)
    pdf.drawString(60, 722, "Apixaban reduced stroke without a significant increase in bleeding.")
    pdf.save()
    return path


def test_a_paper_is_read_with_its_sections_and_pages(tmp_path: Path) -> None:
    document, pages = parse_paper_pdf(make_paper(tmp_path / "trial.pdf"))
    assert pages == 3
    assert document.title.startswith("Apixaban versus Aspirin in Atrial Fibrillation")
    assert document.source_type == "uploaded"
    assert document.publication_types == ["Randomized Controlled Trial"]
    titles = [s.title for s in document.sections]
    assert "Results · p. 2" in titles and "Conclusions · p. 3" in titles
    assert document.abstract and "reduced stroke" in document.abstract


async def test_a_question_to_a_paper_is_answered_from_the_right_passages(tmp_path: Path) -> None:
    from app.retrieval.chunking import chunk_sections

    document, _ = parse_paper_pdf(make_paper(tmp_path / "trial.pdf"))
    chunks = [
        _chunk(c.content, section=c.section, title=document.title)
        for c in chunk_sections(document.sections)
    ]
    ranked = await rank_paper_passages(
        None,  # type: ignore[arg-type]  # no pool needed without an embedder
        chunks=chunks,
        question="What was the hazard ratio for stroke?",
        embedder=None,
    )
    assert ranked[0].section == "Results · p. 2"
    summary = await rank_paper_passages(
        None,  # type: ignore[arg-type]
        chunks=chunks,
        question="Summarise this paper",
        embedder=None,
    )
    sections = {(c.section or "").split(" · ")[0] for c in summary}
    assert {"Abstract", "Results", "Conclusions"} <= sections


# -- the practice streak ------------------------------------------------------------------


def test_a_streak_counts_consecutive_days_up_to_today_or_yesterday() -> None:
    today = date(2026, 9, 26)
    days = [today - timedelta(days=n) for n in (0, 1, 2, 4)]
    assert streak(days, today) == 3
    assert streak([d - timedelta(days=1) for d in days], today) == 3  # not yet today
    assert streak([today - timedelta(days=3)], today) == 0


def test_lines_of_only_labels_and_identifiers_stay_out_of_the_summary() -> None:
    from app.learn.notes import only_identifiers

    assert only_identifiers("Phone: [PHONE]")
    assert only_identifiers("Date of admission: [DATE] Date of discharge: [DATE]")
    assert not only_identifiers("Name: [NAME] Age/Sex: 64 Y / F")
    assert not only_identifiers("Hb 10.8 g/dL")
    lines = note_lines(redact("Advice: Review in OPD after 1 week.\nPhone: 98470 55213").text)
    plan = {s.heading: s for s in extractive_summary(lines)}["Plan"]
    assert [p.text for p in plan.points] == ["Review in OPD after 1 week."]
