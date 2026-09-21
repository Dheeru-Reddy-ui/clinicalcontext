"""PubMed source: search + fetch + parse of PubmedArticleSet XML.

Parsing is separated from fetching so the parser is unit-testable against
fixture XML with no network.
"""

from __future__ import annotations

import re
from collections.abc import AsyncIterator
from datetime import date
from xml.etree import ElementTree

import structlog

from app.ingestion.models import RawDocument, RawSection
from app.ingestion.sources.ncbi import NcbiClient, sanitize_xml

logger = structlog.stdlib.get_logger("app.ingestion.pubmed")

_MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}  # fmt: skip


def _text(element: ElementTree.Element | None) -> str:
    """Flatten an element to text, including inline markup (<i>, <sup>, ...)."""
    if element is None:
        return ""
    return "".join(element.itertext()).strip()


def _parse_pub_date(article: ElementTree.Element) -> date | None:
    node = article.find(".//Journal/JournalIssue/PubDate")
    if node is None:
        return None
    year_text = _text(node.find("Year"))
    if not year_text:
        # MedlineDate fallback, e.g. "2020 Jan-Feb" or "1998-1999".
        medline = _text(node.find("MedlineDate"))
        match = re.search(r"\b(19|20)\d{2}\b", medline)
        if not match:
            return None
        return date(int(match.group()), 1, 1)
    month_text = _text(node.find("Month")).lower()[:3]
    month = _MONTHS.get(month_text)
    if month is None:
        month = int(month_text) if month_text.isdigit() and 1 <= int(month_text) <= 12 else 1
    day_text = _text(node.find("Day"))
    day = int(day_text) if day_text.isdigit() else 1
    try:
        return date(int(year_text), month, day)
    except ValueError:
        return date(int(year_text), 1, 1)


def _parse_authors(article: ElementTree.Element) -> list[str]:
    authors: list[str] = []
    for author in article.findall(".//AuthorList/Author"):
        last = _text(author.find("LastName"))
        initials = _text(author.find("Initials"))
        collective = _text(author.find("CollectiveName"))
        if last:
            authors.append(f"{last} {initials}".strip())
        elif collective:
            authors.append(collective)
    return authors


def _parse_abstract_sections(article: ElementTree.Element) -> tuple[str | None, list[RawSection]]:
    """Structured abstracts (Label="METHODS" etc.) become one section each."""
    nodes = article.findall(".//Abstract/AbstractText")
    if not nodes:
        return None, []
    sections: list[RawSection] = []
    for node in nodes:
        content = _text(node)
        if not content:
            continue
        label = node.get("Label") or node.get("NlmCategory")
        sections.append(RawSection(title=label.title() if label else "Abstract", content=content))
    if not sections:
        return None, []
    abstract = "\n".join(section.content for section in sections)
    return abstract, sections


def parse_pubmed_article_set(xml_text: str) -> list[RawDocument]:
    """Parse an efetch PubmedArticleSet document into RawDocuments."""
    root = ElementTree.fromstring(sanitize_xml(xml_text))
    documents: list[RawDocument] = []
    for record in root.findall(".//PubmedArticle"):
        article = record.find(".//Article")
        if article is None:
            continue
        title = _text(article.find("ArticleTitle")).rstrip(".")
        if not title:
            continue
        pmid = _text(record.find(".//MedlineCitation/PMID"))
        abstract, sections = _parse_abstract_sections(article)

        doi = None
        for eid in article.findall("ELocationID"):
            if eid.get("EIdType") == "doi":
                doi = _text(eid) or None
        if doi is None:
            for aid in record.findall(".//PubmedData/ArticleIdList/ArticleId"):
                if aid.get("IdType") == "doi":
                    doi = _text(aid) or None

        publication_types = [
            _text(pt)
            for pt in article.findall(".//PublicationTypeList/PublicationType")
            if _text(pt)
        ]
        mesh_terms = [
            _text(mh.find("DescriptorName"))
            for mh in record.findall(".//MeshHeadingList/MeshHeading")
            if _text(mh.find("DescriptorName"))
        ]

        documents.append(
            RawDocument(
                source_type="pubmed",
                external_id=pmid or None,
                title=title,
                abstract=abstract,
                sections=sections,
                authors=_parse_authors(article),
                journal=_text(article.find(".//Journal/Title")) or None,
                publication_date=_parse_pub_date(article),
                doi=doi,
                pmid=pmid or None,
                url=f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/" if pmid else None,
                publication_types=publication_types,
                mesh_terms=mesh_terms,
            )
        )
    return documents


async def fetch_pubmed(client: NcbiClient, *, query: str, limit: int) -> AsyncIterator[RawDocument]:
    """Search PubMed and yield parsed documents (abstract-less ones included;
    the pipeline decides whether to skip them)."""
    ids = await client.search_ids(db="pubmed", term=query, limit=limit)
    logger.info("pubmed_search_complete", query=query, matched=len(ids))
    for batch_index, xml_batch in enumerate(await client.fetch_xml_batches(db="pubmed", ids=ids)):
        try:
            documents = parse_pubmed_article_set(xml_batch)
        except ElementTree.ParseError as exc:
            # One malformed batch loses ~200 records, not the whole run.
            logger.warning("pubmed_batch_unparseable", batch=batch_index, error=str(exc))
            continue
        for document in documents:
            yield document
