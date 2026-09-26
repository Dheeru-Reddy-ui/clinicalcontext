"""Every source the symptom check cites, in one place.

Each is a public page from a national health service, a guideline body or
the WHO, checked to resolve when this was written. A rule that no source
states in those words — a stricter threshold chosen for safety — is cited as
a ClinicalContext safety rule instead, so a reader can always tell the two
apart.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Source:
    key: str
    title: str
    publisher: str
    url: str


_ALL = (
    Source(
        "nhs_fever_child",
        "High temperature (fever) in children",
        "NHS",
        "https://www.nhs.uk/conditions/fever-in-children/",
    ),
    Source(
        "nhs_fever_adult",
        "High temperature (fever) in adults",
        "NHS",
        "https://www.nhs.uk/conditions/fever-in-adults/",
    ),
    Source(
        "nice_ng143",
        "Fever in under 5s: assessment and initial management (NG143)",
        "NICE",
        "https://www.nice.org.uk/guidance/ng143",
    ),
    Source(
        "who_dengue",
        "Dengue and severe dengue",
        "World Health Organization",
        "https://www.who.int/news-room/fact-sheets/detail/dengue-and-severe-dengue",
    ),
    Source(
        "nhs_paracetamol_child",
        "Paracetamol for children",
        "NHS",
        "https://www.nhs.uk/medicines/paracetamol-for-children/",
    ),
    Source(
        "nhs_paracetamol_adult",
        "Paracetamol for adults",
        "NHS",
        "https://www.nhs.uk/medicines/paracetamol-for-adults/",
    ),
    Source(
        "nhs_ibuprofen_child",
        "Ibuprofen for children",
        "NHS",
        "https://www.nhs.uk/medicines/ibuprofen-for-children/",
    ),
    Source(
        "nhs_ibuprofen_adult",
        "Ibuprofen for adults",
        "NHS",
        "https://www.nhs.uk/medicines/ibuprofen-for-adults/",
    ),
    Source("nhs_cetirizine", "Cetirizine", "NHS", "https://www.nhs.uk/medicines/cetirizine/"),
    Source("nhs_loratadine", "Loratadine", "NHS", "https://www.nhs.uk/medicines/loratadine/"),
    Source("nhs_loperamide", "Loperamide", "NHS", "https://www.nhs.uk/medicines/loperamide/"),
    Source(
        "nhs_d_and_v",
        "Diarrhoea and vomiting",
        "NHS",
        "https://www.nhs.uk/conditions/diarrhoea-and-vomiting/",
    ),
    Source(
        "who_diarrhoea",
        "Diarrhoeal disease",
        "World Health Organization",
        "https://www.who.int/news-room/fact-sheets/detail/diarrhoeal-disease",
    ),
    Source(
        "who_diarrhoea_manual",
        "The treatment of diarrhoea: a manual for physicians and other senior health workers",
        "World Health Organization",
        "https://www.who.int/publications/i/item/9241593180",
    ),
    Source("nhs_cough", "Cough", "NHS", "https://www.nhs.uk/conditions/cough/"),
    Source("nhs_cold", "Common cold", "NHS", "https://www.nhs.uk/conditions/common-cold/"),
    Source("nhs_sore_throat", "Sore throat", "NHS", "https://www.nhs.uk/conditions/sore-throat/"),
    Source(
        "nice_ng84",
        "Sore throat (acute): antimicrobial prescribing (NG84)",
        "NICE",
        "https://www.nice.org.uk/guidance/ng84",
    ),
    Source(
        "nice_ng120",
        "Cough (acute): antimicrobial prescribing (NG120)",
        "NICE",
        "https://www.nice.org.uk/guidance/ng120",
    ),
    Source(
        "india_ntep",
        "National TB Elimination Programme",
        "Central TB Division, Government of India",
        "https://tbcindia.mohfw.gov.in/",
    ),
    Source("nhs_headaches", "Headaches", "NHS", "https://www.nhs.uk/conditions/headaches/"),
    Source(
        "nhs_uti",
        "Urinary tract infections (UTIs)",
        "NHS",
        "https://www.nhs.uk/conditions/urinary-tract-infections-utis/",
    ),
    Source(
        "nice_ng109",
        "Urinary tract infection (lower): antimicrobial prescribing (NG109)",
        "NICE",
        "https://www.nice.org.uk/guidance/ng109",
    ),
    Source("nhs_hives", "Hives", "NHS", "https://www.nhs.uk/conditions/hives/"),
    Source("nhs_anaphylaxis", "Anaphylaxis", "NHS", "https://www.nhs.uk/conditions/anaphylaxis/"),
    Source("nhs_back_pain", "Back pain", "NHS", "https://www.nhs.uk/conditions/back-pain/"),
    Source("nhs_sepsis", "Sepsis", "NHS", "https://www.nhs.uk/conditions/sepsis/"),
    Source("nhs_meningitis", "Meningitis", "NHS", "https://www.nhs.uk/conditions/meningitis/"),
    Source("nhs_chickenpox", "Chickenpox", "NHS", "https://www.nhs.uk/conditions/chickenpox/"),
    Source(
        "clinicalcontext",
        "ClinicalContext safety rule (stricter than the guidance, chosen for safety)",
        "ClinicalContext",
        "https://clinicalcontext-euev.vercel.app/methodology#symptom-check",
    ),
)

SOURCES: dict[str, Source] = {s.key: s for s in _ALL}


def source(key: str) -> Source:
    return SOURCES[key]
