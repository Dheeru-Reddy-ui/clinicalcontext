"""PMC JATS full-text parsing against a realistic pmc-articleset fixture."""

from __future__ import annotations

from datetime import date

from app.ingestion.sources.pmc import parse_pmc_article_set

FIXTURE = """<?xml version="1.0" ?>
<pmc-articleset>
  <article article-type="research-article">
    <front>
      <journal-meta>
        <journal-title-group><journal-title>Test Critical Care</journal-title></journal-title-group>
      </journal-meta>
      <article-meta>
        <article-id pub-id-type="pmc">9876543</article-id>
        <article-id pub-id-type="pmid">87654321</article-id>
        <article-id pub-id-type="doi">10.1000/pmc.test.2024</article-id>
        <title-group><article-title>Sepsis bundle adherence and mortality: a cohort study</article-title></title-group>
        <contrib-group>
          <contrib contrib-type="author"><name><surname>Lee</surname><given-names>Ana</given-names></name></contrib>
          <contrib contrib-type="author"><name><surname>Okafor</surname><given-names>Chidi</given-names></name></contrib>
        </contrib-group>
        <pub-date pub-type="epub"><day>2</day><month>3</month><year>2024</year></pub-date>
        <abstract><p>We evaluated sepsis bundle adherence in 500 ICU patients.</p></abstract>
      </article-meta>
    </front>
    <body>
      <sec id="s1"><title>Background</title><p>Sepsis remains a leading cause of ICU mortality.</p></sec>
      <sec id="s2"><title>Methods</title>
        <p>Retrospective cohort of 500 consecutive admissions.</p>
        <sec id="s2a"><title>Statistical analysis</title><p>Cox proportional hazards models were used.</p></sec>
      </sec>
      <sec id="s3"><title>Results</title><p>Bundle adherence was associated with lower mortality.</p></sec>
      <sec id="s4"><title>Discussion</title><p>Findings support protocolized care.</p></sec>
    </body>
  </article>
</pmc-articleset>
"""


def test_pmc_metadata_parses() -> None:
    documents = parse_pmc_article_set(FIXTURE)
    assert len(documents) == 1
    doc = documents[0]

    assert doc.source_type == "pmc"
    assert doc.external_id == "PMC9876543"
    assert doc.pmid == "87654321"
    assert doc.doi == "10.1000/pmc.test.2024"
    assert doc.journal == "Test Critical Care"
    assert doc.publication_date == date(2024, 3, 2)
    assert doc.authors == ["Lee Ana", "Okafor Chidi"]
    assert doc.title == "Sepsis bundle adherence and mortality: a cohort study"


def test_pmc_body_sections_extracted_in_order() -> None:
    doc = parse_pmc_article_set(FIXTURE)[0]
    titles = [s.title for s in doc.sections]
    assert titles == ["Abstract", "Background", "Methods", "Results", "Discussion"]

    methods = doc.sections[2]
    # Nested subsection text folds into its top-level section.
    assert "Retrospective cohort" in methods.content
    assert "Cox proportional hazards" in methods.content
