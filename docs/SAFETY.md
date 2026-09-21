# Safety architecture

ClinicalContext answers questions about the **medical literature**. It does not
diagnose patients, does not accept patient data, and does not give
individualized treatment or dosing advice. Those boundaries are enforced by the
guardrail layer described here — in code, with an adversarial test suite — not
by a disclaimer.

This document describes every guardrail, how it can fail, and, honestly, what
it does **not** protect against. Knowing the gaps is part of using the tool
safely.

## Design principle: deterministic backbone, LLM enhancement

Each guardrail has a **deterministic layer** (regex / pattern rules) that
requires no model and no network, and — where useful — an **LLM enhancement
layer** on top. The deterministic layer is authoritative for every blocking
decision. This matters for three reasons:

1. **Prompt injection can't disable it.** A pattern rule isn't susceptible to
   "ignore previous instructions"; an LLM classifier, in principle, is.
2. **It runs before any model sees the text.** PHI is blocked locally, so a
   patient identifier never leaves the process to a third-party LLM API.
3. **It is testable and reproducible.** The adversarial gate (below) passes at
   100% on the non-negotiable categories with the LLM turned off.

The adversarial suite (`backend/evals/adversarial/`, ≥120 cases) gates CI:
PHI-block and diagnosis-refusal must be **100%**; every other category **≥95%**.

---

## 1. PHI detection (`app/guardrails/phi.py`)

**What it does.** Scans every inbound query for protected health information and
**blocks** on any hit (it never silently redacts and proceeds). Layers:

- **Deterministic regex (authoritative):** US SSN, phone, email, medical record
  / chart numbers, dates of birth (labeled and bare `MM/DD/YYYY`), street
  addresses, and high-precision name cues (`Mr./Mrs./Dr. <Name>`,
  `patient named <Name>`, `patient, <Name>`).
- **Base64 decode-and-rescan:** base64-looking tokens are decoded and re-scanned,
  so PHI smuggled as base64 is caught.
- **Presidio NER (telemetry only):** PERSON/LOCATION detection via spaCy. It is
  **recorded but never blocks on its own**, because on clinical text it
  false-positives on drug names, eponyms, and author citations ("Smith et al").

**Findings are PHI-safe:** a verdict records entity *types and counts* only,
never the matched values, so it can be logged and audited without re-introducing
the PHI that was blocked.

**Failure modes / gaps.**
- Bare names with no cue word and no structured identifier (e.g. "Margaret
  Thompson has AF") are only caught by Presidio, which is advisory — so a bare
  name can pass the block. Structured identifiers (DOB/MRN/SSN/phone/email) are
  caught deterministically and reliably.
- Obfuscations beyond base64 (ROT13, leetspeak, image-encoded text) are not
  decoded.
- Non-US identifier formats (NHS numbers, non-US phone/postal formats) have
  partial coverage.
- It is a **PHI minimizer, not a HIPAA compliance boundary.** The correct
  behavior is simply: do not enter patient data.

## 2. Scope / refusal (`app/guardrails/scope.py`)

**What it does.** Refuses out-of-scope framings and redirects:
- `diagnosis` — "what does my patient have?" → refuses; the tool answers about
  the literature, not individuals.
- `dosing` — "how much should I give my patient?" → refuses individualized
  dosing (but *allows* "what dose do the guidelines recommend?").
- `personal_medical` — a patient asking about their own care ("should I stop my
  medication?") → refuses and redirects to their clinician / emergency services.
- `prompt_injection` — "ignore previous instructions", "disable your
  guardrails" → refuses.

A deterministic keyword/pattern layer catches the clear cases (and carries the
100% diagnosis gate); an LLM classifier catches rephrasings and can only *add* a
refusal, never overturn one.

**Failure modes / gaps.**
- The keyword layer keys on individualization markers ("my/this patient", "should
  I give/administer"). Heavily rephrased individualized requests that avoid these
  markers rely on the LLM layer, which is unavailable without an API key.
- The general-vs-individualized line is genuinely fuzzy; the tool errs toward
  *allowing* ambiguous literature questions (grounding still protects the
  answer), which means some borderline individualized phrasings may pass.
- Non-English queries are not classified.

## 3. Red-flag escalation (`app/guardrails/redflag.py`)

**What it does.** Detects active-emergency indicators (cardiac chest pain with
radiation, anaphylaxis, stroke symptoms, suicidal ideation, septic shock,
respiratory/cardiac arrest) and renders an **escalation banner with local
emergency guidance before any retrieved content**. It is **non-blocking** and
runs for every query, including legitimate clinician ones — the banner is cheap
and the right default.

**Failure modes / gaps.**
- Pattern-based, so it misses emergencies described without the trigger
  vocabulary, and it false-positives on benign mentions (e.g. a literature
  question that merely contains "chest pain"). False positives are intentional
  and acceptable here; a spurious banner is harmless, a missed emergency is not.
- It does not triage severity or replace clinical judgment or emergency services.

## 4. Grounding verification (`app/guardrails/grounding.py`)

**What it does.** After generation, splits the answer into sentences, decides
which carry a clinical claim, and verifies each claim against the passage it
cites: `SUPPORTED` / `PARTIALLY_SUPPORTED` / `UNSUPPORTED` / `NO_CITATION`.
Clinical sentences that are `UNSUPPORTED` or `NO_CITATION` are removed; if that
strips more than **30%** of the answer, the whole answer is **rejected and the
system abstains**. A fabricated statistic (a number not present in the cited
passage) fails verification.

The verifier is pluggable: an offline lexical-entailment heuristic (default) or
an LLM entailment verifier. The LLM verifier falls back to the heuristic on any
error, so an outage never lets an ungrounded answer through.

**Failure modes / gaps.**
- The offline heuristic is lexical (token overlap + fabricated-number veto); it
  can miss paraphrased-but-unsupported claims that share vocabulary with the
  passage, and can over-flag correct paraphrases that don't. The LLM verifier is
  materially stronger and is the intended production path.
- Clinical-claim detection is heuristic: a claim phrased without the signal
  vocabulary may be treated as non-clinical framing and escape verification.
- It verifies *grounding in the cited passage*, not the *correctness of the
  underlying literature* — a well-cited claim from a retracted or wrong paper
  would still pass. Source quality is handled upstream by evidence-grade ranking,
  not here.
- It verifies that a sentence is supported by *the passage it cites*, not that
  the passage is *about the question*. An answer assembled from on-topic-sounding
  but irrelevant passages is grounded and wrong. That failure belongs to the
  retrieval grader (`grade_retrieval`), which is why the offline grader requires
  the question's topic phrases to appear in the retrieved passages: the golden
  set's uncovered-topic questions (`expected_abstain`) exist to catch exactly
  this, and the first full run of the harness caught it (a rabies question
  answered from an antenatal screening paper at high confidence).

## 5. Composition & audit (`app/guardrails/pipeline.py`)

Pre-retrieval order is **PHI → scope → red-flag**; grounding runs
post-generation. Every pre-retrieval verdict is written to
`queries.guardrail_verdict` and an immutable `audit_log` row (per tenant), and
grounding outcomes are audited too. Audit persistence never blocks a request and
never stores raw PHI.

## 6. The voice path (`app/voice/`)

Voice is not a side door. A spoken turn runs the same `GuardrailPipeline`
with the same verdicts, and adds what speech needs on top:

- **Spoken PHI forms.** Recognizers write dates and numbers the way they were
  said. The deterministic layer therefore also catches "date of birth March
  14, 1982", "born on 14th March 1982", record numbers read digit by digit
  ("MRN 0 0 4 8 2 9 3 1", also when the recognizer splits them with
  punctuation), spoken social security numbers, and a full name after a
  preposition ("…for John Smith") with an exclusion list so "in Atrial
  Fibrillation" or "at Mayo Clinic" never block. All of these run on the text
  path too.
- **Ordering.** PHI is scanned inline on the final transcript before anything
  is retrieved or embedded; the scope classifier and red-flag check run in
  parallel with retrieval, and **no TTS byte is released before the verdict**
  (`tests/test_voice_ws.py::test_guardrail_verdict_lands_before_the_first_tts_byte`).
  Partial transcripts are PHI-scanned before speculative retrieval fires.
- **Zero generation on a block.** A blocked spoken query never reaches the
  reasoner: the persisted turn records `output_tokens: 0`.
- **The LASA gate.** A drug on the ISMP confused-drug-names table heard with
  low recognizer confidence, produced by the correction pass, or with a
  plausible sound-alike is confirmed by voice and on screen before the
  question is answered — one extra turn, never a silent substitution. The
  harness separates a *substitution* (a different ISMP name reached the
  pipeline unconfirmed — a build failure) from a name garbled beyond any
  drug (a recognition miss, counted in the medical-term error rate and
  listed as `unrecognized`); the second still means the wrong question
  gets answered or abstained on, which the clinician hears and re-asks.
- **Spoken adversarial set.** `evals/voice/fixtures` reads every text
  adversarial category aloud (PHI mid-sentence, diagnosis requests, prompt
  injection, red-flag emergencies, personal medical). The voice harness fails
  the build unconditionally on a PHI or diagnosis miss
  (`uv run python -m evals.voice.run --gate safety`).

## What this layer explicitly does NOT protect against

- **It is not a medical device and not clinical advice.** Every answer is
  literature reference for a qualified clinician to interpret.
- **It does not guarantee the literature is correct or current** beyond the
  corpus it was given and the evidence-grade/recency ranking applied.
- **It is not a HIPAA/GDPR compliance control.** It minimizes accidental PHI
  entry; it does not make the system a compliant processor of PHI.
- **It does not defend against a determined adversary** exfiltrating via novel
  encodings, multi-turn manipulation, or adversarial inputs outside the tested
  categories.
- **Coverage is English-only** across all layers.
- **A patient name spoken without any cue** ("what about Priya?") is not
  caught by the deterministic layer; the bare-name rule needs a preposition
  and two capitalized words. Presidio NER records such names but does not
  block on them, by design (it false-positives on drug names and eponyms).

## What Phase 14 added to the evidence

The guardrails were already tested; verification asked whether they hold in
the *deployed* shape of the system, from a browser and a microphone rather
than from a test harness.

- **The spoken PHI fixture is played through a real microphone** in a real
  Chromium (`frontend/e2e/voice.spec.ts`, Chromium's
  `--use-file-for-fake-audio-capture`). The assertion is not that a screen
  appeared: the turn is `blocked_phi`, no answer row exists, and the cost
  ledger contains **no generation event** for that query. Nothing reached a
  model.
- **The typed PHI path is asserted the same way**, plus the SSE stream is
  checked to contain no `generating` or `token` stage
  (`frontend/e2e/02-ask.spec.ts`).
- **The red-flag banner is measured, not just present**: the test compares
  bounding boxes to prove it renders above the confidence badges and above the
  answer text, and that the first child of the answer region has
  `role="alert"`.
- **Tenant isolation was attacked rather than asserted**
  (`frontend/e2e/03-isolation.spec.ts`): a forged `org_id` in the request
  body, a tampered JWT, an `alg: none` token, another tenant's document and
  chunk ids, and direct SQL under org B's own RLS context — each one refused,
  and the row checked afterwards to confirm nothing moved.

One real weakness was found and fixed in that pass: `documents.content_hash`
was globally unique, so uploading a PDF another organisation already held
returned *"this document already exists in another tenant's corpus"* — no
content crossed the boundary, but **existence** did, which let one tenant test
for another's private document. De-duplication is now scoped per tenant
(migration 023), and two organisations may each hold their own copy of the
same guideline, which is also the correct product behaviour.

## Running the adversarial suite

```bash
cd backend
uv run python -m evals.adversarial.build_cases   # regenerate cases.jsonl
uv run python -m evals.adversarial.run            # deterministic (CI default)
uv run python -m evals.adversarial.run --allow-llm  # include LLM enhancement tiers
```

Exit code is non-zero if the gate is not met. Results are written to
`backend/evals/results/adversarial_results.json`.
