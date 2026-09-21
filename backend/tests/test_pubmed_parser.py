"""PubMed XML parsing against a realistic PubmedArticleSet fixture."""

from __future__ import annotations

from datetime import date

from app.ingestion.sources.pubmed import parse_pubmed_article_set

FIXTURE = """<?xml version="1.0" ?>
<PubmedArticleSet>
  <PubmedArticle>
    <MedlineCitation Status="MEDLINE" Owner="NLM">
      <PMID Version="1">12345678</PMID>
      <Article PubModel="Print-Electronic">
        <Journal>
          <ISSN IssnType="Electronic">1234-5678</ISSN>
          <JournalIssue CitedMedium="Internet">
            <Volume>388</Volume>
            <PubDate><Year>2023</Year><Month>Apr</Month><Day>15</Day></PubDate>
          </JournalIssue>
          <Title>Journal of Test Cardiology</Title>
        </Journal>
        <ArticleTitle>Apixaban versus warfarin in atrial fibrillation with stage 4 CKD.</ArticleTitle>
        <ELocationID EIdType="doi" ValidYN="Y">10.1000/test.2023.001</ELocationID>
        <Abstract>
          <AbstractText Label="BACKGROUND">Anticoagulant choice in advanced CKD is uncertain.</AbstractText>
          <AbstractText Label="METHODS">We randomized 1200 patients with AF and stage 4 CKD.</AbstractText>
          <AbstractText Label="RESULTS">Apixaban reduced stroke without excess bleeding.</AbstractText>
          <AbstractText Label="CONCLUSIONS">Apixaban was superior to warfarin.</AbstractText>
        </Abstract>
        <AuthorList CompleteYN="Y">
          <Author ValidYN="Y"><LastName>Smith</LastName><ForeName>Jane</ForeName><Initials>J</Initials></Author>
          <Author ValidYN="Y"><CollectiveName>TEST-AF Investigators</CollectiveName></Author>
        </AuthorList>
        <PublicationTypeList>
          <PublicationType UI="D016449">Randomized Controlled Trial</PublicationType>
          <PublicationType UI="D016428">Journal Article</PublicationType>
        </PublicationTypeList>
      </Article>
      <MeshHeadingList>
        <MeshHeading><DescriptorName UI="D001281" MajorTopicYN="Y">Atrial Fibrillation</DescriptorName></MeshHeading>
        <MeshHeading><DescriptorName UI="D051436" MajorTopicYN="N">Renal Insufficiency, Chronic</DescriptorName></MeshHeading>
      </MeshHeadingList>
    </MedlineCitation>
    <PubmedData>
      <ArticleIdList>
        <ArticleId IdType="pubmed">12345678</ArticleId>
        <ArticleId IdType="doi">10.1000/test.2023.001</ArticleId>
      </ArticleIdList>
    </PubmedData>
  </PubmedArticle>
  <PubmedArticle>
    <MedlineCitation Status="MEDLINE" Owner="NLM">
      <PMID Version="1">99999999</PMID>
      <Article PubModel="Print">
        <Journal>
          <JournalIssue CitedMedium="Print">
            <PubDate><MedlineDate>1998 Jan-Feb</MedlineDate></PubDate>
          </JournalIssue>
          <Title>Archives of Minimal Records</Title>
        </Journal>
        <ArticleTitle>An abstract-less record.</ArticleTitle>
        <AuthorList><Author><LastName>Doe</LastName><Initials>R</Initials></Author></AuthorList>
        <PublicationTypeList>
          <PublicationType UI="D016428">Journal Article</PublicationType>
        </PublicationTypeList>
      </Article>
    </MedlineCitation>
  </PubmedArticle>
</PubmedArticleSet>
"""


def test_full_record_parses_all_metadata() -> None:
    documents = parse_pubmed_article_set(FIXTURE)
    assert len(documents) == 2
    doc = documents[0]

    assert doc.source_type == "pubmed"
    assert doc.pmid == "12345678"
    assert doc.external_id == "12345678"
    assert doc.title == "Apixaban versus warfarin in atrial fibrillation with stage 4 CKD"
    assert doc.journal == "Journal of Test Cardiology"
    assert doc.publication_date == date(2023, 4, 15)
    assert doc.doi == "10.1000/test.2023.001"
    assert doc.url == "https://pubmed.ncbi.nlm.nih.gov/12345678/"
    assert doc.authors == ["Smith J", "TEST-AF Investigators"]
    assert doc.publication_types == ["Randomized Controlled Trial", "Journal Article"]
    assert doc.mesh_terms == ["Atrial Fibrillation", "Renal Insufficiency, Chronic"]


def test_structured_abstract_becomes_labeled_sections() -> None:
    doc = parse_pubmed_article_set(FIXTURE)[0]
    assert [s.title for s in doc.sections] == [
        "Background",
        "Methods",
        "Results",
        "Conclusions",
    ]
    assert doc.abstract is not None
    assert "randomized 1200 patients" in doc.abstract


def test_minimal_record_parses_with_medlinedate_fallback() -> None:
    doc = parse_pubmed_article_set(FIXTURE)[1]
    assert doc.abstract is None
    assert doc.sections == []
    assert doc.publication_date == date(1998, 1, 1)
    assert doc.full_text() == ""
