"""Guideline PDF source: layout-aware text extraction with header detection.

Headers are detected from layout signals (font size relative to body text)
plus numbering/casing heuristics; each header opens a new section so chunking
preserves guideline structure ("Recommendations" vs "Methodology" matters).
The original PDF is uploaded to Supabase Storage when configured.
"""

from __future__ import annotations

import re
import statistics
from dataclasses import dataclass
from pathlib import Path

import pdfplumber
import structlog

from app.ingestion.models import RawDocument, RawSection

logger = structlog.stdlib.get_logger("app.ingestion.guideline")

_HEADING_NUMBERED = re.compile(r"^(\d+(\.\d+)*)[.)]?\s+\S")
_HEADING_SIZE_RATIO = 1.15
_MAX_HEADING_WORDS = 14


@dataclass(slots=True)
class _Line:
    text: str
    size: float


def _extract_lines(pdf_path: Path) -> list[_Line]:
    lines: list[_Line] = []
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            for line in page.extract_text_lines(strip=True) or []:
                text = re.sub(r"\s+", " ", str(line.get("text", ""))).strip()
                if not text:
                    continue
                chars = line.get("chars", [])
                sizes = [float(c["size"]) for c in chars if "size" in c]
                lines.append(_Line(text=text, size=statistics.median(sizes) if sizes else 0.0))
    return lines


def _is_heading(line: _Line, body_size: float) -> bool:
    words = line.text.split()
    if not words or len(words) > _MAX_HEADING_WORDS:
        return False
    if line.text.endswith((".", ";", ",")):
        return False
    larger_font = body_size > 0 and line.size >= body_size * _HEADING_SIZE_RATIO
    numbered = bool(_HEADING_NUMBERED.match(line.text))
    all_caps = line.text.isupper() and len(line.text) >= 4
    return larger_font or numbered or all_caps


def parse_guideline_pdf(pdf_path: Path, *, title: str | None = None) -> RawDocument:
    """Extract a guideline PDF into titled sections."""
    lines = _extract_lines(pdf_path)
    if not lines:
        raise ValueError(f"no extractable text in {pdf_path.name}")

    body_size = statistics.median([line.size for line in lines if line.size > 0] or [0.0])

    sections: list[RawSection] = []
    current_title: str | None = None
    current_parts: list[str] = []

    def flush() -> None:
        nonlocal current_parts
        content = " ".join(current_parts).strip()
        if content:
            sections.append(RawSection(title=current_title, content=content))
        current_parts = []

    for line in lines:
        if _is_heading(line, body_size):
            flush()
            current_title = line.text
        else:
            current_parts.append(line.text)
    flush()

    resolved_title = title or (lines[0].text if lines else pdf_path.stem)
    logger.info(
        "guideline_parsed",
        file=pdf_path.name,
        sections=len(sections),
        body_font_size=body_size,
    )
    return RawDocument(
        source_type="guideline",
        external_id=pdf_path.name,
        title=resolved_title,
        abstract=None,
        sections=sections,
        publication_types=["Practice Guideline"],
    )
