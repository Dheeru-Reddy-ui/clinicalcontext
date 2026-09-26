"""Ask-this-Paper: one uploaded research paper, and questions answered from it.

A paper is read page by page, so every passage knows its section and page
("Results · p. 4") and every answer can say where in the paper it came from.
It is stored as a private document of the person's organization — its own
source type, never mistaken for a guideline — with who uploaded it, and
embedded like the rest of the library.

A question is answered from that paper alone: its passages are ranked by
word match (BM25) and by meaning (the stored embeddings), the two rankings
fused, and a request for a summary or an appraisal also gets the abstract,
the results and the conclusion — the parts such an answer needs whatever its
words. The chat assistant does the rest (app/assistant/chat.py, kind
"paper"): the answer, its markers, the check against the passages.
"""

from __future__ import annotations

import re
import statistics
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any
from uuid import UUID

import pdfplumber
import structlog

from app.graph.relevance import STOPWORDS
from app.ingestion.models import RawDocument, RawSection
from app.retrieval.fusion import reciprocal_rank_fusion
from app.retrieval.rerank import BM25Reranker
from app.retrieval.types import RetrievedChunk

if TYPE_CHECKING:
    import asyncpg

    from app.retrieval.embed import EmbeddingService

    DbPool = asyncpg.Pool[asyncpg.Record]
else:
    import asyncpg

    DbPool = asyncpg.Pool

logger = structlog.stdlib.get_logger("app.learn.papers")

PAPER_KIND = "paper"
MAX_PAGES = 80
PASSAGES = 6
_HEADING_SIZE_RATIO = 1.12
_MAX_HEADING_WORDS = 12
_HEADING_NUMBERED = re.compile(r"^(\d+(\.\d+)*)[.)]?\s+[A-Z]")
_KNOWN_HEADINGS = re.compile(
    r"^(?:abstract|summary|background|introduction|objectives?|aims?|methods?|materials\s+and\s+"
    r"methods|study\s+design|participants|patients\s+and\s+methods|statistical\s+analysis|"
    r"results?|findings|discussion|conclusions?|interpretation|limitations|strengths\s+and\s+"
    r"limitations|funding|references|acknowledg(?:e)?ments?|keywords?|research\s+in\s+context)"
    r"\b[:.]?$",
    re.I,
)
_DESIGNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("Meta-Analysis", re.compile(r"\bmeta[-\s]?analys[ie]s\b", re.I)),
    ("Systematic Review", re.compile(r"\bsystematic\s+review\b", re.I)),
    ("Randomized Controlled Trial", re.compile(r"\brandomi[sz]ed\b.{0,40}\btrial\b", re.I)),
    ("Practice Guideline", re.compile(r"\b(?:clinical\s+practice\s+)?guidelines?\b", re.I)),
    (
        "Observational Study",
        re.compile(r"\b(?:cohort|case[-\s]control|cross[-\s]sectional)\b", re.I),
    ),
)
_SUMMARY_REQUEST = re.compile(
    r"\bsummar|\boverview\b|\bappraise|\bappraisal\b|\blimitations?\b|\bmain\s+findings?\b|"
    r"\bkey\s+(?:results?|findings?|points?|numbers?)\b|\bwhat\s+(?:is|was)\s+this\s+(?:paper|study)\b|"
    r"\bconclu",
    re.I,
)
_CORE_SECTIONS = re.compile(
    r"^(?:abstract|summary|results?|findings|conclusions?|interpretation|discussion)", re.I
)

# Words a question to a paper uses that say nothing about what to find.
_QUESTION_FILLER = frozenset(
    ("did", "paper", "study", "article", "authors", "author", "tell", "please", "explain", "me")
)


@dataclass(slots=True)
class _Line:
    text: str
    size: float
    page: int


class UnreadablePaperError(ValueError):
    """The PDF has no text a person could ask about."""


def _lines(pdf_path: Path) -> tuple[list[_Line], int]:
    lines: list[_Line] = []
    with pdfplumber.open(pdf_path) as pdf:
        pages = len(pdf.pages)
        for number, page in enumerate(pdf.pages[:MAX_PAGES], start=1):
            for line in page.extract_text_lines(strip=True) or []:
                text = re.sub(r"\s+", " ", str(line.get("text", ""))).strip()
                if not text:
                    continue
                sizes = [float(c["size"]) for c in line.get("chars", []) if "size" in c]
                lines.append(
                    _Line(text=text, size=statistics.median(sizes) if sizes else 0.0, page=number)
                )
    return lines, pages


def _is_heading(line: _Line, body: float) -> bool:
    words = line.text.split()
    if not words or len(words) > _MAX_HEADING_WORDS or line.text.endswith((",", ";")):
        return False
    if _KNOWN_HEADINGS.match(line.text):
        return True
    if line.text.endswith("."):
        return False
    larger = body > 0 and line.size >= body * _HEADING_SIZE_RATIO
    return larger or bool(_HEADING_NUMBERED.match(line.text))


def _title(lines: list[_Line], fallback: str) -> str:
    """Page one's largest text, as long as it reads like a title."""
    first = [line for line in lines if line.page == 1][:40]
    if not first:
        return fallback
    biggest = max(line.size for line in first)
    parts: list[str] = []
    for line in first:
        if line.size >= biggest - 0.5:
            parts.append(line.text)
        elif parts:
            break
    title = " ".join(parts).strip()
    return title if 10 <= len(title) <= 300 else fallback


def parse_paper_pdf(pdf_path: Path, *, title: str | None = None) -> tuple[RawDocument, int]:
    """A research paper as sections that know their page; and its page count."""
    lines, pages = _lines(pdf_path)
    if not lines:
        raise UnreadablePaperError(
            "No text could be read from this PDF — it may be a scanned image."
        )
    body = statistics.median([line.size for line in lines if line.size > 0] or [0.0])
    sections: list[RawSection] = []
    heading = "Opening"
    page = lines[0].page
    parts: list[str] = []

    def flush() -> None:
        content = " ".join(parts).strip()
        if content:
            sections.append(RawSection(title=f"{heading} · p. {page}", content=content))
        parts.clear()

    for line in lines:
        if _is_heading(line, body):
            flush()
            heading = line.text.rstrip(":. ").strip()[:80] or heading
            page = line.page
            continue
        if line.page != page:
            flush()
            page = line.page
        parts.append(line.text)
    flush()
    if not sections:
        raise UnreadablePaperError("No readable passages were found in this PDF.")

    resolved = title or _title(lines, fallback=pdf_path.stem)
    abstract = next(
        (
            s.content
            for s in sections
            if (s.title or "").lower().startswith(("abstract", "summary"))
        ),
        None,
    )
    opening = " ".join([resolved, abstract or sections[0].content])[:6000]
    designs = [name for name, pattern in _DESIGNS if pattern.search(opening)]
    return (
        RawDocument(
            source_type="uploaded",
            external_id=pdf_path.name,
            title=resolved[:500],
            abstract=abstract[:4000] if abstract else None,
            sections=sections,
            publication_types=designs[:1],
        ),
        pages,
    )


# -- answering from one paper -----------------------------------------------------------


async def paper_chunks(pool: DbPool, *, document_id: UUID, org_id: UUID) -> list[RetrievedChunk]:
    """Every passage of one of the organization's papers, in order."""
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT c.id, c.document_id, c.content, c.section, d.title, d.publication_date,
                   d.evidence_grade::text AS grade, d.study_type::text AS study_type,
                   d.journal, d.pmid, d.doi, d.url
            FROM public.documents d
            JOIN public.chunks c ON c.document_id = d.id
            WHERE d.id = $1 AND d.org_id = $2 AND d.metadata ->> 'kind' = $3
            ORDER BY c.chunk_index
            """,
            document_id,
            org_id,
            PAPER_KIND,
        )
    return [
        RetrievedChunk(
            chunk_id=r["id"],
            document_id=r["document_id"],
            content=r["content"],
            section=r["section"],
            title=r["title"],
            publication_date=r["publication_date"],
            evidence_grade=r["grade"],
            study_type=r["study_type"],
            journal=r["journal"],
            pmid=r["pmid"],
            doi=r["doi"],
            url=r["url"],
        )
        for r in rows
    ]


async def _by_meaning(
    pool: DbPool, chunks: list[RetrievedChunk], question: str, embedder: EmbeddingService
) -> list[RetrievedChunk]:
    vector = await embedder.embed_query(question)
    literal = "[" + ",".join(f"{v:.8f}" for v in vector) + "]"
    by_id = {c.chunk_id: c for c in chunks}
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT chunk_id, 1.0 - (embedding <=> $1::vector) AS similarity "
            "FROM public.chunk_embeddings WHERE chunk_id = ANY($2::uuid[]) "
            "ORDER BY embedding <=> $1::vector, chunk_id",
            literal,
            list(by_id),
        )
    return [
        by_id[r["chunk_id"]].with_score("dense", float(r["similarity"]))
        for r in rows
        if r["chunk_id"] in by_id
    ]


async def rank_paper_passages(
    pool: DbPool,
    *,
    chunks: list[RetrievedChunk],
    question: str,
    embedder: EmbeddingService | None,
    limit: int = PASSAGES,
) -> list[RetrievedChunk]:
    """The passages of one paper that answer ``question``, best first, in the
    order the answer should number them."""
    if not chunks:
        return []
    # Ranked on the question's content words: in a handful of passages "what",
    # "was" and "the" look rare to BM25 and would outweigh "hazard ratio".
    words = [
        w
        for w in re.findall(r"[a-z0-9]+", question.lower())
        if w not in STOPWORDS and w not in _QUESTION_FILLER and len(w) > 1
    ]
    by_words = await BM25Reranker().rerank(
        " ".join(words) or question, list(chunks), top_n=len(chunks)
    )
    by_meaning: list[RetrievedChunk] = []
    if embedder is not None:
        try:
            by_meaning = await _by_meaning(pool, chunks, question, embedder)
        except Exception as exc:  # word match alone still answers
            logger.warning("paper_dense_rank_failed", error=type(exc).__name__)
    fused = reciprocal_rank_fusion(by_meaning, by_words, limit=len(chunks))
    chosen: list[RetrievedChunk] = []
    if _SUMMARY_REQUEST.search(question):
        # A summary or an appraisal needs the abstract, the results and the
        # conclusion, whatever words the request used.
        seen_sections: set[str] = set()
        for chunk in chunks:
            label = (chunk.section or "").split(" · ")[0]
            if _CORE_SECTIONS.match(label) and label.lower() not in seen_sections:
                seen_sections.add(label.lower())
                chosen.append(chunk)
            if len(chosen) >= limit - 2:
                break
    for chunk in fused:
        if len(chosen) >= limit:
            break
        if all(c.chunk_id != chunk.chunk_id for c in chosen):
            chosen.append(chunk)
    return chosen


def paper_metadata(*, uploaded_by: UUID, pages: int, filename: str) -> dict[str, Any]:
    """What a paper's documents row carries beyond an ordinary upload."""
    return {
        "kind": PAPER_KIND,
        "uploaded_by": str(uploaded_by),
        "pages": pages,
        "filename": filename,
    }
