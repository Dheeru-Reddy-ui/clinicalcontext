"""The complaints the symptom check covers, and what each one asks.

Every protocol asks the danger signs first — as one checklist, so a person
in trouble is told to get help after one tap, not ten — then what decides
between "see a doctor" and "look after it at home", then gives the advice.
Warning signs are the NHS's lists for each complaint; the thresholds for
children are NICE's; dengue, diarrhoea and zinc follow the WHO; India's TB
programme sets the two-week cough rule. Where ClinicalContext is stricter
than any of them (fever for three days in an adult, fever in pregnancy), the
reason is cited as its own safety rule.
"""

from __future__ import annotations

from app.treatment.formulary import (
    Context,
    Profile,
    cetirizine,
    ibuprofen,
    loperamide,
    oral_rehydration,
    paracetamol,
    zinc,
)
from app.treatment.model import (
    Answers,
    Assessment,
    DoctorOption,
    Option,
    Protocol,
    Question,
    Reason,
    chosen,
    is_child,
    is_under_five,
)

NONE = "none"


def _baby(profile: Profile) -> bool:
    return profile.age_years < 1


def _adult(profile: Profile) -> bool:
    return profile.age_years >= 16


def _over_50(profile: Profile) -> bool:
    return profile.age_years >= 50


# -- shared danger signs ---------------------------------------------------------------

_SEVERE_ILLNESS = (
    Option(
        "drowsy",
        "Very drowsy, hard to wake, confused, or not responding normally",
        "emergency",
        "Drowsiness, confusion or not responding normally can mean a serious infection.",
        "nhs_fever_child",
    ),
    Option(
        "breathing",
        "Struggling to breathe, or breathing very fast",
        "emergency",
        "Difficulty breathing needs emergency care.",
        "nhs_fever_child",
    ),
    Option(
        "colour",
        "Skin, lips or tongue blue, grey, very pale or blotchy",
        "emergency",
        "A blue, grey or blotchy colour can be a sign of sepsis.",
        "nhs_sepsis",
    ),
    Option(
        "fit",
        "A fit (seizure)",
        "emergency",
        "A fit with a fever needs urgent assessment, especially the first time.",
        "nhs_fever_child",
    ),
)

# -- fever -----------------------------------------------------------------------------

_FEVER_DANGER = Question(
    id="fever_danger",
    text="Do any of these apply right now?",
    kind="multi",
    help="Tick everything that applies. If none do, choose “None of these”.",
    options=(
        *_SEVERE_ILLNESS,
        Option(
            "neck",
            "Stiff neck, or pain when looking at bright lights",
            "emergency",
            "A stiff neck or pain from light with a fever can be meningitis.",
            "nhs_meningitis",
        ),
        Option(
            "rash_glass",
            "A rash that does not fade when a glass is pressed on it",
            "emergency",
            "A rash that does not fade under a glass with a fever can be meningitis or sepsis.",
            "nhs_meningitis",
        ),
        Option(
            "cold_hands",
            "Unusually cold hands and feet",
            "emergency",
            "Cold hands and feet with a fever can be a sign of serious illness in a child.",
            "nhs_fever_child",
            only=is_child,
        ),
        Option(
            "not_feeding",
            "Not feeding, or not interested in anything",
            "urgent",
            "A child who is not feeding or not their usual self needs to be seen today.",
            "nhs_fever_child",
            only=is_child,
        ),
    ),
)

_FEVER_TEMPERATURE = Question(
    id="fever_temp",
    text="What is the highest temperature you measured?",
    kind="single",
    options=(
        Option("not_measured", "I haven't measured it"),
        Option("under_38", "Below 38°C (100.4°F)"),
        Option("38", "38 to 38.9°C (100.4 to 102°F)"),
        Option("39", "39 to 39.9°C (102.2 to 103.8°F)"),
        Option("40", "40°C (104°F) or higher"),
    ),
)

_FEVER_DAYS = Question(
    id="fever_days",
    text="How long has the fever lasted?",
    kind="single",
    options=(
        Option("under_1", "Less than a day"),
        Option("1_2", "1 to 2 days"),
        Option("3_4", "3 to 4 days"),
        Option("5_plus", "5 days or more"),
    ),
)

_FEVER_WARNING = Question(
    id="fever_warning",
    text="Have you noticed any of these?",
    kind="multi",
    help="These are warning signs of dengue and other infections that need a doctor.",
    options=(
        Option(
            "bleeding",
            "Bleeding gums or nose, blood in vomit or stool, or unusual bruising",
            "emergency",
            "Bleeding with a fever is a warning sign of severe dengue.",
            "who_dengue",
        ),
        Option(
            "abdominal",
            "Severe tummy pain",
            "emergency",
            "Severe tummy pain with a fever is a warning sign of severe dengue.",
            "who_dengue",
        ),
        Option(
            "vomiting",
            "Vomiting again and again, or can't keep fluids down",
            "urgent",
            "Persistent vomiting with a fever is a warning sign (dengue, dehydration).",
            "who_dengue",
        ),
        Option(
            "restless",
            "Extreme tiredness or restlessness",
            "urgent",
            "Fatigue and restlessness can be warning signs of severe dengue.",
            "who_dengue",
        ),
        Option(
            "urine",
            "Burning when passing urine, or pain in the back or side",
            "urgent",
            "Fever with urinary symptoms or back pain can be a kidney infection.",
            "nhs_uti",
        ),
        Option(
            "chest",
            "Cough with chest pain or shortness of breath",
            "urgent",
            "Fever with chest pain or breathlessness can be pneumonia.",
            "nhs_cough",
        ),
        Option(
            "rash",
            "A rash (that does fade under a glass)",
            "urgent",
            "A fever with a rash should be checked by a doctor.",
            "nhs_fever_child",
        ),
        Option(
            "body_pain",
            "Severe headache, pain behind the eyes, or severe muscle and joint pain",
            "soon",
            "These are typical of dengue — a blood test can confirm it.",
            "who_dengue",
        ),
    ),
)

_RISK_FACTORS = Question(
    id="risk",
    text="Does any of this apply?",
    kind="multi",
    options=(
        Option(
            "immunity",
            "Weak immune system (chemotherapy, long-term steroids, HIV, no spleen, transplant)",
            "urgent",
            "With a weak immune system, infections can become serious quickly.",
            "nhs_sepsis",
        ),
        Option(
            "chronic",
            "Long-term heart, lung, kidney or liver disease, or diabetes",
            "soon",
            "With a long-term condition, a doctor should check an infection early.",
            "clinicalcontext",
        ),
        Option(
            "recent_surgery",
            "An operation, or giving birth, in the last 6 weeks",
            "urgent",
            "A fever soon after surgery or childbirth needs a doctor today.",
            "nhs_sepsis",
        ),
    ),
)


def _assess_fever(profile: Profile, answers: Answers, result: Assessment) -> None:
    temp = next(iter(chosen(answers, "fever_temp")), "not_measured")
    days = next(iter(chosen(answers, "fever_days")), "under_1")
    warning = chosen(answers, "fever_warning")
    hot = temp in ("38", "39", "40")

    if profile.months < 6 and temp in ("39", "40"):
        result.raise_to(
            "urgent",
            "A baby aged 3 to 6 months with a temperature of 39°C or more needs a doctor today.",
            "nhs_fever_child",
        )
    if is_child(profile) and days == "5_plus":
        result.raise_to(
            "urgent", "A fever that has lasted 5 days or more needs a doctor.", "nhs_fever_child"
        )
    if not is_child(profile) and days in ("3_4", "5_plus"):
        result.raise_to(
            "soon",
            "A fever lasting 3 days or more should be checked by a doctor — in India, blood "
            "tests for dengue, malaria and typhoid are often needed.",
            "clinicalcontext",
        )
    if not is_child(profile) and temp == "40":
        result.raise_to(
            "soon",
            "A temperature of 40°C or more should be checked by a doctor.",
            "clinicalcontext",
        )
    if profile.pregnant:
        result.raise_to(
            "urgent", "A fever in pregnancy should be checked by a doctor today.", "clinicalcontext"
        )

    result.possible_causes = [
        "Most fevers are caused by viral infections and get better on their own in 3 to 5 days.",
        "In India, dengue, malaria and typhoid are common causes of fever — a blood test is the "
        "only way to tell them apart.",
        "Ear, throat, chest and urine infections can also cause a fever.",
    ]
    result.self_care = [
        "Drink plenty of fluids — water, ORS, coconut water, soups.",
        "Rest, and wear light clothing; don't over-wrap a child.",
        "Check on a child regularly, including during the night.",
        "Stay home and away from others until the fever has gone.",
    ]
    context = Context(fever=hot or temp == "not_measured", dengue_possible=True)
    result.medicines = [paracetamol(profile, context), ibuprofen(profile, context)]
    result.tests = [
        "Complete blood count with platelets",
        "Dengue NS1 antigen (first 5 days) or dengue IgM",
        "Malaria rapid test or blood smear",
        "Typhoid: blood culture (a doctor decides when)",
        "Urine test if there are urinary symptoms",
    ]
    result.doctor_may = [
        DoctorOption(
            "Antibiotics do not help viral fevers or dengue. A doctor prescribes them only when "
            "tests or examination show a bacterial infection such as typhoid or a urine infection.",
            "nice_ng120",
        )
    ]
    result.see_doctor_if = [
        "The fever lasts 3 days or more (5 days in a child), or keeps coming back.",
        "Any warning sign appears — bleeding, severe tummy pain, repeated vomiting, "
        "drowsiness, difficulty breathing, a rash that doesn't fade under a glass.",
        "Warning signs of dengue often start as the fever goes down — keep watching for 2 days "
        "after it settles.",
        "A child is not drinking or has far fewer wet nappies.",
    ]
    del warning  # every warning option carries its own urgency and reason


def _young_baby(profile: Profile) -> Reason | None:
    if profile.months < 3:
        return Reason(
            "Any baby under 3 months with a temperature of 38°C or more needs to be seen by a "
            "doctor straight away.",
            "emergency",
            "nice_ng143",
        )
    return None


FEVER = Protocol(
    id="fever",
    name="Fever",
    summary="High temperature, with or without chills, body ache or headache.",
    questions=(_FEVER_DANGER, _FEVER_TEMPERATURE, _FEVER_DAYS, _FEVER_WARNING, _RISK_FACTORS),
    assess=_assess_fever,
    source_keys=("nhs_fever_child", "nhs_fever_adult", "nice_ng143", "who_dengue"),
    precheck=_young_baby,
)

# -- cough, cold and sore throat ------------------------------------------------------

_RESP_DANGER = Question(
    id="resp_danger",
    text="Do any of these apply right now?",
    kind="multi",
    options=(
        Option(
            "breathing",
            "Struggling to breathe, or lips/face turning blue",
            "emergency",
            "Severe difficulty breathing needs emergency care.",
            "nhs_cough",
        ),
        Option(
            "saliva",
            "Can't swallow saliva, drooling, or noisy, high-pitched breathing",
            "emergency",
            "Drooling or noisy breathing with a sore throat can mean the airway is narrowing.",
            "nhs_sore_throat",
        ),
        Option(
            "blood",
            "Coughing up blood",
            "urgent",
            "Coughing up blood needs a doctor today.",
            "nhs_cough",
        ),
        Option(
            "chest_pain",
            "Chest pain",
            "urgent",
            "Chest pain with a cough needs a doctor today.",
            "nhs_cough",
        ),
        Option(
            "confused",
            "Confused, very drowsy or very unwell",
            "emergency",
            "Confusion or drowsiness with a chest infection can mean serious illness.",
            "nhs_sepsis",
        ),
    ),
)

_RESP_SYMPTOMS = Question(
    id="resp_symptoms",
    text="Which of these do you have?",
    kind="multi",
    options=(
        Option("cough", "Cough"),
        Option("sore_throat", "Sore throat"),
        Option("runny_nose", "Runny or blocked nose, sneezing"),
        Option("fever", "Fever"),
        Option("earache", "Earache"),
    ),
)

_COUGH_DAYS = Question(
    id="cough_days",
    text="How long have you had the cough?",
    kind="single",
    when=lambda _p, a: "cough" in chosen(a, "resp_symptoms"),
    options=(
        Option("under_1w", "Less than a week"),
        Option("1_2w", "1 to 2 weeks"),
        Option(
            "2_3w",
            "2 to 3 weeks",
            "soon",
            "A cough for 2 weeks or more should be tested for TB — free at government health "
            "centres in India.",
            "india_ntep",
        ),
        Option(
            "3w_plus",
            "More than 3 weeks",
            "soon",
            "A cough for more than 3 weeks needs a doctor (and a TB test in India).",
            "nhs_cough",
        ),
    ),
)

_THROAT_SCORE = Question(
    id="throat",
    text="About the sore throat — which of these are true?",
    kind="multi",
    help="These five points are the FeverPAIN score doctors use to decide on antibiotics.",
    when=lambda _p, a: "sore_throat" in chosen(a, "resp_symptoms"),
    options=(
        Option("fever24", "Fever in the last 24 hours"),
        Option("pus", "White spots or pus on the tonsils"),
        Option("recent", "It started within the last 3 days"),
        Option("inflamed", "Tonsils very red and swollen"),
        Option("no_cold", "No cough and no runny nose"),
    ),
)

_RESP_MORE = Question(
    id="resp_more",
    text="Does any of this apply?",
    kind="multi",
    options=(
        Option(
            "fast_breathing",
            "Breathing faster than usual",
            "urgent",
            "Fast breathing with a cough or fever can be pneumonia.",
            "nhs_cough",
        ),
        Option(
            "weight_loss",
            "Losing weight without trying, or night sweats",
            "soon",
            "Weight loss or night sweats with a cough need a doctor (including a TB test).",
            "india_ntep",
        ),
        Option(
            "one_sided",
            "Severe throat pain on one side, or can't open the mouth fully",
            "urgent",
            "Severe one-sided throat pain can be an abscess (quinsy).",
            "nhs_sore_throat",
        ),
        Option(
            "immunity",
            "Weak immune system, or long-term lung or heart disease",
            "soon",
            "With a weak immune system or long-term lung or heart disease, see a doctor early.",
            "nhs_cough",
        ),
    ),
)


def _assess_respiratory(profile: Profile, answers: Answers, result: Assessment) -> None:
    symptoms = chosen(answers, "resp_symptoms")
    throat = chosen(answers, "throat")
    if "sore_throat" in symptoms:
        score = len(throat)
        if score >= 4:
            result.raise_to(
                "soon",
                f"Your sore throat scores {score} of 5 on FeverPAIN — a bacterial (strep) "
                "infection is more likely, and a doctor may prescribe an antibiotic.",
                "nice_ng84",
            )
            result.doctor_may.append(
                DoctorOption(
                    "For a sore throat with a FeverPAIN score of 4 or 5, doctors may prescribe "
                    "phenoxymethylpenicillin (penicillin V) for 5 to 10 days, or clarithromycin "
                    "for people allergic to penicillin.",
                    "nice_ng84",
                )
            )
        elif score >= 2:
            result.see_doctor_if.append(
                f"Your sore throat scores {score} of 5 on FeverPAIN: see a doctor if it isn't "
                "getting better in 3 to 5 days."
            )
    if "fever" in symptoms and profile.months < 3:
        result.raise_to(
            "emergency",
            "Any baby under 3 months with a fever needs to be seen by a doctor straight away.",
            "nice_ng143",
        )
    if "earache" in symptoms and is_child(profile):
        result.see_doctor_if.append(
            "The earache lasts more than 3 days, or fluid or pus comes out of the ear."
        )
    result.possible_causes = [
        "Most coughs, colds and sore throats are caused by viruses and get better on their "
        "own — a cold in about a week, a cough in 3 to 4 weeks.",
        "Antibiotics don't help viral infections, and can cause side effects.",
    ]
    result.self_care = [
        "Rest and drink plenty of fluids.",
        "Warm drinks, and honey with lemon for a cough (not for babies under 1 year).",
        "Gargle with warm salty water for a sore throat (adults and older children).",
        "Saline nose drops can ease a blocked nose.",
        "Stay away from others while you have a fever.",
    ]
    context = Context(fever="fever" in symptoms, dengue_possible="fever" in symptoms)
    result.medicines = [paracetamol(profile, context), ibuprofen(profile, context)]
    if not result.doctor_may:
        result.doctor_may.append(
            DoctorOption(
                "Most coughs and sore throats do not need antibiotics; a doctor prescribes them "
                "only when a bacterial infection is likely.",
                "nice_ng120",
            )
        )
    result.see_doctor_if += [
        "You have a cough for 2 weeks or more (TB test), or cough up blood.",
        "Breathing becomes difficult or fast, or you get chest pain.",
        "You feel much worse, or not better after 3 weeks.",
    ]


RESPIRATORY = Protocol(
    id="cough_cold",
    name="Cough, cold or sore throat",
    summary="Cough, runny or blocked nose, sore throat, earache.",
    questions=(_RESP_DANGER, _RESP_SYMPTOMS, _COUGH_DAYS, _THROAT_SCORE, _RESP_MORE),
    assess=_assess_respiratory,
    source_keys=("nhs_cough", "nhs_cold", "nhs_sore_throat", "nice_ng84", "nice_ng120"),
)

# -- diarrhoea and vomiting -------------------------------------------------------------

_DV_DANGER = Question(
    id="dv_danger",
    text="Do any of these apply right now?",
    kind="multi",
    options=(
        Option(
            "blood_vomit",
            "Vomiting blood, or vomit that looks like ground coffee",
            "emergency",
            "Vomiting blood needs emergency care.",
            "nhs_d_and_v",
        ),
        Option(
            "green_vomit",
            "Green or yellow-green vomit",
            "emergency",
            "Green vomit can mean a blocked bowel.",
            "nhs_d_and_v",
        ),
        Option(
            "poison",
            "May have swallowed something poisonous",
            "emergency",
            "Possible poisoning needs emergency care.",
            "nhs_d_and_v",
        ),
        Option(
            "severe_pain",
            "A sudden, severe tummy ache or headache",
            "emergency",
            "A sudden severe tummy ache or headache needs emergency care.",
            "nhs_d_and_v",
        ),
        Option(
            "neck",
            "Stiff neck and pain when looking at bright lights",
            "emergency",
            "A stiff neck with pain from light can be meningitis.",
            "nhs_d_and_v",
        ),
        *_SEVERE_ILLNESS[:3],
    ),
)

_DV_WHICH = Question(
    id="dv_which",
    text="What is happening?",
    kind="single",
    options=(
        Option("diarrhoea", "Diarrhoea (loose motions)"),
        Option("vomiting", "Vomiting"),
        Option("both", "Both"),
    ),
)

_DV_DEHYDRATION = Question(
    id="dv_dehydration",
    text="Any signs of dehydration?",
    kind="multi",
    options=(
        Option("little_urine", "Passing little or no urine (fewer wet nappies)"),
        Option("dry", "Very dry mouth or no tears when crying"),
        Option("sunken", "Sunken eyes (or a sunken soft spot on a baby's head)"),
        Option("sleepy", "Unusually sleepy or irritable"),
    ),
)

_DV_MORE = Question(
    id="dv_more",
    text="Does any of this apply?",
    kind="multi",
    options=(
        Option(
            "blood_stool",
            "Blood in the diarrhoea, or bleeding from the bottom",
            "urgent",
            "Bloody diarrhoea needs a doctor today.",
            "nhs_d_and_v",
        ),
        Option(
            "cant_keep",
            "Keep vomiting and can't keep fluids down",
            "urgent",
            "Not being able to keep fluids down leads to dehydration.",
            "nhs_d_and_v",
        ),
        Option(
            "long_diarrhoea",
            "Diarrhoea for more than 7 days",
            "urgent",
            "Diarrhoea for more than 7 days needs a doctor.",
            "nhs_d_and_v",
        ),
        Option(
            "long_vomiting",
            "Vomiting for more than 2 days",
            "urgent",
            "Vomiting for more than 2 days needs a doctor.",
            "nhs_d_and_v",
        ),
        Option(
            "stopped_feeding",
            "Stopped breast or bottle feeding",
            "urgent",
            "A child who stops feeding while ill needs a doctor today.",
            "nhs_d_and_v",
            only=is_child,
        ),
        Option(
            "fever",
            "High temperature as well",
            "soon",
            "Diarrhoea with a fever can be a bacterial infection such as typhoid or dysentery.",
            "who_diarrhoea",
        ),
    ),
)


def _assess_dv(profile: Profile, answers: Answers, result: Assessment) -> None:
    which = next(iter(chosen(answers, "dv_which")), "diarrhoea")
    dehydration = chosen(answers, "dv_dehydration")
    more = chosen(answers, "dv_more")
    if _baby(profile):
        result.raise_to(
            "urgent",
            "Diarrhoea or vomiting in a baby under 12 months should be checked by a doctor.",
            "nhs_d_and_v",
        )
    if dehydration and is_under_five(profile):
        result.raise_to(
            "urgent",
            "A child under 5 with signs of dehydration needs a doctor today.",
            "nhs_d_and_v",
        )
    elif dehydration:
        result.see_doctor_if.insert(
            0, "Signs of dehydration don't improve after drinking ORS — see a doctor today."
        )
    result.possible_causes = [
        "Most diarrhoea and vomiting is a stomach infection (gastroenteritis), often from "
        "contaminated food or water. It usually stops within a few days: vomiting in 1 to 2 "
        "days, diarrhoea in 5 to 7.",
        "Blood in the stool or a high fever point to a bacterial infection that may need "
        "treatment.",
    ]
    result.self_care = [
        "Drink plenty — small, frequent sips if you feel sick.",
        "Keep breastfeeding, and eat normally as soon as you can.",
        "Wash hands with soap after the toilet and before food; stay home until 2 days after "
        "the last episode.",
        "Do not give medicine to stop diarrhoea to children under 12.",
    ]
    context = Context(
        fever="fever" in more, dehydrated=bool(dehydration), blood_in_stool="blood_stool" in more
    )
    result.medicines = [oral_rehydration(profile, context)]
    if which in ("diarrhoea", "both"):
        child_zinc = zinc(profile, context)
        if child_zinc is not None:
            result.medicines.append(child_zinc)
        result.medicines.append(loperamide(profile, context))
    result.medicines.append(paracetamol(profile, context))
    result.doctor_may = [
        DoctorOption(
            "Antibiotics are not needed for most diarrhoea. A doctor may prescribe one for "
            "bloody diarrhoea (dysentery), cholera or typhoid after examining you.",
            "who_diarrhoea_manual",
        )
    ]
    result.see_doctor_if += [
        "You can't keep fluids down, or there's blood in the stool.",
        "Diarrhoea lasts more than 7 days or vomiting more than 2 days.",
        "Signs of dehydration: little urine, very dry mouth, sunken eyes, drowsiness.",
    ]


DIARRHOEA_VOMITING = Protocol(
    id="diarrhoea_vomiting",
    name="Diarrhoea or vomiting",
    summary="Loose motions, being sick, stomach upset.",
    questions=(_DV_DANGER, _DV_WHICH, _DV_DEHYDRATION, _DV_MORE),
    assess=_assess_dv,
    source_keys=("nhs_d_and_v", "who_diarrhoea", "who_diarrhoea_manual"),
)

# -- headache ----------------------------------------------------------------------------

_HEAD_DANGER = Question(
    id="head_danger",
    text="Do any of these apply?",
    kind="multi",
    options=(
        Option(
            "sudden",
            "It started suddenly and is extremely painful (the worst ever)",
            "emergency",
            "A sudden, extremely painful headache can be bleeding in the brain.",
            "nhs_headaches",
        ),
        Option(
            "weakness",
            "Numbness, weakness, slurred speech or a drooping face",
            "emergency",
            "Weakness, numbness or slurred speech can be a stroke.",
            "nhs_headaches",
        ),
        Option(
            "seizure",
            "A fit (seizure)",
            "emergency",
            "A headache with a fit needs emergency care.",
            "nhs_headaches",
        ),
        Option(
            "head_injury",
            "A head injury in the last 3 months",
            "emergency",
            "A headache after a head injury needs emergency assessment.",
            "nhs_headaches",
        ),
        Option(
            "fever_neck",
            "Fever with a stiff neck or a rash that doesn't fade under a glass",
            "emergency",
            "Fever with a stiff neck or such a rash can be meningitis.",
            "nhs_meningitis",
        ),
        Option(
            "confused",
            "Confusion or very drowsy",
            "emergency",
            "Confusion with a headache needs emergency care.",
            "nhs_headaches",
        ),
    ),
)

_HEAD_URGENT = Question(
    id="head_urgent",
    text="And any of these?",
    kind="multi",
    options=(
        Option(
            "vision",
            "Problems with vision or the eyes",
            "urgent",
            "A headache with vision problems needs a doctor today.",
            "nhs_headaches",
        ),
        Option(
            "cough_worse",
            "Worse when coughing, sneezing, bending down or exercising",
            "urgent",
            "A headache made worse by coughing or bending needs a doctor today.",
            "nhs_headaches",
        ),
        Option(
            "vomiting",
            "Being sick (vomiting)",
            "urgent",
            "A headache with vomiting needs a doctor today.",
            "nhs_headaches",
        ),
        Option(
            "jaw",
            "Pain in the jaw when eating, or a tender scalp (age 50 or over)",
            "urgent",
            "Over 50, jaw pain when eating or a tender scalp can be giant cell arteritis, which "
            "can threaten eyesight.",
            "clinicalcontext",
            only=_over_50,
        ),
        Option(
            "frequent",
            "Headaches on most days, or painkillers on 15 or more days a month",
            "soon",
            "Frequent headaches, or painkillers on most days, should be reviewed by a doctor — "
            "painkillers themselves can cause daily headaches.",
            "nhs_headaches",
        ),
    ),
)


def _assess_headache(profile: Profile, answers: Answers, result: Assessment) -> None:
    result.possible_causes = [
        "Most headaches are tension headaches, migraine, or come with a cold, dehydration, "
        "missed meals, stress or eye strain.",
    ]
    result.self_care = [
        "Drink plenty of water and don't skip meals.",
        "Rest in a quiet, dark room; try to relax.",
        "Limit screens and alcohol; keep a regular sleep pattern.",
    ]
    context = Context()
    result.medicines = [paracetamol(profile, context), ibuprofen(profile, context)]
    result.see_doctor_if = [
        "Headaches keep coming back, or painkillers aren't helping.",
        "You need painkillers on 15 or more days a month.",
        "A new headache after age 50, or any warning sign above.",
    ]


HEADACHE = Protocol(
    id="headache",
    name="Headache",
    summary="Head pain, migraine.",
    questions=(_HEAD_DANGER, _HEAD_URGENT),
    assess=_assess_headache,
    source_keys=("nhs_headaches",),
)

# -- urinary symptoms --------------------------------------------------------------------

_URINE_URGENT = Question(
    id="urine_urgent",
    text="Do any of these apply?",
    kind="multi",
    options=(
        Option(
            "back_pain",
            "Pain in the back or side, just under the ribs",
            "urgent",
            "Back or side pain can mean the infection has reached the kidneys.",
            "nhs_uti",
        ),
        Option(
            "fever",
            "High temperature, feeling hot and cold, or shivering",
            "urgent",
            "A fever or shivering with a urine infection needs a doctor today.",
            "nhs_uti",
        ),
        Option(
            "blood",
            "Blood in the urine",
            "urgent",
            "Blood in the urine needs a doctor.",
            "nhs_uti",
        ),
        Option(
            "confused",
            "Confused, very drowsy, or not passed urine all day",
            "emergency",
            "Confusion or no urine all day can be sepsis.",
            "nhs_sepsis",
        ),
        Option(
            "recurrent",
            "Two or more urine infections in the last 6 months",
            "soon",
            "Repeated urine infections should be looked into by a doctor.",
            "nhs_uti",
        ),
        Option(
            "two_days",
            "Symptoms for more than 2 days despite fluids and paracetamol",
            "soon",
            "Symptoms that aren't improving after 2 days need a doctor.",
            "nhs_uti",
        ),
    ),
)


def _assess_urinary(profile: Profile, answers: Answers, result: Assessment) -> None:
    if profile.sex == "male" or profile.sex == "other":
        result.raise_to(
            "soon", "Urine infections in men should always be checked by a doctor.", "nhs_uti"
        )
    if profile.pregnant:
        result.raise_to("urgent", "A urine infection in pregnancy needs a doctor.", "nhs_uti")
    if profile.age_years < 16:
        result.raise_to("soon", "Children with urine symptoms should see a doctor.", "nhs_uti")
    if profile.age_years >= 65 or profile.has("diabetes"):
        result.raise_to(
            "soon",
            "Over 65 or with diabetes, a urine infection should be checked early.",
            "nhs_uti",
        )
    result.possible_causes = [
        "Burning or pain when passing urine, needing to go often and urgently, or cloudy urine "
        "are usually a bladder infection (UTI).",
        "In some people the same symptoms come from other causes, such as a sexually "
        "transmitted infection — a doctor can test.",
    ]
    result.self_care = [
        "Drink enough to pass pale urine regularly.",
        "Avoid alcohol and coffee while you have symptoms.",
        "Rest.",
    ]
    result.medicines = [paracetamol(profile, Context())]
    result.doctor_may = [
        DoctorOption(
            "Most UTIs are treated with a short antibiotic course from a doctor or pharmacist. "
            "For women who aren't pregnant, NICE recommends nitrofurantoin or trimethoprim for 3 "
            "days; men and pregnant women need longer courses chosen by a doctor.",
            "nice_ng109",
        )
    ]
    result.see_doctor_if = [
        "You get pain in your back or side, a fever or shivering.",
        "There is blood in the urine.",
        "Symptoms don't improve within 2 days, or come back after treatment.",
    ]


URINARY = Protocol(
    id="urinary",
    name="Burning or pain when passing urine",
    summary="Burning, passing urine often, lower tummy pain — possible urine infection.",
    questions=(_URINE_URGENT,),
    assess=_assess_urinary,
    source_keys=("nhs_uti", "nice_ng109"),
)

# -- rash, hives and allergy -------------------------------------------------------------

_RASH_DANGER = Question(
    id="rash_danger",
    text="Do any of these apply right now?",
    kind="multi",
    options=(
        Option(
            "swelling",
            "Swelling of the lips, mouth, tongue or throat",
            "emergency",
            "Swelling of the lips, tongue or throat can be anaphylaxis — use an adrenaline "
            "auto-injector if you have one.",
            "nhs_anaphylaxis",
        ),
        Option(
            "breathing",
            "Wheezing, breathing very fast or struggling to breathe",
            "emergency",
            "Difficulty breathing with a rash can be anaphylaxis.",
            "nhs_anaphylaxis",
        ),
        Option(
            "faint",
            "Feeling faint, dizzy, confused or collapsing",
            "emergency",
            "Feeling faint with a rash can be anaphylaxis.",
            "nhs_anaphylaxis",
        ),
        Option(
            "glass",
            "A fever with a rash that doesn't fade when a glass is pressed on it",
            "emergency",
            "A rash that doesn't fade under a glass with a fever can be meningitis or sepsis.",
            "nhs_meningitis",
        ),
    ),
)

_RASH_MORE = Question(
    id="rash_more",
    text="And any of these?",
    kind="multi",
    options=(
        Option(
            "fever",
            "A high temperature as well",
            "urgent",
            "Hives or a rash with a high temperature should be checked by a doctor.",
            "nhs_hives",
        ),
        Option(
            "blisters",
            "Blisters, peeling skin, or sores in the mouth or eyes",
            "urgent",
            "Blistering or peeling skin can be a severe reaction, often to a medicine.",
            "clinicalcontext",
        ),
        Option(
            "new_medicine",
            "Started a new medicine in the last few weeks",
            "soon",
            "A rash after starting a medicine can be a reaction — tell the doctor who prescribed "
            "it (don't stop a prescribed medicine on your own).",
            "clinicalcontext",
        ),
        Option(
            "two_days",
            "Not better after 2 days, spreading, or keeps coming back",
            "soon",
            "Hives that haven't improved in 2 days or keep coming back need a doctor.",
            "nhs_hives",
        ),
    ),
)


def _assess_rash(profile: Profile, answers: Answers, result: Assessment) -> None:
    if is_child(profile):
        result.see_doctor_if.append("You are worried about a child's rash — ask a doctor.")
    result.possible_causes = [
        "Itchy raised patches (hives) are often an allergic reaction — to a food, medicine, "
        "insect bite or plant — or come with a viral infection, and usually settle within days.",
        "Many rashes are eczema, fungal infections or insect bites; a doctor can tell them apart.",
    ]
    result.self_care = [
        "Avoid whatever seems to trigger it.",
        "Cool compresses or a cool shower ease itching; avoid scratching.",
        "Wear loose cotton clothing.",
    ]
    result.medicines = [cetirizine(profile, Context())]
    result.doctor_may = [
        DoctorOption(
            "For hives that don't settle, a doctor may prescribe a stronger antihistamine plan or "
            "a short course of steroid tablets.",
            "nhs_hives",
        )
    ]
    result.see_doctor_if += [
        "Swelling of the face, lips or tongue, or any breathing difficulty — emergency.",
        "The rash spreads, blisters, or comes with a fever.",
    ]


RASH = Protocol(
    id="rash_allergy",
    name="Itchy rash, hives or allergy",
    summary="Hives, itching, allergic reactions, rashes.",
    questions=(_RASH_DANGER, _RASH_MORE),
    assess=_assess_rash,
    source_keys=("nhs_hives", "nhs_anaphylaxis"),
)

# -- body, joint and back pain -------------------------------------------------------------

_PAIN_DANGER = Question(
    id="pain_danger",
    text="Do any of these apply?",
    kind="multi",
    options=(
        Option(
            "chest",
            "Chest pain",
            "emergency",
            "Chest pain can be a heart attack — get emergency help.",
            "nhs_back_pain",
        ),
        Option(
            "numb",
            "Numbness around the genitals or bottom, or trouble controlling urine or stools",
            "emergency",
            "Back pain with these signs can mean pressure on the nerves of the spine "
            "(cauda equina) — emergency.",
            "nhs_back_pain",
        ),
        Option(
            "legs",
            "Weakness, numbness or tingling in both legs",
            "emergency",
            "Weakness or numbness in both legs with back pain is an emergency.",
            "nhs_back_pain",
        ),
        Option(
            "accident",
            "It started after a serious accident or fall",
            "emergency",
            "Pain after a serious accident needs emergency assessment.",
            "nhs_back_pain",
        ),
    ),
)

_PAIN_MORE = Question(
    id="pain_more",
    text="And any of these?",
    kind="multi",
    options=(
        Option(
            "hot_joint",
            "A hot, red, swollen joint",
            "urgent",
            "A hot, swollen joint — especially with a fever — can be an infected joint.",
            "clinicalcontext",
        ),
        Option(
            "unwell",
            "Feeling hot, shivery or generally unwell with it",
            "urgent",
            "Pain with a fever or feeling unwell needs a doctor today.",
            "nhs_back_pain",
        ),
        Option(
            "sudden_severe",
            "Severe pain that started suddenly or is getting worse quickly",
            "urgent",
            "Severe pain that started suddenly or is quickly getting worse needs a doctor today.",
            "nhs_back_pain",
        ),
        Option(
            "calf",
            "A painful, swollen calf",
            "urgent",
            "A painful swollen calf can be a blood clot (DVT).",
            "clinicalcontext",
        ),
        Option(
            "weight",
            "Weight loss without trying, or pain worse at night",
            "soon",
            "Pain with weight loss or at night should be checked by a doctor.",
            "nhs_back_pain",
        ),
        Option(
            "weeks",
            "Not better after a few weeks, or stopping daily activities",
            "soon",
            "Pain that isn't improving after a few weeks should be seen by a doctor.",
            "nhs_back_pain",
        ),
    ),
)


def _assess_pain(profile: Profile, answers: Answers, result: Assessment) -> None:
    result.possible_causes = [
        "Most back, muscle and joint pain comes from a strain or overuse and gets better in a "
        "few weeks.",
        "Body aches with a fever are usually part of an infection (such as flu or dengue) — "
        "use the Fever check instead.",
    ]
    result.self_care = [
        "Stay active and keep doing daily activities as far as you can — long bed rest slows "
        "recovery.",
        "An ice pack wrapped in a towel for new pain and swelling; a heat pack for stiffness.",
        "Gentle stretches and exercises.",
    ]
    context = Context()
    result.medicines = [ibuprofen(profile, context), paracetamol(profile, context)]
    result.see_doctor_if = [
        "The pain isn't improving after a few weeks, or stops you doing daily activities.",
        "It's worse at night, or you lose weight without trying.",
        "Any warning sign above appears.",
    ]


PAIN = Protocol(
    id="body_pain",
    name="Body, joint or back pain",
    summary="Back pain, muscle aches, joint pain, sprains.",
    questions=(_PAIN_DANGER, _PAIN_MORE),
    assess=_assess_pain,
    source_keys=("nhs_back_pain",),
)

PROTOCOLS: dict[str, Protocol] = {
    p.id: p for p in (FEVER, RESPIRATORY, DIARRHOEA_VOMITING, HEADACHE, URINARY, RASH, PAIN)
}
