"""Drug labels from openFDA, filed into the shared corpus.

Journal abstracts rarely state a dose; the label always does. When a
question names a medicine, its current FDA label (openFDA's copy of DailyMed)
is fetched and filed as a ``drug_label`` document — indications, dosing,
contraindications, warnings, interactions, use in pregnancy and children, and
for over-the-counter products the Drug Facts directions — so an answer about
a dose cites the regulator's text, with a link to the DailyMed page.

openFDA is free and needs no key for this volume (1,000 requests a day per
address without one). Names are matched exactly against the label's generic
name, so "amoxicillin" finds amoxicillin, not amoxicillin-clavulanate.
"""

from __future__ import annotations

import re
from datetime import date
from typing import Any

import httpx
import structlog

from app.ingestion.models import RawDocument, RawSection

logger = structlog.stdlib.get_logger("app.knowledge.drugs")

OPENFDA_LABEL = "https://api.fda.gov/drug/label.json"

# Label sections in reading order, with the heading each is filed under.
_SECTIONS: tuple[tuple[str, str], ...] = (
    ("indications_and_usage", "Indications and usage"),
    ("purpose", "Purpose"),
    ("uses", "Uses"),
    ("dosage_and_administration", "Dosage and administration"),
    ("directions", "Directions"),
    ("dosage_forms_and_strengths", "Dosage forms and strengths"),
    ("contraindications", "Contraindications"),
    ("do_not_use", "Do not use"),
    ("ask_doctor", "Ask a doctor before use"),
    ("ask_doctor_or_pharmacist", "Ask a doctor or pharmacist before use"),
    ("stop_use", "Stop use and ask a doctor"),
    ("warnings_and_cautions", "Warnings and precautions"),
    ("warnings", "Warnings"),
    ("boxed_warning", "Boxed warning"),
    ("drug_interactions", "Drug interactions"),
    ("pregnancy", "Pregnancy"),
    ("pregnancy_or_breast_feeding", "Pregnancy or breast-feeding"),
    ("pediatric_use", "Pediatric use"),
    ("geriatric_use", "Geriatric use"),
    ("use_in_specific_populations", "Use in specific populations"),
    ("adverse_reactions", "Adverse reactions"),
    ("overdosage", "Overdosage"),
)
_SECTION_CHARS = 2400
_DRUG_QUESTION = re.compile(
    r"\b(?:dose|doses|dosage|dosing|mg|mcg|tablet|tablets|capsule|syrup|injection|"
    r"side[-\s]effects?|adverse|interaction|interacts?|contraindicat\w*|overdose|"
    r"prescri\w*|medicine|medication|drug|drugs|antibiotic|pill|pills|safe\s+in\s+pregnancy)\b",
    re.I,
)
_CANDIDATE = re.compile(r"\b[a-z][a-z\-]{4,}\b")


def mentions_a_medicine(question: str) -> bool:
    return bool(_DRUG_QUESTION.search(question))


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def label_document(label: dict[str, Any]) -> RawDocument | None:
    """One openFDA label → a RawDocument, or None when it has no usable text."""
    openfda = label.get("openfda") or {}
    generic = ", ".join(str(g) for g in openfda.get("generic_name") or []) or None
    brand = ", ".join(str(b) for b in (openfda.get("brand_name") or [])[:3]) or None
    if not generic:
        return None
    sections: list[RawSection] = []
    for key, heading in _SECTIONS:
        values = label.get(key)
        if not values:
            continue
        text = _clean(" ".join(str(v) for v in values))
        # Labels repeat their own heading as the first words of the section.
        text = re.sub(rf"^\d*\s*{re.escape(heading)}\s*", "", text, flags=re.I)
        if len(text) < 20:
            continue
        if len(text) > _SECTION_CHARS:
            text = text[:_SECTION_CHARS].rsplit(" ", 1)[0] + " …"
        sections.append(RawSection(title=heading, content=text))
    if not sections:
        return None
    effective = str(label.get("effective_time") or "")
    published: date | None = None
    if len(effective) >= 8 and effective[:8].isdigit():
        published = date(int(effective[:4]), int(effective[4:6]), int(effective[6:8]))
    set_id = str(label.get("set_id") or "") or None
    title = f"{generic.title()} — FDA drug label" + (f" ({brand})" if brand else "")
    return RawDocument(
        source_type="drug_label",
        external_id=set_id,
        title=title,
        abstract=sections[0].content,
        sections=sections,
        journal="DailyMed / openFDA",
        publication_date=published,
        url=(
            f"https://dailymed.nlm.nih.gov/dailymed/lookup.cfm?setid={set_id}" if set_id else None
        ),
    )


async def find_labels(
    question: str, *, limit: int = 2, client: httpx.AsyncClient | None = None
) -> list[RawDocument]:
    """Labels for the medicines ``question`` names (at most ``limit``).

    One request: the question's longer words are offered as exact generic
    names together; words that are not a medicine simply match nothing."""
    words = []
    for word in _CANDIDATE.findall(question.lower()):
        if word not in words:
            words.append(word)
    if not words:
        return []
    names = " ".join(f'"{w.upper()}"' for w in words[:12])
    params = {
        "search": f"openfda.generic_name.exact:({names})",
        "limit": str(limit * 3),
        "sort": "effective_time:desc",
    }
    owned = client is None
    http = client or httpx.AsyncClient(timeout=6.0)
    try:
        response = await http.get(OPENFDA_LABEL, params=params)
        if response.status_code == 404:  # openFDA's "no matches"
            return []
        response.raise_for_status()
        results: list[dict[str, Any]] = response.json().get("results", [])
    except (httpx.HTTPError, ValueError) as exc:
        logger.warning("openfda_unavailable", error=f"{type(exc).__name__}: {exc}")
        return []
    finally:
        if owned:
            await http.aclose()
    documents: list[RawDocument] = []
    seen: set[str] = set()
    for label in results:
        document = label_document(label)
        if document is None:
            continue
        key = document.title.split(" — ")[0].lower()
        if key in seen:
            continue  # one label per medicine: the newest comes first
        seen.add(key)
        documents.append(document)
        if len(documents) == limit:
            break
    return documents
