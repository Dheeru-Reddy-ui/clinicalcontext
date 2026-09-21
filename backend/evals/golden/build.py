"""Build the golden set from the corpus (evals/golden/set.jsonl).

Every label is derived from something a human expert wrote, never from the
retrieval pipeline under test:

* **Which documents are relevant** — PubMed's MeSH indexing (professional
  indexers assign the headings), stored per document at ingestion. A question
  about statins in cardiovascular disease is relevant to the documents indexed
  with both headings — every such document whose own title and conclusion
  address the question type (see ``_supports``), ordered by authority
  (evidence grade, then study design, then recency). recall@k is scored
  with the denominator ``min(|relevant|, k)``, so a topic with thirty
  relevant papers is not penalised for having thirty.
* **Which chunks** — the Conclusion(s) section of each relevant document's
  structured abstract: the passage that answers the question (the Abstract
  chunk when the abstract is unstructured).
* **The labelable universe** — only MeSH-indexed documents can be labelled
  (MEDLINE indexing lags publication by months), so retrieval is scored over
  that universe: the runner skips unindexed documents in the ranking.
* **The reference answer** — the authors' own conclusion from the top-ranked
  document, attributed (PMID, journal, year). Not a clinician-reviewed model
  answer; the feedback loop (``promote``) is how reviewed answers enter the set.
* **Expected evidence grade** — the top-ranked document's grade.
* **Contradiction expected** — whether the relevant conclusions split between
  recommending and recommending against (the same stance lexicon the
  contradiction detector uses, applied to the *gold* passages).

Questions instantiate the generic clinical question types of Ely et al.
(BMJ 1999; "What is the drug of choice for condition X?", "What are the
adverse effects of drug X?", "What is the prognosis of condition X?" …) on
the topics this corpus actually covers. A small set of questions on topics
the corpus does *not* cover is included with ``expected_abstain`` — the
right answer there is to say so.

    uv run python -m evals.golden.build            # rebuild set.jsonl
    uv run python -m evals.golden.build --dry-run  # print the summary only

The build is deterministic for a given corpus (``--seed`` orders the
category balancing). Promoted items (provenance.source == "feedback") are
preserved across rebuilds.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import random
import re
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import asyncpg

from app.config import get_settings
from app.graph.reasoner import _RECOMMEND_NEG, _RECOMMEND_POS
from evals.golden.schema import (
    SET_PATH,
    Category,
    GoldenItem,
    Grade,
    Provenance,
    ReferenceSource,
    load_set,
    write_set,
)

# -- how a clinician says it (MeSH heading -> phrase) ------------------------------------

DRUGS: dict[str, str] = {
    "anti-bacterial agents": "antibiotics",
    "antipsychotic agents": "antipsychotics",
    "hydroxymethylglutaryl-coa reductase inhibitors": "statins",
    "platelet aggregation inhibitors": "antiplatelet therapy",
    "antidepressive agents": "antidepressants",
    "anticoagulants": "anticoagulation",
    "hypoglycemic agents": "glucose-lowering drugs",
    "bone density conservation agents": "antiresorptive therapy",
    "ticagrelor": "ticagrelor",
    "antimanic agents": "mood stabilizers",
    "sodium-glucose transporter 2 inhibitors": "SGLT2 inhibitors",
    "insulin": "insulin",
    "clopidogrel": "clopidogrel",
    "purinergic p2y receptor antagonists": "P2Y12 inhibitors",
    "clozapine": "clozapine",
    "olanzapine": "olanzapine",
    "aripiprazole": "aripiprazole",
    "hypolipidemic agents": "lipid-lowering therapy",
    "denosumab": "denosumab",
    "prasugrel hydrochloride": "prasugrel",
    "fibrinolytic agents": "thrombolytic therapy",
    "selective serotonin reuptake inhibitors": "SSRIs",
    "antihypertensive agents": "antihypertensive drugs",
    "semaglutide": "semaglutide",
    "factor xa inhibitors": "factor Xa inhibitors",
    "pcsk9 inhibitors": "PCSK9 inhibitors",
    "valsartan": "valsartan",
    "atorvastatin": "atorvastatin",
    "quetiapine fumarate": "quetiapine",
    "risperidone": "risperidone",
    "angiotensin receptor antagonists": "ARBs",
    "teriparatide": "teriparatide",
    "adrenergic beta-antagonists": "beta blockers",
    "metformin": "metformin",
    "rosuvastatin calcium": "rosuvastatin",
    "dipeptidyl-peptidase iv inhibitors": "DPP-4 inhibitors",
    "glucocorticoids": "corticosteroids",
    "paliperidone palmitate": "paliperidone palmitate",
    "rivaroxaban": "rivaroxaban",
    "tirzepatide": "tirzepatide",
    "anti-arrhythmia agents": "antiarrhythmic drugs",
    "mineralocorticoid receptor antagonists": "mineralocorticoid receptor antagonists",
    "angiotensin-converting enzyme inhibitors": "ACE inhibitors",
    "dabigatran": "dabigatran",
    "pneumococcal vaccines": "pneumococcal vaccination",
    "amoxicillin": "amoxicillin",
    "heparin": "heparin",
    "proton pump inhibitors": "proton pump inhibitors",
    "vancomycin": "vancomycin",
    "fluoxetine": "fluoxetine",
    "aromatase inhibitors": "aromatase inhibitors",
    "immune checkpoint inhibitors": "immune checkpoint inhibitors",
    "anti-obesity agents": "anti-obesity medications",
    "analgesics, opioid": "opioid analgesics",
    "anti-anxiety agents": "anxiolytics",
}
# Single agents (not classes) — eligible for head-to-head comparison questions.
SINGLE_AGENTS = frozenset(
    {
        "ticagrelor",
        "clopidogrel",
        "clozapine",
        "olanzapine",
        "aripiprazole",
        "denosumab",
        "prasugrel hydrochloride",
        "semaglutide",
        "valsartan",
        "atorvastatin",
        "quetiapine fumarate",
        "risperidone",
        "teriparatide",
        "metformin",
        "rosuvastatin calcium",
        "paliperidone palmitate",
        "rivaroxaban",
        "tirzepatide",
        "dabigatran",
        "amoxicillin",
        "vancomycin",
        "fluoxetine",
        "insulin",
    }
)

CONDITIONS: dict[str, str] = {
    "thyroid nodule": "thyroid nodules",
    "diabetes mellitus, type 2": "type 2 diabetes",
    "bipolar disorder": "bipolar disorder",
    "cardiovascular diseases": "cardiovascular disease",
    "schizophrenia": "schizophrenia",
    "thyroid neoplasms": "thyroid cancer",
    "major depressive disorder": "major depressive disorder",
    "osteoporosis, postmenopausal": "postmenopausal osteoporosis",
    "atrial fibrillation": "atrial fibrillation",
    "acute coronary syndrome": "acute coronary syndrome",
    "heart failure": "heart failure",
    "depression": "depression",
    "sepsis": "sepsis",
    "community-acquired pneumonia": "community-acquired pneumonia",
    "anxiety": "anxiety",
    "urinary tract infections": "urinary tract infections",
    "stroke": "stroke",
    "diabetes mellitus, type 1": "type 1 diabetes",
    "hypertension": "hypertension",
    "sleep initiation and maintenance disorders": "insomnia",
    "coronary artery disease": "coronary artery disease",
    "myocardial infarction": "myocardial infarction",
    "osteoporosis": "osteoporosis",
    "ischemic stroke": "ischemic stroke",
    "pneumonia": "pneumonia",
    "bacteremia": "bacteremia",
    "psychotic disorders": "psychosis",
    "dyslipidemias": "dyslipidemia",
    "obesity": "obesity",
    "generalized anxiety disorder": "generalized anxiety disorder",
    "schizophrenia, treatment-resistant": "treatment-resistant schizophrenia",
    "hiv infections": "HIV infection",
    "gram-negative bacterial infections": "gram-negative bacterial infections",
    "depressive disorder, treatment-resistant": "treatment-resistant depression",
    "metabolic syndrome": "metabolic syndrome",
    "shock, septic": "septic shock",
    "thyroid cancer, papillary": "papillary thyroid cancer",
    "st elevation myocardial infarction": "STEMI",
    "venous thromboembolism": "venous thromboembolism",
    "kidney failure, chronic": "chronic kidney disease",
    "pulmonary disease, chronic obstructive": "COPD",
    "dementia": "dementia",
    "non-st elevated myocardial infarction": "NSTEMI",
    "hypothyroidism": "hypothyroidism",
    "hypercholesterolemia": "hypercholesterolemia",
    "hypertension, pulmonary": "pulmonary hypertension",
    "mania": "mania",
    "peripheral arterial disease": "peripheral arterial disease",
    "diabetes, gestational": "gestational diabetes",
    "pulmonary embolism": "pulmonary embolism",
    "non-alcoholic fatty liver disease": "non-alcoholic fatty liver disease",
    "chronic pain": "chronic pain",
    "attention deficit disorder with hyperactivity": "ADHD",
    "delirium": "delirium",
    "inflammatory bowel diseases": "inflammatory bowel disease",
    "heart failure, diastolic": "heart failure with preserved ejection fraction",
    "hashimoto disease": "Hashimoto's thyroiditis",
    "graves disease": "Graves' disease",
    "stress disorders, post-traumatic": "PTSD",
    "neonatal sepsis": "neonatal sepsis",
    "breast neoplasms": "breast cancer",
    "lung neoplasms": "lung cancer",
    "colorectal neoplasms": "colorectal cancer",
    "prostatic neoplasms": "prostate cancer",
    "anemia": "anemia",
    "surgical wound infection": "surgical site infection",
    "catheter-related infections": "catheter-related infections",
    "respiratory tract infections": "respiratory tract infections",
}

ADVERSE: dict[str, str] = {
    "hemorrhage": "bleeding",
    "hypoglycemia": "hypoglycemia",
    "weight gain": "weight gain",
    "acute kidney injury": "acute kidney injury",
    "metabolic syndrome": "metabolic syndrome",
    "suicidal ideation": "suicidal ideation",
    "gastrointestinal hemorrhage": "gastrointestinal bleeding",
    "intracranial hemorrhages": "intracranial hemorrhage",
    "hyperkalemia": "hyperkalemia",
    "cardiotoxicity": "cardiotoxicity",
    "neutropenia": "neutropenia",
    "tardive dyskinesia": "tardive dyskinesia",
    "fractures, bone": "fractures",
}

TESTS: dict[str, str] = {
    "ultrasonography": "ultrasound",
    "biopsy, fine-needle": "fine-needle aspiration biopsy",
    "electrocardiography": "ECG",
    "echocardiography": "echocardiography",
    "tomography, x-ray computed": "CT",
    "magnetic resonance imaging": "MRI",
    "natriuretic peptide, brain": "BNP",
    "procalcitonin": "procalcitonin",
    "c-reactive protein": "C-reactive protein",
    "glycated hemoglobin": "HbA1c",
}

PROGNOSIS_MARKERS: dict[str, str] = {
    "prognosis": "What is the prognosis of {cond}?",
    "survival rate": "What is the survival rate in {cond}?",
    "mortality": "What predicts mortality in {cond}?",
    "risk factors": "What are the risk factors for poor outcomes in {cond}?",
    "risk assessment": "How is risk stratified in {cond}?",
    "recurrence": "What predicts recurrence in {cond}?",
    "disease progression": "What predicts progression of {cond}?",
    "hospitalization": "What predicts hospitalization in {cond}?",
}

# Ely et al. (1999) generic question types, by category.
PATTERNS: dict[str, str] = {
    "therapy": "Is drug X indicated in situation Y?",
    "harm": "What are the adverse effects of drug X?",
    "first_line": "What is the drug of choice for condition X?",
    "guideline": "How should I manage condition X (guideline)?",
    "diagnosis": "Is test X indicated in situation Y?",
    "prognosis": "What is the prognosis of condition X?",
    "comparison": "Is drug X better than drug Y for condition Z?",
    "abstain": "Is the question answerable from this corpus?",
}

THERAPY_TEMPLATES = [
    "{Is} {drug} effective for {cond}?",
    "What is the evidence for {drug} in {cond}?",
    "{Does} {drug} improve outcomes in {cond}?",
    "Should {drug} be used in {cond}?",
]
HARM_TEMPLATES = [
    "{Does} {drug} increase the risk of {adverse}?",
    "How common is {adverse} with {drug}?",
    "What is the risk of {adverse} with {drug}?",
]
# Class names that take a plural verb ("Are statins…", "Do SSRIs…").
PLURAL = frozenset(
    {
        "antibiotics",
        "antipsychotics",
        "statins",
        "antidepressants",
        "glucose-lowering drugs",
        "mood stabilizers",
        "SGLT2 inhibitors",
        "P2Y12 inhibitors",
        "SSRIs",
        "antihypertensive drugs",
        "factor Xa inhibitors",
        "PCSK9 inhibitors",
        "ARBs",
        "beta blockers",
        "DPP-4 inhibitors",
        "corticosteroids",
        "antiarrhythmic drugs",
        "mineralocorticoid receptor antagonists",
        "ACE inhibitors",
        "proton pump inhibitors",
        "aromatase inhibitors",
        "immune checkpoint inhibitors",
        "anti-obesity medications",
        "opioid analgesics",
        "anxiolytics",
    }
)


def _agree(drug_name: str) -> dict[str, str]:
    plural = drug_name in PLURAL
    return {"Is": "Are" if plural else "Is", "Does": "Do" if plural else "Does"}


FIRST_LINE_TEMPLATES = [
    "What is the recommended first-line treatment for {cond}?",
    "How should {cond} be managed?",
    "What is the treatment of choice for {cond}?",
]
GUIDELINE_TEMPLATES = [
    "What do current guidelines recommend for {cond}?",
    "What are the guideline recommendations for managing {cond}?",
]
DIAGNOSIS_TEMPLATES = [
    "How accurate is {test} for diagnosing {cond}?",
    "What is the role of {test} in evaluating {cond}?",
]
COMPARISON_TEMPLATES = [
    "Is {a} or {b} more effective for {cond}?",
    "How does {a} compare with {b} in {cond}?",
]

# Real clinical questions on topics this corpus does not index. The builder
# verifies the absence (no document indexed with or titled by the key term)
# and drops any that the corpus has since come to cover.
ABSTAIN_QUESTIONS: list[tuple[str, tuple[str, ...]]] = [
    ("What is the first-line treatment for scabies?", ("scabies",)),
    ("Is ivermectin effective for COVID-19?", ("ivermectin",)),
    (
        "How should acute mountain sickness be prevented?",
        ("altitude sickness", "mountain sickness"),
    ),
    (
        "What is the recommended management of a snake bite?",
        ("snake bites", "snakebite", "snake bite"),
    ),
    ("Is methotrexate effective for psoriatic arthritis?", ("arthritis, psoriatic", "psoriatic")),
    ("What is the treatment of choice for trigeminal neuralgia?", ("trigeminal neuralgia",)),
    (
        "How is Wilson's disease diagnosed?",
        ("hepatolenticular degeneration", "wilson disease", "wilson's disease"),
    ),
    (
        "What is the prognosis of amyotrophic lateral sclerosis?",
        ("amyotrophic lateral sclerosis", "motor neuron"),
    ),
    (
        "Is botulinum toxin effective for chronic migraine?",
        ("botulinum toxins", "botulinum", "migraine"),
    ),
    ("What antibiotics are recommended for Lyme disease?", ("lyme disease", "lyme", "borrelia")),
    ("How should a scorpion sting be managed?", ("scorpion stings", "scorpion")),
    (
        "What is the treatment for cutaneous leishmaniasis?",
        ("leishmaniasis, cutaneous", "leishmania"),
    ),
    ("How should rabies post-exposure prophylaxis be given?", ("rabies",)),
    ("What is the treatment for scurvy?", ("scurvy", "ascorbic acid deficiency")),
    ("Is sildenafil effective for erectile dysfunction?", ("erectile dysfunction", "sildenafil")),
    (
        "What is the first-line treatment for head lice?",
        ("lice infestations", "pediculosis", "head lice"),
    ),
    ("How is acute angle-closure glaucoma managed?", ("glaucoma, angle-closure", "glaucoma")),
    ("What is the treatment for tinea pedis?", ("tinea pedis", "tinea")),
]

# How the entity is named in a title or conclusion (MeSH heading -> regex).
# The default is the heading's own words; classes need their members.
KEYWORDS: dict[str, str] = {
    "anti-bacterial agents": r"antibiotic|antimicrobial|antibacterial|amoxicillin|ceftriaxone|"
    r"vancomycin|piperacillin|carbapenem|fluoroquinolone|macrolide|beta-lactam|β-lactam",
    "antipsychotic agents": r"antipsychotic|clozapine|olanzapine|risperidone|aripiprazole|"
    r"quetiapine|paliperidone|haloperidol|lurasidone|cariprazine",
    "hydroxymethylglutaryl-coa reductase inhibitors": r"statin|atorvastatin|rosuvastatin|"
    r"simvastatin|pravastatin|pitavastatin",
    "platelet aggregation inhibitors": r"antiplatelet|p2y12|clopidogrel|ticagrelor|prasugrel|"
    r"aspirin|dapt|dual antiplatelet",
    "antidepressive agents": r"antidepress|ssri|snri|sertraline|fluoxetine|escitalopram|"
    r"venlafaxine|bupropion|esketamine|ketamine",
    "anticoagulants": r"anticoagul|warfarin|apixaban|rivaroxaban|dabigatran|edoxaban|doac|noac|"
    r"heparin",
    "hypoglycemic agents": r"glucose-lowering|hypoglyc|antidiabetic|metformin|sglt2|glp-1|"
    r"dpp-4|insulin|sulfonylurea|semaglutide|tirzepatide|empagliflozin|dapagliflozin",
    "bone density conservation agents": r"bisphosphonate|denosumab|teriparatide|romosozumab|"
    r"zoledron|alendronate|antiresorptive|anabolic|bone density",
    "antimanic agents": r"lithium|valproate|lamotrigine|mood stabili|antimanic",
    "sodium-glucose transporter 2 inhibitors": r"sglt2|sglt-2|gliflozin",
    "purinergic p2y receptor antagonists": r"p2y12|clopidogrel|ticagrelor|prasugrel",
    "hypolipidemic agents": r"lipid-lowering|statin|ezetimibe|pcsk9|hypolipid|cholesterol",
    "fibrinolytic agents": r"thrombolys|fibrinolys|alteplase|tenecteplase|tpa",
    "selective serotonin reuptake inhibitors": r"ssri|sertraline|fluoxetine|escitalopram|"
    r"citalopram|paroxetine|serotonin reuptake",
    "antihypertensive agents": r"antihypertensive|blood pressure|hypertens",
    "factor xa inhibitors": r"factor xa|apixaban|rivaroxaban|edoxaban|doac",
    "pcsk9 inhibitors": r"pcsk9|evolocumab|alirocumab|inclisiran",
    "angiotensin receptor antagonists": r"angiotensin receptor|arb\b|valsartan|losartan|"
    r"candesartan|sacubitril",
    "adrenergic beta-antagonists": r"beta[- ]?block|bisoprolol|metoprolol|carvedilol|"
    r"propranolol|β-block",
    "dipeptidyl-peptidase iv inhibitors": r"dpp-?4|gliptin",
    "glucocorticoids": r"corticosteroid|glucocorticoid|prednis|dexamethasone|hydrocortisone|"
    r"methylprednisolone",
    "anti-arrhythmia agents": r"antiarrhythm|amiodarone|rhythm control|flecainide|sotalol",
    "mineralocorticoid receptor antagonists": r"mineralocorticoid|spironolactone|eplerenone|"
    r"finerenone",
    "angiotensin-converting enzyme inhibitors": r"ace inhibitor|acei|ramipril|enalapril|"
    r"lisinopril|angiotensin-converting",
    "pneumococcal vaccines": r"pneumococcal|vaccin",
    "proton pump inhibitors": r"proton pump|ppi",
    "aromatase inhibitors": r"aromatase|letrozole|anastrozole|exemestane",
    "immune checkpoint inhibitors": r"checkpoint|pd-1|pd-l1|pembrolizumab|nivolumab|"
    r"immunotherapy",
    "anti-obesity agents": r"anti-obesity|weight[- ]loss|semaglutide|tirzepatide|liraglutide",
    "analgesics, opioid": r"opioid|morphine|oxycodone|fentanyl",
    "anti-anxiety agents": r"anxiolytic|benzodiazepine|buspirone",
    "prasugrel hydrochloride": r"prasugrel",
    "quetiapine fumarate": r"quetiapine",
    "rosuvastatin calcium": r"rosuvastatin",
    "paliperidone palmitate": r"paliperidone",
    "thyroid nodule": r"thyroid nodule|nodul",
    "diabetes mellitus, type 2": r"type 2 diabetes|t2d|type ii diabetes",
    "diabetes mellitus, type 1": r"type 1 diabetes|t1d|type i diabetes",
    "cardiovascular diseases": r"cardiovascular|cardiac|coronary|heart",
    "thyroid neoplasms": r"thyroid (?:cancer|carcinoma|neoplasm|malignan)",
    "major depressive disorder": r"depress",
    "osteoporosis, postmenopausal": r"osteopor|postmenopausal",
    "acute coronary syndrome": r"acute coronary|acs\b|myocardial infarction|stemi",
    "community-acquired pneumonia": r"community-acquired|pneumonia|cap\b",
    "urinary tract infections": r"urinary tract|uti\b|cystitis|pyelonephritis",
    "sleep initiation and maintenance disorders": r"insomnia|sleep",
    "psychotic disorders": r"psychos|psychotic",
    "dyslipidemias": r"dyslipid|lipid|cholesterol",
    "schizophrenia, treatment-resistant": r"treatment-resistant|resistant schizophrenia|clozapine",
    "hiv infections": r"hiv",
    "gram-negative bacterial infections": r"gram-negative|enterobacter|klebsiella|pseudomonas|"
    r"escherichia|e\. coli|carbapenem",
    "depressive disorder, treatment-resistant": r"treatment-resistant|resistant depression",
    "shock, septic": r"septic shock|sepsis",
    "thyroid cancer, papillary": r"papillary",
    "st elevation myocardial infarction": r"stemi|st-elevation|st elevation",
    "venous thromboembolism": r"thromboembol|vte\b|deep vein|pulmonary embolism",
    "kidney failure, chronic": r"chronic kidney|ckd|renal|kidney",
    "pulmonary disease, chronic obstructive": r"copd|obstructive pulmonary",
    "non-st elevated myocardial infarction": r"nstemi|non-st|non st",
    "hypertension, pulmonary": r"pulmonary hypertension|pulmonary arterial",
    "diabetes, gestational": r"gestational",
    "attention deficit disorder with hyperactivity": r"adhd|attention[- ]deficit",
    "inflammatory bowel diseases": r"inflammatory bowel|crohn|ulcerative colitis|ibd",
    "heart failure, diastolic": r"preserved ejection|hfpef|diastolic",
    "hashimoto disease": r"hashimoto|thyroiditis",
    "graves disease": r"graves",
    "stress disorders, post-traumatic": r"ptsd|post-?traumatic",
    "breast neoplasms": r"breast",
    "lung neoplasms": r"lung",
    "colorectal neoplasms": r"colorectal|colon|rectal",
    "prostatic neoplasms": r"prostat",
    "surgical wound infection": r"surgical site|wound infection",
    "hemorrhage": r"bleed|hemorrhag|haemorrhag",
    "gastrointestinal hemorrhage": r"gastrointestinal bleed|gi bleed|upper gastrointestinal|ugib",
    "intracranial hemorrhages": r"intracranial|intracerebral",
    "fractures, bone": r"fracture",
    "ultrasonography": r"ultraso|sonograph|us-|tirads",
    "biopsy, fine-needle": r"fine-needle|fna|aspiration|cytolog|bethesda",
    "biomarkers": r"biomarker|marker",
    "electrocardiography": r"electrocardiogra|ecg|ekg",
    "echocardiography": r"echocardiogra",
    "tomography, x-ray computed": r"computed tomography|\bct\b",
    "magnetic resonance imaging": r"magnetic resonance|\bmri\b",
    "natriuretic peptide, brain": r"natriuretic|bnp",
    "c-reactive protein": r"c-reactive|crp",
    "glycated hemoglobin": r"hba1c|glycated|glycosylated",
    "risk factors": r"risk",
    "risk assessment": r"risk",
    "disease progression": r"progress",
}

# The question type's own vocabulary, which the reference document's title
# must carry: a paper that merely co-indexes two headings is not an answer.
TOPIC_WORDS: dict[str, str] = {
    "therapy": r"efficac|effective|outcome|treatment|therap|trial|versus|\bvs\b|compar|"
    r"benefit|reduc|improv|response|remission|prevent|use of|effect",
    "harm": r"safety|adverse|risk|bleed|hemorrhag|haemorrhag|toxic|side[- ]effect|"
    r"complication|harm|event",
    "first_line": r"treatment|therap|management|guideline|recommend|first[- ]line|regimen|"
    r"efficac|effective|compar|versus|network meta",
    "guideline": r"guideline|recommendation|consensus|position statement|clinical practice|"
    r"standards of care|expert",
    "diagnosis": r"diagnos|accura|sensitiv|specific|detect|predict|screen|evaluat|assess|"
    r"marker|imaging|ultraso|biopsy|classif|discriminat|stratif",
    "prognosis": r"prognos|predict|risk factor|outcome|mortality|survival|recurren|"
    r"progression|hospitali|readmission|risk of|determinant|associated with",
    "comparison": r"versus|\bvs\b|compar|head-to-head|non-?inferior|superior|network",
}


def _entity(term: str) -> re.Pattern[str]:
    pattern = KEYWORDS.get(term)
    if pattern is None:
        first = re.split(r"[ ,]", term)[0]
        pattern = re.escape(first)
    return re.compile(pattern, re.I)


# Question types whose vocabulary must be in the *title* (the paper is about
# treating or recommending); the others may carry it in the conclusion.
_TITLE_GATED = frozenset({"therapy", "first_line", "guideline"})
# Papers *about* guidelines or practice (adherence audits, surveys, model
# evaluations) are not the recommendation itself.
_EXCLUDE_WORDS: dict[str, str] = {
    "guideline": r"adherence|concordance|quality of|evaluat|chatgpt|language model|survey|audit|"
    r"disparit|implementation|awareness|knowledge|attitude|appropriateness",
    "first_line": r"adherence|disparit|survey|awareness|knowledge|attitude|chatgpt|language model",
}


def _supports(doc: Doc, category: str, entities: list[str]) -> bool:
    """Does this document, by its own title and conclusion, answer this kind
    of question about these entities?"""
    title = doc.title.lower()
    text = f"{title} {doc.conclusion[:400].lower()}"
    if not all(_entity(term).search(text) for term in entities):
        return False
    excluded = _EXCLUDE_WORDS.get(category)
    if excluded is not None and re.search(excluded, title, re.I):
        return False
    words = TOPIC_WORDS.get(category)
    if words is None:
        return True
    scope = title if category in _TITLE_GATED else text
    return re.search(words, scope, re.I) is not None


CAPS: dict[str, int] = {
    "therapy": 60,
    "harm": 16,
    "first_line": 20,
    "guideline": 12,
    "diagnosis": 18,
    "prognosis": 22,
    "comparison": 16,
    "abstain": 12,
}
MIN_DOCS = 2
MAX_RELEVANT = 25  # bounds the payload; few topics have more
MIN_REFERENCE_CHARS = 120

_GRADE_RANK = {"A": 4, "B": 3, "C": 2, "D": 1}
_TYPE_RANK = {
    "clinical_guideline": 5,
    "meta_analysis": 5,
    "systematic_review": 4,
    "randomized_controlled_trial": 3,
    "cohort_study": 2,
    "case_control_study": 2,
    "narrative_review": 1,
    "other": 1,
    "case_report": 0,
}
_AUTHORITATIVE = frozenset({"clinical_guideline", "meta_analysis", "systematic_review"})


@dataclass(slots=True)
class Doc:
    id: str
    title: str
    journal: str | None
    year: int | None
    pmid: str | None
    study_type: str | None
    grade: str | None
    mesh: frozenset[str]
    conclusion_ids: list[str]  # the answering chunk(s): Conclusion(s), else Abstract
    conclusion: str
    has_conclusion: bool = True

    @property
    def rank_key(self) -> tuple[int, int, int, int]:
        return (
            _GRADE_RANK.get(self.grade or "", 0),
            _TYPE_RANK.get(self.study_type or "", 0),
            1 if self.has_conclusion else 0,
            min(max((self.year or 2015) - 2015, 0), 10),
        )


@dataclass(slots=True)
class Corpus:
    docs: list[Doc]
    by_term: dict[str, list[Doc]] = field(default_factory=dict)
    signature: str = ""
    title_index: str = ""

    def with_terms(self, *terms: str) -> list[Doc]:
        pools = [self.by_term.get(t, []) for t in terms]
        if not pools or not all(pools):
            return []
        ids = set.intersection(*({d.id for d in p} for p in pools))
        hits = [d for d in pools[0] if d.id in ids]
        return sorted(hits, key=lambda d: d.rank_key, reverse=True)

    def covers(self, term: str) -> bool:
        if term in self.by_term:
            return True
        return re.search(r"\b" + re.escape(term), self.title_index) is not None


async def load_corpus(dsn: str) -> Corpus:
    pool = await asyncpg.create_pool(dsn, min_size=1, max_size=2)
    try:
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """
                WITH answering AS (
                  SELECT ch.document_id,
                         array_agg(ch.id::text ORDER BY ch.chunk_index) AS ids,
                         string_agg(ch.content, ' ' ORDER BY ch.chunk_index) AS text,
                         bool_or(ch.section IN ('Conclusion', 'Conclusions')) AS has_conclusion
                  FROM public.chunks ch
                  WHERE ch.strategy = 'structural'
                    AND ch.section IN ('Conclusion', 'Conclusions', 'Abstract')
                    AND (
                      ch.section <> 'Abstract'
                      OR NOT EXISTS (
                        SELECT 1 FROM public.chunks c2
                        WHERE c2.document_id = ch.document_id AND c2.strategy = 'structural'
                          AND c2.section IN ('Conclusion', 'Conclusions')
                      )
                    )
                  GROUP BY ch.document_id
                )
                SELECT d.id, d.title, d.journal, d.pmid, d.study_type, d.evidence_grade,
                       extract(year FROM d.publication_date)::int AS year,
                       d.metadata->'mesh_terms' AS mesh,
                       a.ids AS conclusion_ids, a.text AS conclusion, a.has_conclusion
                FROM public.documents d
                JOIN answering a ON a.document_id = d.id
                WHERE d.org_id IS NULL
                  AND jsonb_array_length(coalesce(d.metadata->'mesh_terms', '[]'::jsonb)) > 0
                """
            )
            signature = await conn.fetchval(
                "SELECT count(*)::text || ':' || coalesce(max(ingested_at)::text, '') "
                "FROM public.documents WHERE org_id IS NULL"
            )
            titles = await conn.fetch("SELECT lower(title) AS t FROM public.documents")
    finally:
        await pool.close()
    docs: list[Doc] = []
    for r in rows:
        mesh = frozenset(str(t).lower() for t in json.loads(r["mesh"]))
        docs.append(
            Doc(
                id=str(r["id"]),
                title=str(r["title"]),
                journal=r["journal"],
                year=r["year"],
                pmid=r["pmid"],
                study_type=r["study_type"],
                grade=r["evidence_grade"],
                mesh=mesh,
                conclusion_ids=[str(c) for c in r["conclusion_ids"]],
                conclusion=re.sub(r"\s+", " ", str(r["conclusion"])).strip(),
                has_conclusion=bool(r["has_conclusion"]),
            )
        )
    corpus = Corpus(docs, signature=str(signature), title_index="\n".join(t["t"] for t in titles))
    for doc in docs:
        for term in doc.mesh:
            corpus.by_term.setdefault(term, []).append(doc)
    return corpus


# -- labelling -----------------------------------------------------------------------


def _stance_split(docs: list[Doc]) -> bool:
    positive = negative = 0
    for doc in docs:
        text = doc.conclusion
        if _RECOMMEND_NEG.search(text):
            negative += 1
        elif _RECOMMEND_POS.search(text):
            positive += 1
    return positive > 0 and negative > 0


def _relevant(docs: list[Doc]) -> list[Doc]:
    """The relevant pool, most authoritative first. The reference document
    (first) must carry a real conclusion of usable length; the rest may
    answer from an unstructured abstract."""
    reference = next(
        (d for d in docs if d.has_conclusion and len(d.conclusion) >= MIN_REFERENCE_CHARS), None
    )
    if reference is None:
        return []
    rest = [d for d in docs if d.id != reference.id]
    return [reference, *rest][:MAX_RELEVANT]


def _item(
    *,
    ident: str,
    question: str,
    category: Category,
    docs: list[Doc],
    mesh: list[str],
    corpus: Corpus,
    built_at: str,
    pico: dict[str, str] | None = None,
    entities: list[str] | None = None,
) -> GoldenItem | None:
    named = mesh if entities is None else entities
    relevant = _relevant([d for d in docs if _supports(d, category, named)])
    if len(relevant) < MIN_DOCS:
        return None
    top = relevant[0]
    grade: Grade | None = top.grade if top.grade in _GRADE_RANK else None  # type: ignore[assignment]
    return GoldenItem(
        id=ident,
        question=question,
        category=category,
        pattern=PATTERNS[category],
        reference_answer=top.conclusion,
        reference_source=ReferenceSource(
            document_id=top.id,
            chunk_id=top.conclusion_ids[0],
            pmid=top.pmid,
            title=top.title,
            journal=top.journal,
            year=top.year,
            study_type=top.study_type,
            evidence_grade=grade,
        ),
        relevant_document_ids=[d.id for d in relevant],
        relevant_chunk_ids=[c for d in relevant for c in d.conclusion_ids],
        expected_grade=grade,
        contradiction_expected=_stance_split(relevant),
        expected_abstain=False,
        pico=pico,
        provenance=Provenance(
            source="corpus_derived",
            built_at=built_at,
            corpus_signature=corpus.signature,
            mesh_terms=mesh,
        ),
    )


def _pick(templates: list[str], index: int) -> str:
    return templates[index % len(templates)]


def build_candidates(corpus: Corpus, built_at: str) -> dict[str, list[GoldenItem]]:
    out: dict[str, list[GoldenItem]] = defaultdict(list)
    counter = Counter[str]()

    def ident(category: str) -> str:
        counter[category] += 1
        return f"{category}-{counter[category]:03d}"

    # therapy: drug x condition
    for drug, drug_name in DRUGS.items():
        for cond, cond_name in CONDITIONS.items():
            if drug == cond or cond in ADVERSE:
                continue
            docs = corpus.with_terms(drug, cond)
            if len(docs) < MIN_DOCS or not any(d.grade in ("A", "B") for d in docs):
                continue
            n = len(out["therapy"])
            item = _item(
                ident=ident("therapy"),
                question=_pick(THERAPY_TEMPLATES, n).format(
                    drug=drug_name, cond=cond_name, **_agree(drug_name)
                ),
                category="therapy",
                docs=docs,
                mesh=[drug, cond],
                corpus=corpus,
                built_at=built_at,
            )
            if item:
                out["therapy"].append(item)

    # harm: drug x adverse outcome
    for drug, drug_name in DRUGS.items():
        for adverse, adverse_name in ADVERSE.items():
            docs = corpus.with_terms(drug, adverse)
            if len(docs) < MIN_DOCS:
                continue
            n = len(out["harm"])
            item = _item(
                ident=ident("harm"),
                question=_pick(HARM_TEMPLATES, n).format(
                    drug=drug_name, adverse=adverse_name, **_agree(drug_name)
                ),
                category="harm",
                docs=docs,
                mesh=[drug, adverse],
                corpus=corpus,
                built_at=built_at,
            )
            if item:
                out["harm"].append(item)

    # first-line and guideline: condition alone, authoritative designs only
    for cond, cond_name in CONDITIONS.items():
        docs = [d for d in corpus.with_terms(cond) if d.study_type in _AUTHORITATIVE]
        if len(docs) >= MIN_DOCS:
            n = len(out["first_line"])
            item = _item(
                ident=ident("first_line"),
                question=_pick(FIRST_LINE_TEMPLATES, n).format(cond=cond_name),
                category="first_line",
                docs=docs,
                mesh=[cond],
                corpus=corpus,
                built_at=built_at,
            )
            if item:
                out["first_line"].append(item)
        guidelines = [d for d in corpus.with_terms(cond) if d.study_type == "clinical_guideline"]
        if len(guidelines) >= MIN_DOCS:
            n = len(out["guideline"])
            item = _item(
                ident=ident("guideline"),
                question=_pick(GUIDELINE_TEMPLATES, n).format(cond=cond_name),
                category="guideline",
                docs=guidelines,
                mesh=[cond],
                corpus=corpus,
                built_at=built_at,
            )
            if item:
                out["guideline"].append(item)

    # diagnosis: test x condition
    for test, test_name in TESTS.items():
        for cond, cond_name in CONDITIONS.items():
            docs = corpus.with_terms(test, cond)
            if len(docs) < MIN_DOCS:
                continue
            n = len(out["diagnosis"])
            item = _item(
                ident=ident("diagnosis"),
                question=_pick(DIAGNOSIS_TEMPLATES, n).format(test=test_name, cond=cond_name),
                category="diagnosis",
                docs=docs,
                mesh=[test, cond],
                corpus=corpus,
                built_at=built_at,
            )
            if item:
                out["diagnosis"].append(item)

    # prognosis: condition x prognostic marker
    for marker, template in PROGNOSIS_MARKERS.items():
        for cond, cond_name in CONDITIONS.items():
            docs = [d for d in corpus.with_terms(cond, marker) if d.grade]
            if len(docs) < MIN_DOCS:
                continue
            item = _item(
                ident=ident("prognosis"),
                question=template.format(cond=cond_name),
                category="prognosis",
                docs=docs,
                mesh=[cond, marker],
                corpus=corpus,
                built_at=built_at,
                entities=[cond],
            )
            if item:
                out["prognosis"].append(item)

    # comparison: two single agents that share documents for a condition
    singles = [d for d in DRUGS if d in SINGLE_AGENTS]
    for i, a in enumerate(singles):
        for b in singles[i + 1 :]:
            for cond, cond_name in CONDITIONS.items():
                both = corpus.with_terms(a, b, cond)
                if len(both) < MIN_DOCS:
                    continue
                n = len(out["comparison"])
                a_name, b_name = DRUGS[a], DRUGS[b]
                item = _item(
                    ident=ident("comparison"),
                    question=_pick(COMPARISON_TEMPLATES, n).format(
                        a=a_name, b=b_name, cond=cond_name
                    ),
                    category="comparison",
                    docs=both,
                    mesh=[a, b, cond],
                    corpus=corpus,
                    built_at=built_at,
                    pico={
                        "population": cond_name,
                        "intervention": a_name,
                        "comparison": b_name,
                        "outcome": "",
                    },
                )
                if item:
                    out["comparison"].append(item)

    # abstain: the corpus must not cover the topic
    for question, terms in ABSTAIN_QUESTIONS:
        covered = [t for t in terms if corpus.covers(t)]
        if covered:
            print(f"  skipping abstain item (corpus covers {covered[0]!r}): {question}")
            continue
        out["abstain"].append(
            GoldenItem(
                id=ident("abstain"),
                question=question,
                category="abstain",
                pattern=PATTERNS["abstain"],
                reference_answer=(
                    "The corpus does not cover this topic; the correct response is to "
                    "abstain and say so rather than answer from unrelated evidence."
                ),
                expected_abstain=True,
                provenance=Provenance(
                    source="corpus_derived",
                    built_at=built_at,
                    corpus_signature=corpus.signature,
                    mesh_terms=list(terms),
                ),
            )
        )
    return out


def balance(candidates: dict[str, list[GoldenItem]], *, seed: int) -> list[GoldenItem]:
    """Cap each category, spreading the cut across topics rather than taking
    the alphabetical head (a deterministic shuffle), and dedupe questions."""
    rng = random.Random(seed)
    chosen: list[GoldenItem] = []
    seen_questions: set[str] = set()
    for category, cap in CAPS.items():
        pool = list(candidates.get(category, []))
        rng.shuffle(pool)
        kept = 0
        for item in pool:
            key = item.question.lower()
            if key in seen_questions or kept >= cap:
                continue
            seen_questions.add(key)
            chosen.append(item)
            kept += 1
    # Stable ids after balancing: re-number within category in question order.
    counts = Counter[str]()
    renumbered: list[GoldenItem] = []
    for item in sorted(chosen, key=lambda i: (i.category, i.question)):
        counts[item.category] += 1
        new_id = f"{item.category}-{counts[item.category]:03d}"
        renumbered.append(item.model_copy(update={"id": new_id}))
    return renumbered


def summarize(items: list[GoldenItem]) -> str:
    by_cat = Counter(i.category for i in items)
    grades = Counter(i.expected_grade or "none" for i in items if not i.expected_abstain)
    contradictions = sum(1 for i in items if i.contradiction_expected)
    pico = sum(1 for i in items if i.pico)
    relevant = [len(i.relevant_chunk_ids) for i in items if not i.expected_abstain]
    lines = [
        f"golden set: {len(items)} items",
        "  by category: " + ", ".join(f"{k}={v}" for k, v in sorted(by_cat.items())),
        "  expected grade: " + ", ".join(f"{k}={v}" for k, v in sorted(grades.items())),
        f"  contradiction expected: {contradictions}   pico-eligible: {pico}",
        f"  relevant chunks per item: min={min(relevant)} max={max(relevant)} "
        f"mean={sum(relevant) / len(relevant):.1f}",
    ]
    return "\n".join(lines)


async def build(*, seed: int, out: Path, dry_run: bool) -> list[GoldenItem]:
    settings = get_settings()
    corpus = await load_corpus(settings.database_url)
    built_at = datetime.now(UTC).isoformat()
    print(f"corpus: {len(corpus.docs)} labelled documents with a conclusion ({corpus.signature})")
    candidates = build_candidates(corpus, built_at)
    print("  candidates: " + ", ".join(f"{k}={len(v)}" for k, v in sorted(candidates.items())))
    items = balance(candidates, seed=seed)
    existing = load_set(out) if out.exists() else []
    promoted = [i for i in existing if i.provenance.source == "feedback"]
    items.extend(promoted)
    print(summarize(items))
    if promoted:
        print(f"  kept {len(promoted)} promoted item(s) from reviewed feedback")
    if not dry_run:
        write_set(items, out)
        print(f"wrote {out}")
    return items


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--seed", type=int, default=12)
    parser.add_argument("--out", type=Path, default=SET_PATH)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    items = asyncio.run(build(seed=args.seed, out=args.out, dry_run=args.dry_run))
    return 0 if len(items) >= 150 else 1


if __name__ == "__main__":
    sys.exit(main())


__all__: list[Any] = ["balance", "build", "build_candidates", "load_corpus", "summarize"]
