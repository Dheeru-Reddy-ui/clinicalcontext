# Architecture notes

Decisions that shape the system, with the trade-off each one accepts. Phase
numbers refer to the build plan in the README.

## Voice: a cascaded pipeline, not a speech-to-speech model (Phase 11)

**Decision.** The voice agent is a cascade — streaming speech-to-text →
the Phase 7 guardrails → the Phase 8 retrieval graph → streaming
text-to-speech — over one WebSocket per session (`/api/v1/voice/ws`). It is
*not* a speech-native model (OpenAI Realtime, Gemini Live).

**Why.** Speech-native models are more naturally conversational, but they
generate audio directly from the model: there is no interception point for
the PHI guardrail, the scope classifier, the grounding verifier, or citation
injection. For a clinical evidence tool, control beats naturalness. The
cascade gives every claim a source and every spoken word a gate it passed
through; what it costs in naturalness is clawed back with latency
engineering (below). The trade-off is stated on the public methodology page
and is deliberate.

**How the latency comes back.** The budget is measured from the moment the
user stops speaking to first audio out of the speaker (target p50 ≤ 1.2 s,
p95 ≤ 2.0 s, `evals/results/voice.json` is the record):

| Leg | Mechanism |
| --- | --- |
| Endpoint decision | Layered: acoustic silence window → semantic completeness of the fresh partial (extend when the clinician is mid-thought) → a hard 2 s ceiling. The commit is provisional until the final transcript lands: if the final carries a word the partial lacked and now reads as incomplete ("…treatment?" → "…treatment for"), the words are held and the window runs on — a retracted commit costs a longer wait, never a cut-off. `app/voice/endpointing.py`, `VoiceSession._commit` |
| Final transcript | Streaming partials; the speech-end hint asks the recognizer to decode what it has, so the commit finds the transcript already in hand. |
| Guardrails | Regex PHI inline on the final (< 5 ms); scope and red-flag run *in parallel* with retrieval; no TTS byte is released before the verdict (`TurnRunner._gate`). |
| Retrieval | Speculative retrieval fires once per turn on a *fresh* partial — one whose audio coverage reaches the user's last word (a decode that merely *finished* after the pause may have started before it) — that reads as complete, after the same correction pass the final gets; on commit a close-enough final reuses the chunks. Hit and wasted rates are tracked and charted. `app/voice/speculation.py`, `SttEvent.covered_ms` |
| First sentence | Voice prompt (`prompts/voice_answer.v1.md`) leads with the direct answer; the sentence pipeline ships each sentence to TTS as it closes, grounding-gated. |
| TTS first byte | ElevenLabs Flash over a WebSocket pre-warmed at session start; the offline backend synthesizes per sentence with the OS voice. |
| Client playback | AudioWorklet playback with a 2-frame jitter buffer; an instant flush on barge-in, measured client-side. |

**Guardrail parity.** Voice is not a side door. The same `GuardrailPipeline`
runs with the same verdicts; the spoken adversarial set in
`evals/voice/fixtures` is the text adversarial categories read aloud, and
the harness fails the build unconditionally on any PHI or diagnosis miss.
Speech recognizers write dates and numbers the way they were said, so the
deterministic PHI layer also understands spoken forms ("date of birth March
14 1982", "MRN 0 0 4 8…", digit runs split by punctuation) and a full name
after a preposition.

**Medical-grade hearing.** The recognizer is biased with a boost vocabulary
derived from the corpus — its drug-like MeSH terms by document count, then
every name on the ISMP confused-drug-names table, then the other terms —
ranked so that a recognizer with a small budget (whisper's prompt) gets the
names *this corpus* is asked about, and a cloud recognizer's larger keyterm
budget adds the look-alike names; a fuzzy
correction pass fixes near-misses in the *final* transcript only and logs
every change; a confirmation gate asks — by voice and on screen — before
answering when a look-alike/sound-alike drug was heard with low confidence.
One extra turn beats a silent substitution.

**Providers behind a Protocol.** `voice_backend=cloud` uses Deepgram
`nova-3-medical` and ElevenLabs Flash/Multilingual (per-org setting);
`voice_backend=offline` uses faster-whisper and the operating system's
synthesizer (Windows OneCore voices, espeak-ng on Linux). The orchestration
— state machine, endpointing, speculation, gates, rendering, barge-in,
persistence — is identical; only the two edge adapters differ. The tests
swap in scripted doubles for those two adapters and exercise everything
else for real.

**Documented alternative — transport.** WebRTC via LiveKit gives better
jitter and packet-loss behaviour at production scale (adaptive bitrate,
NACK/FEC, SFU fan-out). We build on raw WebSockets because owning the
pipeline is the point of this project; LiveKit is the scale-out path — the
session and turn logic would sit unchanged behind a LiveKit agent, with the
audio tracks replacing the binary frames.

**Documented trade-off — Flash vs. Multilingual.** ElevenLabs Flash
(`eleven_flash_v2_5`) has the lowest time-to-first-byte and is the default;
Multilingual v2 has richer prosody and accent range at roughly three times
the model latency. It is a per-organization setting
(`organizations.voice_tts_quality`, owner-only), because the right answer
depends on whether the org is gloved-and-scrubbed or reading on a phone.

**Known limits of the offline backend.** faster-whisper is not a streaming
recognizer; partials are re-decodes of the utterance so far, and the
endpoint decision waits for a decode that covers the user's last word. The
waterfall reports that cost as it is. Three measured facts shape how that
decode is scheduled (`app/voice/stt/whisper_local.py`): a decode costs the
same ~0.5 s whether the utterance is 1 s or 5 s (the encoder runs on a
30 s window), so a tail-only decode buys nothing; two decodes at once do
not overlap on the CPU (the slower of a pair lands at ~2.1x a solo decode),
so the speech-end decode queues behind an in-flight partial instead of
racing it; and the hotword prompt is paid for on every decode — flat to
~80 tokens, then roughly double — so whisper takes the corpus's top-ranked
drug names up to that budget rather than a fixed count. The session tells
the recognizer *where* its VAD heard speech end, in stream time, so the
decode that counts as fresh is the one covering that frame — the two
components' loudness thresholds differ by design and must not disagree
about which decode is the last one. The tiny/base model choice is a
measured accuracy-vs-latency trade-off recorded in
`evals/results/voice.json` — not an allowance.

## Evaluation: ground truth that does not come from the pipeline (Phase 12)

**Decision.** The golden set's labels are derived from what human experts
wrote — PubMed's MeSH indexing selects the relevant documents, the authors'
structured-abstract conclusions are the relevant passages and the reference
answer — and never from the retrieval pipeline under test. Questions are
instantiated from a published taxonomy of clinical questions (Ely et al.,
BMJ 1999) on the topics the corpus covers. Reviewed cases from real usage
join the set through the feedback loop with a reviewer-written answer.

**Why.** A golden set labelled by running the retriever and keeping what it
returns scores the retriever against itself. MeSH indexing is independent,
professional, and already in the corpus metadata; a content gate on each
document's own title and conclusion (does it address *this kind* of
question about *these* entities?) keeps co-indexing from counting as
relevance. The cost is coverage: only MeSH-indexed documents (56 % of the
corpus) can be labelled, so retrieval is scored over that universe — the
runner asks for extra candidates and skips unindexed documents in the
ranking, and says so in the report. recall@k uses `min(|relevant|, k)` as
its denominator so a well-covered topic is not penalised for its coverage.

**Retrieval is scored before generation.** Two passes per item: the pipeline
ranks passages and is scored against the labelled ids; then the same graph
the API serves runs end to end. A recall regression and a faithfulness
regression are different bugs in different layers and the report keeps them
apart. The ten-row ablation uses stage switches that already exist on
`RetrievalConfig` and `GraphFeatures` rather than a parallel pipeline: a row
is the production code with a stage off, so what the table measures is what
ships.

**Faithfulness on the offline backend.** The heuristic reasoner is
extractive — every clinical sentence is a cited passage's lead sentence —
so faithfulness measured by the lexical grounding verifier is close to one by
construction. The report labels which faithfulness it carries (the LLM
judge's RAGAS score, or the lexical proxy) and the CI regression check
compares like with like. The RAGAS metrics are computed as the paper defines
them through the Anthropic SDK behind an `AnswerJudge` protocol, not through
the `ragas` package (which needs a LangChain-wrapped model); without a key
the judge reports `ran: false` rather than a number.

**The CI corpus is a snapshot.** GitHub Actions cannot hold the corpus, so
the golden set's relevant documents plus 1,200 deterministic distractors
(3.6 MB gzipped, original ids preserved) are loaded into the job's Postgres
and embedded locally. The numbers differ from a full-corpus run — fewer
distractors — so the committed CI baseline is produced the same way and the
gate compares snapshot to snapshot.

**Calibration retunes by hand.** Stated confidence is categorical; the
reliability curve maps each level's nominal probability to its observed
accuracy (judge correctness on the golden set when keyed, otherwise whether
a gold passage was cited; thumbs feedback in production). Drift beyond
fifteen points on ten answers produces a recommendation naming the
threshold in `_assess` to tighten. The script never changes a threshold: a
confidence rule is a product decision, reviewed like one.

## Observability: attribution without plumbing (Phase 13)

The full operational picture — how to run each signal, what it answers and
how to read a trace — is [OBSERVABILITY.md](OBSERVABILITY.md). What follows
is why the design is what it is.

**Context belongs on the span, not in the call signature.** A span created
inside the reranker has no idea which tenant it serves; passing ids down
through five layers to tell it would be a change to every signature for the
benefit of telemetry. Instead `ContextAttributes`, a `SpanProcessor`, copies
the structlog contextvars (`request_id`, `tenant_id`, `query_id`, `user_id`,
`channel`) onto every span at `on_start`. The ids arrive because the request
bound them once, at authentication and at query provisioning. Logs and traces
therefore carry the same ids by construction rather than by discipline, and
`sentry_before_send` reads the same contextvars, so an error report, a log
line and a span all name the same request.

**Provider calls are spans, not stage spans.** `retrieval.rerank` says how
long the stage took; `provider.cohere.rerank` says how long the vendor took.
Wrapping the call site (`app/retrieval/embedders.py`,
`app/retrieval/rerank.py`, `app/graph/reasoner.py`) is what separates *our*
latency from *theirs* on a trace, and it is the same place the cost ledger
records the units, so the two can never disagree about what was called.

**The ledger is contextvar-scoped, because providers do not know the
tenant.** A request opens `cost.collecting()`; providers inside call
`cost.record(...)` with what the provider bills; the request flushes the
events with its `org_id`/`query_id`. Concurrent requests cannot mix, tasks
started inside the request inherit the same collector (so the graph's nodes
and a voice turn's speaker land in the right turn), and the same provider
called from an eval or a backfill records nothing rather than inventing a
tenant.

**Cache savings are recorded, not estimated.** A semantic-cache entry stores
the rerank and generation events of the answer it cached; a hit replays them
with `cached=true`. So "semantic caching avoided $X" is a sum over the events
the cached answer actually made — not a per-query average multiplied by a hit
count. The embedding cache does the same for tokens. The hit still embeds the
query, so that event is never counted as avoided.

**Offline is $0, and the projection is labelled.** The local embedder, BM25,
the extractive reasoner, faster-whisper and the OS voice cost nothing, so the
ledger stores `0.000000` — the alternative, storing what Cohere *would* have
charged, would be a fabricated bill in the database. The projection is
computed at read time from the same units at the cloud provider's list price
and shown in a column the dashboard marks as a projection. `LIST_PRICES` and
`PRICE_CHECKED` are the single place a rate is written down.

**Waste is attributed.** Speculative voice retrieval fires on a partial
transcript and is often thrown away. Its provider calls belong to the turn
that resolves it — hit or miss — because waste that is not recorded is waste
that cannot be managed.

**Load numbers carry their machine.** `evals/results/load.json` records the
platform, CPU count, backend and target beside the percentiles, because a
laptop running the extractive reasoner in-process and a cloud deployment
calling Anthropic are different systems. Cache-served answers are counted as
their own request type so the pipeline's percentiles are the pipeline's. The
first full run broke at 100 concurrent users on p95, not on errors — on the
offline backend the reasoner and reranker run inside the API process, so the
limit is CPU contention in that process, and the honest reading is on the
methodology page rather than a capacity claim.

**Retrieval has to be deterministic before any of this means anything.**
Both arms ordered only by score (vector distance; `ts_rank_cd`), and the
corpus has many exact ties, so Postgres was free to return a different top-k
for the same query — which it did. Every published retrieval number was a
sample rather than a measurement, and a trace of one query said nothing about
the next. `dense.py` and `lexical.py` now break ties on `c.id`. Determinism is
a precondition for observability, not a nicety: you cannot attribute a
regression to a change if the baseline moves on its own.

**The public page reads the artifacts, not a copy of them.** `/methodology`
fetches `evals/results/*.json` through a whitelisted public endpoint at
request time and renders "not run" where a file is missing. There is no step
where a number is transcribed into prose, which is the only way a published
number stays true after the next run.

## The options that were rejected (Phase 14)

Every section above explains a decision. This one explains the alternatives
that were seriously considered and turned down, because the reasoning is the
part that transfers.

**Elasticsearch / OpenSearch for lexical retrieval → Postgres full-text
search.** Elasticsearch is better at lexical search than Postgres: BM25 is
native, analyzers are richer, and it scales past one machine. It was rejected
because the tenant boundary is row-level security, and RLS is a Postgres
feature. Running the lexical index elsewhere would mean enforcing tenancy
twice — once in the database, once in the search cluster — and a tenant leak
would then be one forgotten filter away in a system where nothing else
enforces it. With one store, "org A cannot see org B's chunk" is a policy the
database refuses to break, provable in SQL (`tests/test_rls.py`), and a
hybrid query is one statement rather than a fan-out and a merge. The cost is
accepted honestly: `ts_rank_cd` is a weaker ranker than Elasticsearch's BM25,
which is part of why the lexical arm contributes what it does in the
ablation. At a corpus two orders of magnitude larger, this decision should be
revisited — and the ablation is how you would know.

**A managed vector database (Pinecone, Weaviate, Qdrant) → pgvector in the
same Postgres.** Same reasoning, plus one more: a separate vector store makes
the embedding a second source of truth that can drift from the chunk it
describes. Here `chunk_embeddings` has a foreign key to `chunks` and the same
RLS policy, so a deleted document cannot leave a searchable ghost. pgvector's
HNSW is slower than a dedicated engine at scale and the index is 315 MB for
44k vectors; at tens of millions that trade flips.

**LlamaIndex or a framework-level RAG abstraction → LangGraph for the state
machine only.** The pipeline's interesting behaviour is all in the parts a
framework hides: when to rewrite a query, when to abstain, how to structure a
contradiction, what counts as grounded. A framework would have made the first
week faster and every week after that slower, because each of those decisions
would be fought against a default. LangGraph is used for what it is good at —
a typed state machine with a recursion limit — and the nodes are ours.

**A reranking model served in-process → Cohere rerank-v3.5 with a local BM25
stand-in.** A cross-encoder on our own GPU would be cheaper per query at
volume and has no vendor dependency. It was rejected for this build because
it turns a deployment into a GPU deployment, and because the eval harness
makes the swap safe later: `RetrievalConfig` switches the reranker, the
ablation measures what it is worth, and the decision becomes a number rather
than an opinion.

**Fine-tuning a model on clinical text → prompting with versioned prompts and
an extractive fallback.** Fine-tuning is the wrong tool for a system whose
job is to be *faithful to retrieved text*: it moves knowledge into weights,
which is the failure mode (confident recall of something not in the corpus)
the whole design exists to prevent. Versioned prompt files, a judge protocol
and a grounding verifier keep the answer tied to the passages, and the
offline extractive reasoner exists precisely so the pipeline can be measured
without a model at all.

**Celery or a queue service for background work → a loop in a second
process.** The jobs are idempotent, low-frequency and few (webhook delivery,
nightly re-checks, a weekly digest). Celery would add a broker, a result
backend, and a new failure mode — a task that is neither running nor dead —
in exchange for scheduling we do not need. The worker is `sh
worker-entrypoint.sh`: a loop, a sleep, and jobs that are safe to re-run. When
the work outgrows that, the jobs are already CLI commands, which is what any
scheduler wants.

**Storing PHI with encryption and a BAA → refusing PHI at the door.** The
product could have accepted patient data, encrypted it, and pursued a HIPAA
posture. It does not, because "no PHI" is a boundary that can be enforced and
tested (131 adversarial cases, 100% on PHI and diagnosis, the same on the
spoken path) where "PHI handled correctly" is a programme of work that is
never finished. The narrower product is the one that can make an honest
promise.

**Server-side sessions → Supabase JWTs verified by JWKS.** A session table
would let us revoke instantly, which JWTs cannot. It was rejected because it
puts an unavoidable database read in front of every request — including the
streaming ones — and because the token's `org_id` claim is what RLS reads;
the same claim would otherwise have to be fetched and trusted from
application code. Revocation is handled where it matters instead: API keys
are hashed rows and die on the next request (`tests` cover exactly that), and
JWT lifetimes are short.
