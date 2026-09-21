"""Guideline PDF parsing: layout-aware section extraction from a generated PDF."""

from __future__ import annotations

from pathlib import Path

import pytest
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas

from app.ingestion.sources.guideline import parse_guideline_pdf

BODY = (
    "Patients with stage 2 hypertension should receive pharmacologic therapy. "
    "Lifestyle modification remains foundational for all risk groups."
)


@pytest.fixture
def guideline_pdf(tmp_path: Path) -> Path:
    path = tmp_path / "test-htn-guideline.pdf"
    page = canvas.Canvas(str(path), pagesize=letter)

    page.setFont("Helvetica-Bold", 20)
    page.drawString(72, 730, "Hypertension Management Guideline 2026")

    page.setFont("Helvetica", 11)
    page.drawString(72, 700, "This document summarizes consensus recommendations.")
    page.drawString(72, 686, "It was produced by the Test Society of Cardiology.")

    page.setFont("Helvetica-Bold", 15)
    page.drawString(72, 650, "1. Recommendations")
    page.setFont("Helvetica", 11)
    page.drawString(72, 630, BODY[:80])
    page.drawString(72, 616, BODY[80:160])
    page.drawString(72, 602, BODY[160:])

    page.setFont("Helvetica-Bold", 15)
    page.drawString(72, 560, "2. Methodology")
    page.setFont("Helvetica", 11)
    page.drawString(72, 540, "Evidence was reviewed systematically by the panel.")

    page.showPage()
    page.save()
    return path


def test_pdf_sections_follow_headings(guideline_pdf: Path) -> None:
    document = parse_guideline_pdf(guideline_pdf)

    assert document.source_type == "guideline"
    assert document.external_id == "test-htn-guideline.pdf"
    assert document.publication_types == ["Practice Guideline"]

    titles = [s.title for s in document.sections]
    assert "1. Recommendations" in titles
    assert "2. Methodology" in titles

    recommendations = next(s for s in document.sections if s.title == "1. Recommendations")
    assert "pharmacologic therapy" in recommendations.content


def test_pdf_title_override(guideline_pdf: Path) -> None:
    document = parse_guideline_pdf(guideline_pdf, title="AHA HTN Guideline")
    assert document.title == "AHA HTN Guideline"


def test_default_title_is_first_line(guideline_pdf: Path) -> None:
    document = parse_guideline_pdf(guideline_pdf)
    assert document.title == "Hypertension Management Guideline 2026"
