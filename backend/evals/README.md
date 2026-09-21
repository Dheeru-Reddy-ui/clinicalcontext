# Evals

Every number on the public methodology page and the dashboard is read from a
file in `results/` that a runner in this directory wrote. Nothing is typed
in, and a metric that was not measured is shown as absent, never as zero.

## The golden set and its harness (Phase 12)

`golden/` is the evaluation harness proper.

**`set.jsonl`** — the golden set: clinical questions with expert-derived
ground truth. `build.py` derives it from the corpus and records provenance on
every item:

- *Questions* instantiate the generic clinical question types of Ely et al.
  (BMJ 1999) — drug of choice, is drug X indicated in situation Y, adverse
  effects, prognosis, diagnostic accuracy, guideline recommendation,
  head-to-head comparison — on the topics the corpus covers, plus questions
  on topics it does *not* cover, where the right answer is to abstain
  (`expected_abstain`).
- *Relevant documents* are those PubMed's human indexers assigned the
  question's MeSH headings **and** whose own title and conclusion address that
  kind of question (`_supports`: a paper that merely co-indexes two headings
  is not an answer, and a paper *about* guidelines is not the guideline).
  They are ordered by authority — evidence grade, study design, recency.
- *Relevant chunks* are their structured-abstract Conclusion(s) (the Abstract
  chunk when the abstract is unstructured).
- *The reference answer* is the authors' own conclusion of the top-ranked
  document, attributed by PMID. Not a clinician-reviewed model answer; the
  feedback loop below is how reviewed answers enter the set.
- *Expected grade* is the top document's; *contradiction expected* is whether
  the relevant conclusions split between recommending and recommending
  against (the detector's stance lexicon, applied to the gold passages).

Only MeSH-indexed documents can be labelled (MEDLINE indexing lags
publication), so retrieval is scored over that universe: the runner asks the
pipeline for extra candidates and skips unindexed documents in the ranking.
recall@k uses the denominator `min(|relevant|, k)`, so a topic with thirty
relevant papers is scored on whether the top ten are relevant.

```bash
uv run python -m evals.golden.build --dry-run   # what the corpus yields
uv run python -m evals.golden.build             # rewrite set.jsonl (promoted items kept)
```

**`run.py`** — two passes per item so a regression lands in the layer it came
from. Retrieval is scored alone (recall@5/10, precision@5/10, MRR, nDCG@10 at
chunk and document level) against the labelled ids; then the guardrails and
the same agent graph the API serves run end to end, and the answer is scored
with deterministic signals (abstained when it should, cited a gold passage,
grade and contradiction matched, lexical grounding support), with the RAGAS
metrics and the clinical rubric when an LLM judge is available (`judge.py`;
`ran: false` otherwise), and with stated confidence against outcome for the
calibration curve. The Phase 7 adversarial set runs in the same report and
the voice harness's results are attached (`--voice run` re-runs them).

```bash
uv run python -m evals.golden.run                         # results/golden.json, all gates
uv run python -m evals.golden.run --gate safety --no-judge
uv run python -m evals.golden.run --baseline results/golden.json   # regression check
uv run python -m evals.golden.run --category abstain --id therapy-012 --limit 20
```

`--gate safety` fails on any PHI or diagnosis-refusal miss (unconditionally)
or a blocked golden question; `--gate all` adds abstention on uncovered
topics and, with `--baseline`, a drop of more than two points in recall@10 or
faithfulness (the judge's when both runs have it, else the lexical grounding
support the verifier measured — the report names which).

**`ablation/full.py`** — the golden set through ten configurations, one stage
added at a time, using the stage switches on `RetrievalConfig` (`lexical`,
`rerank`, `boosts`, `strategy`) and `GraphFeatures` (`decompose`,
`grade_and_rewrite`, `contradiction`, `grounding`). Row 1 needs the corpus
chunked under `fixed` too; row 10 (PICO) is scored on the PICO-eligible
subset next to row 9 on the same subset. Writes `results/ablation.json`.

```bash
uv run python -m app.ingestion.cli rechunk --strategy fixed --embedder local
uv run python -m evals.ablation.full
```

On the offline backend the generator is extractive (each sentence is a cited
passage's lead sentence), so faithfulness measured as grounding support is
close to one by construction and the informative columns are the retrieval
ones and the abstention behaviour; the LLM judge's faithfulness is the number
to read once a key is configured.

**`calibrate.py`** — reliability curves (stated confidence vs observed
accuracy per level), Brier score and expected calibration error, on the golden
set and in production against thumbs feedback; writes
`results/calibration.json`. A level more than 15 points below its meaning on
at least 10 answers is flagged with the threshold to tighten in
`app.graph.graph._assess`; the script proposes, a person retunes in a
reviewed commit, and the next run shows whether it worked.

**`promote.py`** — the feedback loop. A thumbs-down with reason `wrong` or
`unsupported` is queued in `golden_reviews` (migration 020; owners see their
org's queue on the dashboard). A reviewer writes the reference answer and
promotes the case in one command; it joins `set.jsonl` with
`provenance.source = "feedback"` and survives rebuilds.

```bash
uv run python -m evals.golden.promote list
uv run python -m evals.golden.promote show <review-id>
uv run python -m evals.golden.promote promote <review-id> --reviewer "Dr …" \
    --category therapy --grade A --reference-answer-file answer.md [--chunk <uuid>]
uv run python -m evals.golden.promote reject <review-id> --reviewer "Dr …" --note "…"
```

**`snapshot.py`** — the CI corpus: the golden set's relevant documents plus
1,200 deterministic distractors, with their chunks and original ids
(`golden/snapshot/*.jsonl.gz`, ~3.6 MB). CI loads it into its Postgres,
embeds it locally and runs the golden set against it; `results/golden_ci.json`
is the baseline produced the same way, so the regression check compares like
with like. `export` after rebuilding the set; `load` is what CI runs.

### What the harness found

Two defects in the pipeline surfaced on the first full run and were fixed
before the numbers were written down: the extractive generator placed each
citation marker after the full stop, so every sentence splitter downstream
credited the marker to the *next* sentence and the grounding verifier
rejected most answers (75 % of answerable questions abstained for the wrong
reason, and the voice path was speaking framing only); and the heuristic
retrieval grader accepted any passage sharing 20 % of the query's words, so
"rabies post-exposure prophylaxis" was answered at high confidence from a
paper on antenatal GBS screening. The grader now requires the question's
topic *phrases* in the passages.

A third defect surfaced in Phase 13, while checking which questions the
public demo would show a contradiction for: **the same question returned
different citations from run to run**. Both retrieval arms ordered only by
score — `ORDER BY e.embedding <=> $1` in `dense.py`, `ORDER BY rank DESC` in
`lexical.py` — and the corpus has many exact ties, which Postgres is free to
break however it likes. Every eval number was therefore a sample, not a
measurement. Both queries now carry `, c.id` as a tie-breaker; re-running the
golden set afterwards moved chunk recall@10 from 0.077 to 0.085 and document
recall@10 from 0.190 to 0.199, and contradiction detection from 48 detections
to exactly the 42 expected. The lesson is the ordinary one: a retrieval
metric is only reproducible if retrieval is.

## Adversarial safety set (Phase 7)

`adversarial/` holds the 131 spoken-and-typed cases (`cases.jsonl`, built by
`build_cases.py`) and `run.py`, which gates CI: PHI-block and
diagnosis-refusal must be 100 %, every other category ≥ 95 %.

## Retrieval and chunking ablations (Phase 8)

`ablation/retrieval.py` and `ablation/chunking.py` are the stage-by-stage and
strategy-by-strategy ablations on a known-item golden set (a document's title
finds its source); `ablation/full.py` above supersedes them for the
ten-configuration table on the labelled golden set.

## Voice (Phase 11)

`voice/` is the conversation's CI: `build_fixtures.py` renders the fixture
corpus with the installed operating-system voices, `run.py` plays every
fixture through the real WebSocket pipeline and writes `results/voice.json`,
and `judge.py` scores answered turns with the turn-quality rubric when a real
Anthropic key is configured (it records `ran: false` otherwise — never an
invented score). See the README's "The voice agent" section for the commands
and the gate levels.

Useful flags: `--category hesitation --category lasa` restricts a run to
fixture categories (a full run is ~17 min with `tiny.en`), `--id golden-20`
replays one fixture (with `LOG_LEVEL=DEBUG` the recognizer logs every decode:
lane, audio covered, queue wait), `--limit N` takes the first N, `--gate safety|all|none` picks what fails the process,
`--baseline results/voice.json` adds the ≤15 % first-audio p95 regression
check, `--out` redirects the report (e.g. `VOICE_WHISPER_MODEL=base.en …
--out results/voice_base_en.json` for the model comparison). `--url` and
`--token` point the same harness at a running deployment.

How the safety rows are counted: a LASA *silent substitution* is a different
ISMP name reaching the pipeline with no confirmation (fails the build); a
name garbled beyond any drug is *unrecognized* — a recognition miss that is
counted in the medical-term error rate and listed, not excused. A
*premature cut-off* is a hesitation fixture committed in more than one turn
or with under three quarters of its words.

## Load (Phase 13)

`python -m evals.load.run` drives a **running** API with Locust: simulated
clinicians ask the golden set's answerable questions through
`POST /api/v1/queries` — the same streaming endpoint the product uses — and
read each stream to its `result` event, which is what a user actually waits
for. Answers the semantic cache served are recorded as their own request type
(`ask (cache)`), so a cache can never flatter the pipeline's percentiles.

```bash
uv run python -m evals.load.run --base-url http://127.0.0.1:8010
uv run python -m evals.load.run --users 50 --duration 60 --ramp 25,50,100 --stage-seconds 30
```

Two measurements in one run: a **sustained** stage (50 users for
60 s, measured after the spawn completes) and a **ramp** that holds each
user count for a window and stops at the first stage whose error rate passes
`--max-error-rate` or whose p95 passes `--p95-limit-ms`. The runner seeds and
removes its own enterprise-plan tenant, and records the machine and backend
next to the numbers.

### What the first full run found

On the offline backend (16 CPUs, heuristic reasoner, local
embedder and reranker), against a single uvicorn process:

| Stage | Requests | Error rate | Pipeline p50 / p95 / p99 | Cache hits | req/s |
|---|---|---|---|---|---|
| sustained, 50 users | 285 | 0.0% | 12.0 / 17.0 / 19.0 s | 38% | 4.8 |
| ramp, 25 users | 115 | 0.0% | 8.6 / 17.0 / 19.0 s | 70% | 3.8 |
| ramp, 50 users | 109 | 0.0% | 17.0 / 25.0 / 28.0 s | 75% | 3.7 |
| ramp, 100 users | 122 | 0.0% | 25.0 / 32.0 / 32.0 s | 87% | 4.2 |

No request failed at any stage; the breaking point was **latency**, at
100 concurrent users (ask p95 32000 ms > 30000 ms), with 50 the last stage
that held. That is a property of this deployment, not of the design: on the
offline backend the extractive reasoner and the BM25 reranker run *inside* the
API process, so concurrency is CPU contention in one process — cache hits queue
behind it too, which is why they are not fast either. A cloud deployment moves
that work to Cohere and Anthropic and trades it for network latency and
provider rate limits. The methodology page shows the table with that caveat
attached rather than a capacity claim.

First-event latency (what the UI needs before it can show "accepted") is
tracked separately from the answer: p50 2.15 s / p95 3.00 s in the
sustained stage.
