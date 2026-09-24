"""Over-the-counter medicines: the dose for this person, or why not.

Doses are the age bands the NHS publishes for each medicine (the same bands
printed on UK packs) and the WHO's for oral rehydration and zinc — looked
up, never calculated by a model. Before a dose is shown, the person's
profile is checked against the reasons that medicine must not be taken:
age, weight, pregnancy, conditions, allergies, and the medicines they
already take (a second paracetamol-containing product, a blood thinner with
ibuprofen). A medicine that fails a check is still listed, with the reason,
so the person knows why it is not suggested.

Only over-the-counter medicines are dosed here. Prescription medicines
appear in an assessment only as "a doctor may prescribe", never with a dose
for the person.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Literal

from pydantic import BaseModel, Field

Condition = Literal[
    "asthma",
    "kidney_disease",
    "liver_disease",
    "stomach_ulcer",
    "heart_disease",
    "diabetes",
    "weak_immunity",
    "bleeding_disorder",
    "high_blood_pressure",
    "lung_disease",
]


class Profile(BaseModel):
    """Who the symptom check is for. No name, no date of birth: age is all
    the dosing needs, and identifiers are never collected."""

    age_years: float = Field(ge=0, le=120)
    sex: Literal["female", "male", "other"] | None = None
    weight_kg: float | None = Field(default=None, gt=0, le=350)
    pregnant: bool = False
    breastfeeding: bool = False
    conditions: list[Condition] = Field(default_factory=list)
    allergies: list[str] = Field(default_factory=list, max_length=20)
    medicines: list[str] = Field(default_factory=list, max_length=30)

    @property
    def months(self) -> float:
        return self.age_years * 12

    def has(self, condition: Condition) -> bool:
        return condition in self.conditions


@dataclass(frozen=True, slots=True)
class DoseBand:
    min_months: float
    max_months: float  # exclusive
    dose: str
    how_often: str
    maximum: str


@dataclass(slots=True)
class MedicineAdvice:
    key: str
    name: str
    purpose: str
    suitable: bool
    dose: str | None = None
    how_often: str | None = None
    maximum: str | None = None
    notes: list[str] = field(default_factory=list)
    reason_not_suitable: str | None = None
    source_keys: list[str] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class Context:
    """What the complaint adds to the checks."""

    fever: bool = False
    # Fever where dengue is common and has not been ruled out: WHO advises
    # against ibuprofen and aspirin (bleeding risk).
    dengue_possible: bool = False
    dehydrated: bool = False
    blood_in_stool: bool = False


# -- the medicines people already take -------------------------------------------------

_PARACETAMOL_PRODUCTS = re.compile(
    r"paracetamol|acetaminophen|crocin|dolo|calpol|combiflam|sinarest|p[-\s]?500|pacimol|tylenol",
    re.I,
)
_NSAIDS = re.compile(
    r"ibuprofen|brufen|combiflam|diclofenac|voveran|naproxen|aceclofenac|zerodol|etoricoxib|"
    r"celecoxib|mefenamic|meftal|ketorolac|nimesulide|indomethacin|piroxicam|aspirin|ecosprin",
    re.I,
)
_BLOOD_THINNERS = re.compile(
    r"warfarin|acenocoumarol|apixaban|eliquis|rivaroxaban|xarelto|dabigatran|edoxaban|"
    r"heparin|enoxaparin|clopidogrel|clopilet|ticagrelor|prasugrel|aspirin|ecosprin",
    re.I,
)
_LITHIUM_METHOTREXATE = re.compile(r"lithium|methotrexate", re.I)
_KIDNEY_STRAIN = re.compile(
    r"\w+pril\b|\w+sartan\b|furosemide|lasix|torsemide|hydrochlorothiazide|chlorthalidone|"
    r"spironolactone",
    re.I,
)
_STEROIDS = re.compile(
    r"prednisolone|prednisone|dexamethasone|methylprednisolone|hydrocortisone|deflazacort", re.I
)
_SSRIS = re.compile(r"sertraline|fluoxetine|escitalopram|citalopram|paroxetine|fluvoxamine", re.I)

_ALLERGY: dict[str, re.Pattern[str]] = {
    "paracetamol": re.compile(r"paracetamol|acetaminophen", re.I),
    # An allergy to any NSAID rules ibuprofen out (cross-reaction).
    "ibuprofen": re.compile(r"ibuprofen|nsaid|aspirin|diclofenac|naproxen|painkiller", re.I),
    "cetirizine": re.compile(r"cetirizine|levocetirizine|hydroxyzine|antihistamine", re.I),
    "loratadine": re.compile(r"loratadine|desloratadine|antihistamine", re.I),
    "loperamide": re.compile(r"loperamide", re.I),
}


def _takes(profile: Profile, pattern: re.Pattern[str]) -> list[str]:
    return [m for m in profile.medicines if pattern.search(m)]


def _allergic(profile: Profile, key: str) -> bool:
    pattern = _ALLERGY.get(key)
    return pattern is not None and any(pattern.search(a) for a in profile.allergies)


def _band(bands: tuple[DoseBand, ...], months: float) -> DoseBand | None:
    return next((b for b in bands if b.min_months <= months < b.max_months), None)


def _unsuitable(
    key: str, name: str, purpose: str, reason: str, sources: list[str]
) -> MedicineAdvice:
    return MedicineAdvice(
        key=key,
        name=name,
        purpose=purpose,
        suitable=False,
        reason_not_suitable=reason,
        source_keys=sources,
    )


# -- paracetamol -----------------------------------------------------------------------

PARACETAMOL_BANDS: tuple[DoseBand, ...] = (
    DoseBand(
        3,
        6,
        "60 mg (2.5 ml of 120 mg/5 ml infant liquid)",
        "up to 4 times in 24 hours, at least 4 hours apart",
        "4 doses in 24 hours",
    ),
    DoseBand(
        6,
        24,
        "120 mg (5 ml of 120 mg/5 ml infant liquid)",
        "up to 4 times in 24 hours, at least 4 hours apart",
        "4 doses in 24 hours",
    ),
    DoseBand(
        24,
        48,
        "180 mg (7.5 ml of 120 mg/5 ml infant liquid)",
        "up to 4 times in 24 hours, at least 4 hours apart",
        "4 doses in 24 hours",
    ),
    DoseBand(
        48,
        72,
        "240 mg (10 ml of 120 mg/5 ml infant liquid)",
        "up to 4 times in 24 hours, at least 4 hours apart",
        "4 doses in 24 hours",
    ),
    DoseBand(
        72,
        96,
        "250 mg (5 ml of 250 mg/5 ml 'six plus' liquid)",
        "up to 4 times in 24 hours, at least 4 hours apart",
        "4 doses in 24 hours",
    ),
    DoseBand(
        96,
        120,
        "375 mg (7.5 ml of 250 mg/5 ml 'six plus' liquid)",
        "up to 4 times in 24 hours, at least 4 hours apart",
        "4 doses in 24 hours",
    ),
    DoseBand(
        120,
        144,
        "500 mg (10 ml of 250 mg/5 ml liquid, or one 500 mg tablet)",
        "up to 4 times in 24 hours, at least 4 hours apart",
        "4 doses in 24 hours",
    ),
    DoseBand(
        144,
        192,
        "500 to 750 mg (10 to 15 ml of 250 mg/5 ml liquid)",
        "up to 4 times in 24 hours, at least 4 hours apart",
        "4 doses in 24 hours",
    ),
    DoseBand(
        192,
        1500,
        "500 mg to 1 g (one or two 500 mg tablets)",
        "up to 4 times in 24 hours, at least 4 hours apart",
        "4 g (eight 500 mg tablets) in 24 hours",
    ),
)


def paracetamol(profile: Profile, context: Context) -> MedicineAdvice:
    purpose = "Brings down a high temperature and eases pain"
    child = profile.age_years < 16
    sources = ["nhs_paracetamol_child" if child else "nhs_paracetamol_adult"]
    if _allergic(profile, "paracetamol"):
        return _unsuitable(
            "paracetamol", "Paracetamol", purpose, "You listed an allergy to paracetamol.", sources
        )
    if profile.months < 3:
        return _unsuitable(
            "paracetamol",
            "Paracetamol",
            purpose,
            "Babies under 3 months should be seen by a doctor before being given paracetamol "
            "for a fever.",
            [*sources, "nice_ng143"],
        )
    band = _band(PARACETAMOL_BANDS, profile.months)
    assert band is not None
    notes: list[str] = []
    already = _takes(profile, _PARACETAMOL_PRODUCTS)
    if already:
        notes.append(
            f"You already take {', '.join(already)}, which may contain paracetamol — do not take "
            "both. Check every label: too much paracetamol damages the liver."
        )
    if profile.has("liver_disease"):
        notes.append(
            "Liver disease: ask a doctor or pharmacist for the right dose before taking it."
        )
    if not child and profile.weight_kg is not None and profile.weight_kg < 50:
        notes.append("You weigh under 50 kg: ask a pharmacist — you may need a lower dose.")
    notes.append(
        "Do not give it to a child for more than 3 days without speaking to a doctor."
        if child
        else "If it's not helping after 3 days, see a doctor."
    )
    return MedicineAdvice(
        key="paracetamol",
        name="Paracetamol (acetaminophen)",
        purpose=purpose,
        suitable=True,
        dose=band.dose,
        how_often=band.how_often,
        maximum=band.maximum,
        notes=notes,
        source_keys=sources,
    )


# -- ibuprofen -------------------------------------------------------------------------

IBUPROFEN_BANDS: tuple[DoseBand, ...] = (
    DoseBand(
        3,
        6,
        "50 mg (2.5 ml of 100 mg/5 ml infant liquid) — only if your baby weighs over 5 kg",
        "up to 3 times in 24 hours; not for more than 24 hours without a doctor",
        "3 doses in 24 hours",
    ),
    DoseBand(
        6,
        12,
        "50 mg (2.5 ml of 100 mg/5 ml liquid)",
        "3 or 4 times in 24 hours, 6 to 8 hours apart",
        "4 doses in 24 hours",
    ),
    DoseBand(
        12,
        48,
        "100 mg (5 ml of 100 mg/5 ml liquid)",
        "up to 3 times in 24 hours, 6 to 8 hours apart",
        "3 doses in 24 hours",
    ),
    DoseBand(
        48,
        84,
        "150 mg (7.5 ml of 100 mg/5 ml liquid)",
        "up to 3 times in 24 hours, 6 to 8 hours apart",
        "3 doses in 24 hours",
    ),
    DoseBand(
        84,
        120,
        "200 mg (10 ml of 100 mg/5 ml liquid)",
        "up to 3 times in 24 hours, 6 to 8 hours apart",
        "3 doses in 24 hours",
    ),
    DoseBand(
        120,
        144,
        "the dose on the pack of a 'seven plus' liquid or 200 mg tablets — ask a pharmacist",
        "up to 3 times in 24 hours",
        "as on the pack",
    ),
    DoseBand(
        144,
        1500,
        "200 to 400 mg (one or two 200 mg tablets) with or after food",
        "up to 3 times a day, at least 4 hours apart",
        "1,200 mg (six 200 mg tablets) in 24 hours",
    ),
)


def ibuprofen(profile: Profile, context: Context) -> MedicineAdvice:
    purpose = "Eases pain and inflammation, and brings down a temperature"
    child = profile.age_years < 16
    sources = ["nhs_ibuprofen_child" if child else "nhs_ibuprofen_adult"]
    name = "Ibuprofen"

    def no(reason: str, extra: list[str] | None = None) -> MedicineAdvice:
        return _unsuitable("ibuprofen", name, purpose, reason, [*sources, *(extra or [])])

    if _allergic(profile, "ibuprofen"):
        return no(
            "You listed an allergy to ibuprofen, aspirin or another anti-inflammatory painkiller."
        )
    if profile.months < 3:
        return no("Not for babies under 3 months.")
    if profile.months < 6 and (profile.weight_kg is None or profile.weight_kg <= 5):
        return no(
            "Babies aged 3 to 5 months can only have ibuprofen if they weigh over 5 kg — "
            "add the weight, or ask a pharmacist."
        )
    if context.dengue_possible:
        return no(
            "Not for a fever until a doctor has ruled out dengue: ibuprofen and aspirin can "
            "increase the risk of bleeding in dengue. Use paracetamol instead.",
            ["who_dengue"],
        )
    if profile.pregnant:
        return no(
            "Not recommended in pregnancy unless a doctor advises it — use paracetamol instead."
        )
    if context.dehydrated or context.blood_in_stool:
        return no("Not while dehydrated (it can harm the kidneys) — use paracetamol instead.")
    blocking: dict[Condition, str] = {
        "kidney_disease": "kidney disease",
        "stomach_ulcer": "a stomach ulcer or bleeding",
        "heart_disease": "heart failure or heart disease",
        "bleeding_disorder": "a bleeding disorder",
        "liver_disease": "liver disease",
    }
    for condition, words in blocking.items():
        if profile.has(condition):
            return no(f"Not suitable with {words} unless a doctor says so.")
    thinners = _takes(profile, _BLOOD_THINNERS)
    if thinners:
        return no(f"You take {', '.join(thinners)} — with ibuprofen the risk of bleeding rises.")
    others = [m for m in _takes(profile, _NSAIDS) if not re.search(r"ibuprofen|brufen", m, re.I)]
    if others:
        return no(
            f"You already take {', '.join(others)}, another anti-inflammatory — never take two."
        )
    interacting = _takes(profile, _LITHIUM_METHOTREXATE)
    if interacting:
        return no(f"Ibuprofen interacts with {', '.join(interacting)} — ask your doctor.")
    band = _band(IBUPROFEN_BANDS, profile.months)
    assert band is not None
    notes: list[str] = []
    if profile.has("asthma"):
        notes.append(
            "Asthma: ibuprofen can make some people's asthma worse. Don't take it if it has "
            "before; for children with asthma, ask a pharmacist first."
        )
    if profile.has("high_blood_pressure") or _takes(profile, _KIDNEY_STRAIN):
        notes.append(
            "Blood-pressure or water tablets: ibuprofen can raise blood pressure and strain the "
            "kidneys — check with a pharmacist first."
        )
    if _takes(profile, _STEROIDS) or _takes(profile, _SSRIS):
        notes.append(
            "With steroids or some antidepressants, the risk of stomach bleeding is higher — "
            "ask a pharmacist first."
        )
    if child:
        notes.append("Do not give it with chickenpox.")
    notes.append(
        "Take it with or after food. Don't take it for more than 3 days (children) or 10 "
        "days (adults) without advice."
    )
    if context.fever:
        notes.append("Where dengue is possible, paracetamol is the safer choice for a fever.")
    return MedicineAdvice(
        key="ibuprofen",
        name=name,
        purpose=purpose,
        suitable=True,
        dose=band.dose,
        how_often=band.how_often,
        maximum=band.maximum,
        notes=notes,
        source_keys=sources,
    )


# -- antihistamines -------------------------------------------------------------------

CETIRIZINE_BANDS: tuple[DoseBand, ...] = (
    DoseBand(24, 72, "2.5 mg (liquid)", "twice a day", "5 mg in 24 hours"),
    DoseBand(72, 144, "5 mg", "twice a day", "10 mg in 24 hours"),
    DoseBand(144, 1500, "10 mg", "once a day", "10 mg in 24 hours"),
)


def cetirizine(profile: Profile, context: Context) -> MedicineAdvice:
    purpose = "Calms itching, hives and allergy symptoms"
    sources = ["nhs_cetirizine"]
    if _allergic(profile, "cetirizine"):
        return _unsuitable(
            "cetirizine",
            "Cetirizine",
            purpose,
            "You listed an allergy to this kind of antihistamine.",
            sources,
        )
    if profile.months < 24:
        return _unsuitable(
            "cetirizine",
            "Cetirizine",
            purpose,
            "For children under 2, a doctor works out the dose from their weight.",
            sources,
        )
    band = _band(CETIRIZINE_BANDS, profile.months)
    assert band is not None
    notes = ["It can make some people sleepy — don't drive if it affects you."]
    if profile.has("kidney_disease"):
        notes.append("Kidney disease: the dose is usually lower — ask a pharmacist or doctor.")
    if profile.pregnant or profile.breastfeeding:
        notes.append(
            "Pregnant or breastfeeding: ask a pharmacist or doctor which antihistamine to use."
        )
    return MedicineAdvice(
        key="cetirizine",
        name="Cetirizine (antihistamine)",
        purpose=purpose,
        suitable=True,
        dose=band.dose,
        how_often=band.how_often,
        maximum=band.maximum,
        notes=notes,
        source_keys=sources,
    )


# -- diarrhoea -------------------------------------------------------------------------


def oral_rehydration(profile: Profile, context: Context) -> MedicineAdvice:
    if profile.months < 24:
        amount = "50 to 100 ml"
    elif profile.months < 120:
        amount = "100 to 200 ml"
    else:
        amount = "as much as you want to drink"
    return MedicineAdvice(
        key="ors",
        name="Oral rehydration solution (ORS)",
        purpose="Replaces the water and salts lost in diarrhoea and vomiting",
        suitable=True,
        dose=f"{amount} after each loose stool",
        how_often="after every loose stool, in small sips if vomiting",
        maximum="no fixed maximum — keep drinking",
        notes=[
            "Mix one sachet in exactly the amount of clean water the pack says.",
            "Keep breastfeeding and keep eating normal food if you can.",
        ],
        source_keys=["who_diarrhoea_manual", "who_diarrhoea", "nhs_d_and_v"],
    )


def zinc(profile: Profile, context: Context) -> MedicineAdvice | None:
    """Zinc is for children's diarrhoea (under 5); adults get no entry."""
    if profile.age_years >= 5:
        return None
    dose = "10 mg" if profile.months < 6 else "20 mg"
    return MedicineAdvice(
        key="zinc",
        name="Zinc (dispersible tablets or syrup)",
        purpose="Shortens a child's diarrhoea and makes it less severe",
        suitable=True,
        dose=f"{dose} once a day",
        how_often="once a day for 10 to 14 days, even after the diarrhoea stops",
        maximum=f"{dose} a day",
        notes=["Given together with ORS, not instead of it."],
        source_keys=["who_diarrhoea_manual", "who_diarrhoea"],
    )


def loperamide(profile: Profile, context: Context) -> MedicineAdvice:
    purpose = "Slows diarrhoea for a few hours (adults and children 12 and over)"
    sources = ["nhs_loperamide", "nhs_d_and_v"]
    if profile.age_years < 12:
        return _unsuitable(
            "loperamide",
            "Loperamide",
            purpose,
            "Not for children under 12 unless a doctor prescribes it.",
            sources,
        )
    if _allergic(profile, "loperamide"):
        return _unsuitable(
            "loperamide", "Loperamide", purpose, "You listed an allergy to loperamide.", sources
        )
    if context.blood_in_stool or context.fever:
        return _unsuitable(
            "loperamide",
            "Loperamide",
            purpose,
            "Not with blood in the stool or a high temperature — that needs a doctor.",
            sources,
        )
    notes = ["It doesn't replace fluids — keep drinking ORS."]
    if profile.pregnant or profile.breastfeeding:
        notes.append("Pregnant or breastfeeding: ask a pharmacist or doctor first.")
    return MedicineAdvice(
        key="loperamide",
        name="Loperamide",
        purpose=purpose,
        suitable=True,
        dose="4 mg (2 capsules) at first, then 2 mg (1 capsule) after each loose stool",
        how_often="after each loose stool",
        maximum="12 mg (6 capsules) in 24 hours; not for more than 48 hours without a doctor",
        notes=notes,
        source_keys=sources,
    )
