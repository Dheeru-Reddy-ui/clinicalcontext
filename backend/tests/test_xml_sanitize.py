"""XML sanitization: illegal control chars in real NCBI responses are survivable."""

from __future__ import annotations

from xml.etree import ElementTree

from app.ingestion.sources.ncbi import sanitize_xml
from app.ingestion.sources.pubmed import parse_pubmed_article_set

# A control character (0x1a / SUB) embedded in abstract text — the exact class
# of byte that made a live efetch batch unparseable during seeding.
DIRTY_XML = (
    "<PubmedArticleSet><PubmedArticle><MedlineCitation>"
    "<PMID>111</PMID><Article>"
    "<Journal><JournalIssue><PubDate><Year>2022</Year></PubDate></JournalIssue>"
    "<Title>Test J</Title></Journal>"
    "<ArticleTitle>Valid title</ArticleTitle>"
    "<Abstract><AbstractText>Body with a \x1a bad byte inside.</AbstractText></Abstract>"
    "</Article></MedlineCitation></PubmedArticle></PubmedArticleSet>"
)


def test_sanitize_removes_illegal_chars_but_keeps_text() -> None:
    cleaned = sanitize_xml(DIRTY_XML)
    assert "\x1a" not in cleaned
    assert "bad byte inside" in cleaned


def test_sanitize_preserves_legal_whitespace() -> None:
    assert sanitize_xml("a\tb\nc\rd") == "a\tb\nc\rd"


def test_raw_dirty_xml_would_fail_but_parser_handles_it() -> None:
    # Prove the byte is genuinely fatal to a naive parse...
    try:
        ElementTree.fromstring(DIRTY_XML)
        raised = False
    except ElementTree.ParseError:
        raised = True
    assert raised, "the test fixture must contain a genuinely illegal byte"

    # ...and that our parser sanitizes and succeeds.
    documents = parse_pubmed_article_set(DIRTY_XML)
    assert len(documents) == 1
    assert documents[0].title == "Valid title"
    assert documents[0].abstract is not None
    assert "bad byte inside" in documents[0].abstract
