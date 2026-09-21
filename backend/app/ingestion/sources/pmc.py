"""PMC open-access source: full-text JATS XML parsed into sections."""

from __future__ import annotations

import re
from collections.abc import AsyncIterator
from datetime import date
from xml.etree import ElementTree

import structlog

from app.ingestion.models import RawDocument, RawSection
from app.ingestion.sources.ncbi import NcbiClient, sanitize_xml

logger = structlog.stdlib.get_logger("app.ingestion.pmc")


def _text(element: ElementTree.Element | None) -> str:
    if element is None:
        return ""
    return re.sub(r"\s+", " ", "".join(element.itertext())).strip()


def _article_id(article: ElementTree.Element, id_type: str) -> str | None:
    for node in article.findall(f".//front//article-id[@pub-id-type='{id_type}']"):
        value = _text(node)
        if value:
            return value
    return None


def _parse_date(article: ElementTree.Element) -> date | None:
    # Prefer electronic publication date, fall back to print.
    for pub_type in ("epub", "ppub", "pub", "collection"):
        node = article.find(f".//front//pub-date[@pub-type='{pub_type}']")
        if node is None:
            continue
        year = _text(node.find("year"))
        if not year.isdigit():
            continue
        month_text = _text(node.find("month"))
        day_text = _text(node.find("day"))
        month = int(month_text) if month_text.isdigit() and 1 <= int(month_text) <= 12 else 1
        day = int(day_text) if day_text.isdigit() else 1
        try:
            return date(int(year), month, day)
        except ValueError:
            return date(int(year), 1, 1)
    return None


def _parse_authors(article: ElementTree.Element) -> list[str]:
    authors: list[str] = []
    for contrib in article.findall(".//front//contrib-group/contrib[@contrib-type='author']"):
        surname = _text(contrib.find(".//surname"))
        given = _text(contrib.find(".//given-names"))
        if surname:
            authors.append(f"{surname} {given}".strip())
    return authors


def _section_text(sec: ElementTree.Element) -> str:
    """All paragraph text within a <sec>, including nested subsections."""
    parts: list[str] = []
    for paragraph in sec.iter("p"):
        content = _text(paragraph)
        if content:
            parts.append(content)
    return "\n".join(parts)


def _parse_body_sections(article: ElementTree.Element) -> list[RawSection]:
    body = article.find(".//body")
    if body is None:
        return []
    sections: list[RawSection] = []
    for sec in body.findall("sec"):  # top-level sections only; nested text folds in
        title = _text(sec.find("title")) or None
        content = _section_text(sec)
        if content:
            sections.append(RawSection(title=title, content=content))
    if not sections:
        # Body without <sec> structure: take loose paragraphs as one section.
        loose = "\n".join(_text(p) for p in body.findall("p") if _text(p))
        if loose:
            sections.append(RawSection(title=None, content=loose))
    return sections


def parse_pmc_article_set(xml_text: str) -> list[RawDocument]:
    """Parse an efetch pmc-articleset into RawDocuments (full text as sections)."""
    root = ElementTree.fromstring(sanitize_xml(xml_text))
    documents: list[RawDocument] = []
    for article in root.findall(".//article"):
        title = _text(article.find(".//front//title-group/article-title"))
        if not title:
            continue
        abstract_node = article.find(".//front//abstract")
        abstract = _text(abstract_node) or None

        sections: list[RawSection] = []
        if abstract:
            sections.append(RawSection(title="Abstract", content=abstract))
        sections.extend(_parse_body_sections(article))

        pmcid = _article_id(article, "pmc")
        documents.append(
            RawDocument(
                source_type="pmc",
                external_id=f"PMC{pmcid}" if pmcid and not pmcid.startswith("PMC") else pmcid,
                title=title,
                abstract=abstract,
                sections=sections,
                authors=_parse_authors(article),
                journal=_text(article.find(".//front//journal-title")) or None,
                publication_date=_parse_date(article),
                doi=_article_id(article, "doi"),
                pmid=_article_id(article, "pmid"),
                url=(f"https://www.ncbi.nlm.nih.gov/pmc/articles/PMC{pmcid}/" if pmcid else None),
                publication_types=[],  # JATS carries no PublicationTypeList
                mesh_terms=[],
            )
        )
    return documents


async def fetch_pmc(client: NcbiClient, *, query: str, limit: int) -> AsyncIterator[RawDocument]:
    """Search the PMC open-access subset and yield full-text documents."""
    term = f"({query}) AND open access[filter]"
    ids = await client.search_ids(db="pmc", term=term, limit=limit)
    logger.info("pmc_search_complete", query=query, matched=len(ids))
    for batch_index, xml_batch in enumerate(await client.fetch_xml_batches(db="pmc", ids=ids)):
        try:
            documents = parse_pmc_article_set(xml_batch)
        except ElementTree.ParseError as exc:
            logger.warning("pmc_batch_unparseable", batch=batch_index, error=str(exc))
            continue
        for document in documents:
            yield document
