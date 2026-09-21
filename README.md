# ClinicalContext AI

A production-grade, multi-tenant, evidence-based clinical question-answering
platform. Clinicians ask questions in natural language; the system answers
from public medical literature (PubMed, PMC open access, clinical guidelines)
with **every clinical claim grounded in a citation** — and says so explicitly
when the evidence is insufficient, weak, or contradictory.

> **This is a clinical decision *support* tool, not a diagnostic tool.** It
> answers questions about the literature. It does not diagnose patients, does
> not accept patient data, and does not store PHI. That boundary is enforced
> in code (guardrail layer), not in a footer.

## The shape of it

```
 browser ──SSE/WebSocket──► FastAPI ──► guardrails ──► semantic cache
                                            │              │ miss
                                            │ block        ▼
                                            │         LangGraph
                                            │   classify → decompose → retrieve ──┐
                                            │        ▲                            │
                                            │        └── rewrite ◄── grade ◄───────┘
                                            │                         │
                                            │        contradiction ◄──┘
                                            │             │
                                            │          generate → verify grounding → assess confidence
                                            ▼             │
                                     PHI / scope /        ▼
                                     red-flag view    cited answer, or an abstention
                                                          │
 Postgres + pgvector ◄── dense + BM25, fused, reranked ────┘
```

Voice enters the same graph after speech-to-text, so every guardrail applies
to a spoken question too — proven, not assumed
([`e2e/voice.spec.ts`](frontend/e2e/voice.spec.ts) plays the adversarial
fixtures through a real microphone). A rendered version of this diagram, with
the observability signals marked, is on the
[methodology page](frontend/app/methodology/architecture-diagram.tsx).

## Where it stands

Measured on the offline backend against 9,996 PubMed abstracts
(run 2026-09-20; every number is read live from
`backend/evals/results/` by [docs/EVAL.md](docs/EVAL.md) and the methodology page):

| | |
|---|---|
| Adversarial safety suite | **131/131** (100%), PHI and diagnosis refusals 100% |
| Abstains when the corpus cannot answer | 90% |
| Retrieval recall@10 (documents / passages) | 0.199 / 0.085 |
| Answers citing a gold passage | 34% |
| Voice first audio | p50 1,094 ms / p95 1,460 ms |
| Load | 50 concurrent users at 0% errors; breaks at 100 on latency |
| Phase gates re-proven | [docs/VERIFICATION.md](docs/VERIFICATION.md) |

The retrieval numbers are low because the offline stand-ins are a hashing
lexical embedder and BM25 — the honest measurement of what runs without
vendor keys. The same harness pointed at Cohere and Anthropic is the number
that would matter for a deployment, and it has not been run, because there is
no key in this environment. Nothing here is estimated.

**Live demo:** not yet — deploying needs accounts (Fly, Vercel, Supabase
cloud, a domain). Everything for it is written and tested:
[docs/DEPLOY.md](docs/DEPLOY.md), `fly.toml`,
`.github/workflows/deploy.yml`. Run it locally in about ten minutes with the
setup below, or watch [the recorded demo](docs/demo/) — real footage of the
product, re-recordable with one command.

## Stack

| Layer     | Technology |
|-----------|------------|
| Backend   | Python 3.12, FastAPI (async), Pydantic v2, LangGraph |
| Database  | Supabase Postgres (pgvector + FTS), Row Level Security for tenant isolation |
| Cache     | Redis (Upstash in deploy) |
| AI        | Cohere embed-v4.0 / rerank-v3.5, Anthropic Claude, LangSmith, RAGAS |
| Voice     | Deepgram nova-3-medical STT, ElevenLabs Flash TTS (cascaded pipeline) |
| Frontend  | Next.js 15 (App Router), TypeScript strict, Tailwind, shadcn/ui |
| Observability | OpenTelemetry (OTLP → Jaeger locally), Sentry, LangSmith, a per-tenant cost ledger |
| Infra     | Docker Compose (local), GitHub Actions CI, Railway/Fly.io + Vercel |

## Local setup

### Prerequisites

- Python 3.12+ and [uv](https://docs.astral.sh/uv/) (`pip install uv`)
- Node 22+ and pnpm 10 (`npm install -g pnpm@10`)
- Docker Desktop

Optional, for the parts that need them:

- `pnpm exec playwright install chromium` — the end-to-end suite
- the Supabase CLI (`npx supabase start`) — real magic-link auth and mailpit
- `uv sync --group voice` — the offline speech stack (faster-whisper + an OS
  voice); without it the voice path needs Deepgram and ElevenLabs keys

Nothing above needs a paid account. The default `AI_BACKEND=offline` runs the
whole pipeline on local stand-ins, which is what every number in this
repository was measured on.

### 1. Environment

```bash
cp .env.example .env
```

The defaults work as-is against the local docker-compose services. Every
variable is required at boot (fail-fast); keys for later phases can keep
their placeholder values until that phase (each is annotated in the file).

### 2. Infrastructure

```bash
docker compose up -d
```

Starts Postgres 16 with pgvector (port 5432) and Redis 7 (port 6379), both
with healthchecks. First boot enables the `vector` extension automatically.

### 3. Backend

```bash
cd backend
uv sync                                        # creates .venv, installs everything
uv run python -m scripts.migrate --local-shim  # apply DB migrations (idempotent)
uv run uvicorn app.main:app --reload --port 8000
```

`--local-shim` gives plain Postgres the Supabase auth surface (schema,
JWT helpers, roles) so migrations and RLS policies are identical in every
environment. Against a real Supabase project, run without the flag and use
the direct (session-mode, port 5432) connection string — see
[backend/migrations/README.md](backend/migrations/README.md).

Verify:

```bash
curl http://localhost:8000/health   # liveness
curl http://localhost:8000/ready    # readiness: DB + Redis both "ok"
```

### 4. Frontend

```bash
cd frontend
cp .env.example .env.local   # then fill in the Supabase values
pnpm install
pnpm dev
```

### 5. Supabase (required for real sign-in from Phase 3)

1. Create a project at <https://supabase.com/dashboard> (any region).
2. **Settings → API**: copy the Project URL, `anon` key, and `service_role`
   key into `.env` (`SUPABASE_URL`, `SUPABASE_ANON_KEY`,
   `SUPABASE_SERVICE_ROLE_KEY`) and `frontend/.env.local`
   (`NEXT_PUBLIC_SUPABASE_URL`, `NEXT_PUBLIC_SUPABASE_ANON_KEY`).
3. **Authentication → Providers**: ensure Email is enabled. For instant local
   testing, disable "Confirm email"; for magic links add
   `http://localhost:3000/auth/callback` (or your dev port) to
   **Authentication → URL Configuration → Redirect URLs**.
4. Apply migrations to the project (direct/session connection string):
   `uv run python -m scripts.migrate --url "postgresql://postgres:<pw>@db.<ref>.supabase.co:5432/postgres"`
5. Point `DATABASE_URL` in `.env` at the same connection string (or keep the
   local compose DB for development — both carry the identical schema).

Open <http://localhost:3000/health> — it calls the backend `/ready` and
renders the live status of every dependency.

### 5b. Local auth without a Supabase account

The Supabase CLI runs a real GoTrue locally, so sign-in works end to end with
no cloud project:

```bash
npx supabase start -x realtime,storage-api,imgproxy,postgrest,postgres-meta,studio,edge-runtime,logflare,vector,supavisor
npx supabase status -o json   # API_URL, ANON_KEY, SERVICE_ROLE_KEY, JWT_SECRET
```

Put `API_URL` (`http://127.0.0.1:54321`) and the keys into `.env` and
`frontend/.env.local` as in step 5, and set `AUTH_IDENTITY_MIRROR=true` in
`.env`. Auth and application data then live in **different** databases (the
CLI's Postgres for GoTrue, the compose Postgres for everything else), so the
backend mirrors each verified identity into the data DB's `auth.users` shim on
first sign-in; on a real Supabase project the flag stays off. Magic-link
emails land in mailpit at <http://127.0.0.1:54324>. `supabase/config.toml`
already allows redirects to the dev ports, and exposes mailpit's SMTP port on
54325 so the weekly digest (Phase 13) can deliver to the same inbox.

### Port conflicts

If another local stack already holds 8000/3000 (containers with a restart
policy come back with Docker Desktop), run on alternate ports and keep the
three settings in sync:

```bash
uv run uvicorn app.main:app --reload --port 8010   # backend
pnpm dev --port 3005                               # frontend
```

Then set `NEXT_PUBLIC_API_URL=http://localhost:8010` in `frontend/.env.local`
and add `http://localhost:3005` to `CORS_ORIGINS` in `.env`.

## Corpus ingestion

The shared public corpus (`documents`/`chunks` with `org_id IS NULL`) is built
by a reproducible, idempotent pipeline: fetch → dedupe (`content_hash`) →
parse → classify → chunk → embed → upsert. Re-running any ingest inserts zero
duplicate rows.

```bash
cd backend

# Ingest by PubMed search (respects NCBI rate limits; abstracts + metadata)
uv run python -m app.ingestion.cli ingest --source pubmed \
  --query "atrial fibrillation anticoagulation" --limit 500

# PMC open-access full text, or a guideline PDF (stored in Supabase Storage)
uv run python -m app.ingestion.cli ingest --source pmc --query "sepsis management" --limit 200
uv run python -m app.ingestion.cli ingest --source guideline --file path/to/guideline.pdf

# Seed the whole demo corpus (17 queries x N across 4 clinical domains)
uv run python -m app.ingestion.seed --per-query 600

# Resumable backfill stages (run once real API keys are set)
uv run python -m app.ingestion.cli embed-pending      # Cohere embeddings
uv run python -m app.ingestion.cli classify-pending   # LLM grades the ungraded

# Corpus counts by source / evidence grade / study type
uv run python -m app.ingestion.cli stats
```

**Background jobs (arq):** long ingests can run on a worker instead of
in-process. Start it, then enqueue:

```bash
uv run arq app.ingestion.worker.WorkerSettings                       # worker
uv run python -m app.ingestion.cli enqueue --source pubmed \
  --query "type 2 diabetes" --limit 1000                             # enqueue
```

Failed documents dead-letter into `ingestion_failures` with a replayable
payload; jobs retry with exponential backoff.

**API keys.** Ingestion runs without paid keys — embeddings and LLM
classification are separate, resumable stages that defer cleanly when
`COHERE_API_KEY` / `ANTHROPIC_API_KEY` are placeholders. Set a real
`NCBI_API_KEY` (+ `NCBI_EMAIL`) to raise the NCBI limit from 3 to 10 req/s.

## Chunking strategies & the ablation

Four interchangeable chunking strategies live behind a `ChunkingStrategy`
protocol ([backend/app/retrieval/chunking/](backend/app/retrieval/chunking/)),
selectable by name and coexisting in the `chunks` table via a `strategy`
column:

| Strategy | Idea |
|----------|------|
| `fixed` | 512-token windows, 64-token overlap (baseline) |
| `recursive` | paragraph → sentence boundaries, packed to target |
| `semantic` | split where adjacent-sentence embedding distance spikes |
| `structural` | split on section structure; **prepend title + section header to the embedded text** so no chunk is context-free |

The ablation runs all four over the same 200-document subset, embeds each with
one held-constant embedder, and measures retrieval:

```bash
cd backend
uv run python -m evals.ablation.chunking --embedder local    # offline, no keys
uv run python -m evals.ablation.chunking --embedder cohere    # once COHERE_API_KEY is set
```

It writes a markdown table to stdout and
[backend/evals/results/chunking_ablation.json](backend/evals/results/chunking_ablation.json)
(read by the Phase 13 methodology page — never hand-edited). The embedder used
is stamped in the output; the default `local` embedder is a deterministic
lexical one so the harness runs and produces genuinely-measured numbers
without paid keys. Section structure for the sample is re-fetched from PubMed
and cached under `evals/ablation/cache/` (git-ignored) for reproducible reruns.

## Hybrid retrieval

The retrieval core ([backend/app/retrieval/](backend/app/retrieval/)) runs five
stages, each independently measurable:

```
embed query → (dense ∥ lexical) → RRF fuse → rerank → recency/evidence boost
```

- **dense** ([dense.py](backend/app/retrieval/dense.py)) — pgvector cosine over the HNSW index. Embeddings live in a dedicated narrow `chunk_embeddings` table (not inline on `chunks`), so a 6KB vector per row never bloats lexical scans.
- **lexical** ([lexical.py](backend/app/retrieval/lexical.py)) — Postgres full-text (`ts_rank_cd`) with OR-recall semantics, query-time medical-abbreviation expansion (`afib` → atrial fibrillation) from a synonym table, and **IDF pruning** (via a `lexeme_stats` table) that drops low-information common terms so ranking never scans a large fraction of the corpus.
- **fusion** ([fusion.py](backend/app/retrieval/fusion.py)) — Reciprocal Rank Fusion (`k=60`), configurable dense/lexical weights.
- **rerank** ([rerank.py](backend/app/retrieval/rerank.py)) — Cohere `rerank-v3.5` (production) or an offline BM25 reranker.
- **boost** ([boost.py](backend/app/retrieval/boost.py)) — recency + evidence-grade adjustment, fully **transparent**: every chunk carries `pre_boost`, `recency_factor`, `grade_factor`, and `post_boost` so the UI can explain any ranking.

The [pipeline](backend/app/retrieval/pipeline.py) runs dense and lexical
concurrently and writes one `retrieval_traces` row per stage (chunk ids +
scores + timing) whenever a `query_id` is supplied.

**Embeddings.** Query/document embedding goes through a batched, retrying,
Redis-cached [service](backend/app/retrieval/embed.py) that preserves the
`search_document` vs `search_query` input-type distinction. The corpus is
embedded by a resumable backfill:

```bash
uv run python -m app.ingestion.cli embed-pending --embedder local   # offline, 1536-dim
uv run python -m app.ingestion.cli embed-pending --embedder cohere   # production, once keyed
uv run python -m app.ingestion.cli build-lexeme-stats                # lexical-pruning frequencies
```

The local embedder is dimension-matched to the production `vector(1536)`
column, so dense search, the pipeline, and the ablation all run without a
Cohere key and swap to Cohere in place.

### Retrieval ablation

```bash
uv run python -m evals.ablation.retrieval --sample 100                       # offline
uv run python -m evals.ablation.retrieval --embedder cohere --reranker cohere # production
```

Measures recall@10, MRR, and nDCG@10 at every stage over a known-item golden
set (title → source document), plus end-to-end p50/p95 latency. Writes
[backend/evals/results/retrieval_ablation.json](backend/evals/results/retrieval_ablation.json).

## The agent graph

The reasoning core ([backend/app/graph/](backend/app/graph/)) is a LangGraph
state machine:

```
classify → [decompose] → retrieve → grade → (rewrite ↺ cap 2) →
    detect-contradiction → generate → verify-grounding → assess-confidence
                                          ↘ abstain ↗
```

- **Self-correction**: an LLM/heuristic grader scores retrieval
  `sufficient`/`insufficient`/`irrelevant`; on failure it rewrites the query
  (broaden, then HyDE) and retries — at most twice — then **abstains** rather
  than looping or guessing.
- **Contradiction is first-class**: when sources disagree, the answer is
  structured as an explicit conflict ("the 2019 guideline recommends A [1],
  the 2023 guideline recommends B [2]…"), never smoothed into false consensus.
- **Abstention is a feature**: an honest non-answer that says what was searched,
  what was found, and why it's insufficient.
- **Confidence** (`high`/`moderate`/`low`) and an evidence grade are derived
  from the cited sources' grades, recency, retrieval scores, and whether a
  conflict was found.

Every reasoning step is behind a `Reasoner` protocol with two implementations:
`LLMReasoner` (Claude, versioned prompts) and `HeuristicReasoner` (deterministic,
offline). The graph runs identically either way — selected by
`AI_BACKEND=offline|cloud`. LangSmith tracing (tagged with `tenant_id`,
`query_id`, prompt versions) turns on automatically when a real
`LANGSMITH_API_KEY` is set.

## The API

Everything under `/api/v1` accepts **either** a Supabase JWT (`Authorization:
Bearer …`) **or** a tenant API key (`X-API-Key: cck_…`), resolved by the same
dependency, so any endpoint works with either credential.

### Querying

`POST /api/v1/queries` runs guardrails → cache → graph → persistence and
**streams** Server-Sent Events: reasoning progress ("Searching the
literature…" → "Checking for conflicting evidence…"), then the answer as
`token` events, then a terminal `result` (or `blocked`) event.

```bash
curl -N -X POST http://localhost:8000/api/v1/queries \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"query": "How is type 2 diabetes managed with metformin?"}'
```

| Endpoint | What it does |
|---|---|
| `POST /queries` | Streaming answer (SSE). Supports `session_id`, `pico`, and comparison mode |
| `GET /queries`, `GET /queries/{id}` | History (filterable) and full detail: citations, retrieval traces, guardrail verdict, cost |
| `POST/GET /sessions`, `GET /sessions/{id}` | Multi-turn threads; follow-ups are contextualized against the previous turn |
| `POST /batches`, `GET /batches/{id}` | Up to 50 queries as one job; poll or subscribe to `batch.completed` |
| `GET /documents`, `/{id}`, `/{id}/chunks` | Browse the corpus — shared public corpus + this org's private uploads |
| `POST /documents/upload` | Ingest a PDF into the org's **private** corpus (parse → classify → chunk → embed) |
| `POST /feedback` | Thumbs + reason (`wrong`, `unsupported`, `outdated`, `incomplete`, `should_have_abstained`) |
| `GET /analytics/overview\|usage\|quality` | Volume, cost, latency percentiles, abstention/contradiction rates, feedback |
| `POST /answers/{id}/follow`, `GET /answers/{id}/versions` | Living Answers: follow an answer, read its immutable version history |
| `GET /suggest` | Autocomplete over MeSH vocabulary + this org's history (trigram-indexed) |
| `GET /corpus/freshness` | Per-domain staleness: how many papers the source has that we don't |
| `POST/GET/DELETE /webhooks`, `GET /webhooks/deliveries` | HMAC-signed webhooks and their delivery log |
| `POST/GET/DELETE /api-keys` | Scoped keys (`read` < `query` < `full`), hashed at rest, revocable |

**Comparison mode** returns an entity × outcome table where every cell carries
its own citations and evidence grade — and a cell whose retrieved passages
never mention the entity renders honestly as "Insufficient evidence." rather
than borrowing someone else's evidence.

### Cost, caching, and limits

- **Real cost accounting**: input/output tokens and USD come from the
  provider's reported usage, priced in one place
  ([`graph/cost.py`](backend/app/graph/cost.py)). The offline backend does no
  paid work, so it records a truthful `$0` — never an estimate.
- **Semantic cache**: a query within cosine ≥ 0.97 of a recent one *for the
  same tenant* is served from cache, flagged `cached: true` with the provider
  spend it avoided. Strictly per-tenant: the Redis key is the org id.
- **Idempotency**: `Idempotency-Key` on `POST /queries` stores the response for
  24h; a replay returns the identical events and does **not** run the query
  again.
- **Rate limits**: plan-tiered per-tenant and per-user token buckets, with API
  keys on their own bucket. Exceeding one returns `429` with `Retry-After`.

### Scheduled jobs

The API deliberately does not run these inline:

```bash
uv run python -m scripts.jobs deliver-webhooks   # every minute — drains the delivery queue with backoff
uv run python -m scripts.jobs living-answers     # nightly — re-checks followed answers, versions material changes
uv run python -m scripts.jobs corpus-freshness   # weekly — re-queries each domain (needs network)
uv run python -m scripts.jobs rebuild-mesh       # after an ingest — rebuilds the autocomplete vocabulary
```

### Typed client

The OpenAPI schema is generated from the live app, so it cannot drift from the
routes; the frontend's types are generated from it, which turns a breaking API
change into a TypeScript error:

```bash
uv run python -m scripts.export_openapi          # backend/ → docs/openapi.json
uv run python -m scripts.export_openapi --check  # CI: fail if stale
pnpm gen:api                                     # frontend/ → lib/api-types.ts
```

## The frontend

Next.js 15 (App Router) + React 19 + Tailwind v4 + shadcn (Base UI), typed
against the OpenAPI spec (`pnpm gen:api`) with `@typescript-eslint/no-explicit-any`
as an error and `tsc --noEmit` clean.

**Design system.** `frontend/app/globals.css` defines a restrained clinical
palette — one desaturated blue for actions, neutral surfaces — and a small set
of *semantic* scales used identically everywhere: confidence (high / moderate /
low), evidence grade (A–D), stance (supports / opposes / neutral), and the
guardrail states (calm for PHI and scope, unmissable for red flags). Every badge
carries its label; colour reinforces, never carries, meaning. Light and dark
are full token sets (`next-themes`, follows `prefers-color-scheme`, persisted).

**Screens.** Ask (streaming reasoning steps, the answer growing token by
token, inline `[n]` chips), the citation panel (the passage with the
supporting sentence highlighted, journal / year / design / grade, PubMed and
DOI links, `[`/`]`/Esc keyboard navigation), contradiction / abstention /
guardrail views, sessions (multi-turn threads), history with the full trace,
the library and document pages, Evidence Binders (highlight-and-annotate with
threaded replies, present mode, PDF), notifications and the Living Answer
diff, the owner/clinician dashboard (Recharts, every number from
`/analytics`), and Admin (members, private-corpus upload, sharing policy,
API keys, plan).

**Signature pieces.** ⌘K / Ctrl+K command palette (`cmdk`) reaches every
screen and live-searches sessions and the library; the Evidence Timeline plots
cited sources by year, coloured by stance and sized by grade, and draws a band
where the dominant recommendation flipped; comparison mode renders the
entity × outcome table with honest "insufficient evidence" cells; the PICO
builder; export to PDF (client-side, `@react-pdf/renderer`), Vancouver / AMA
to the clipboard, BibTeX / RIS downloads; read-only public permalinks at
`/a/{slug}` with a superseded banner, killable per-link or org-wide (clean
404, never an error page); an installable PWA (`@serwist/next`) that keeps
history, sessions and binders readable offline and badges the state.

**Latency honesty.** Every answer footer shows the recorded latency, token
counts, cost, cache status, and the model + prompt versions that produced it —
the offline backend reports a truthful $0.

Run it: `pnpm -C frontend dev --port 3005` (dev, no service worker) or
`pnpm -C frontend build && pnpm -C frontend start --port 3005` (production,
service worker on). The demo queries that exercise the honesty features
against the seeded corpus: *"Should aspirin be used for primary prevention of
cardiovascular disease?"* (sources disagree), *"Should antidepressants be used
to treat bipolar depression?"* (an 8-year timeline with a recommendation flip;
the system abstains, correctly), and *"what about in pregnancy?"* as a
follow-up to any answer (multi-turn contextualisation).

## The voice agent

Phase 11 puts a hands-free interface on top of the same guardrails, graph and
API: speech-to-text → guardrails → retrieval graph → text-to-speech, over one
WebSocket per session (`/api/v1/voice/ws`). The design note — why a cascade
and not a speech-to-speech model, and where the latency comes back — is in
[`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).

Two backends, one pipeline:

- `VOICE_BACKEND=offline` (default) — faster-whisper for STT and the operating
  system's synthesizer for TTS (Windows OneCore voices, including en-IN Heera
  and Ravi; `espeak-ng` on Linux). Real engines, no accounts:
  `uv sync --group voice`.
- `VOICE_BACKEND=cloud` — Deepgram `nova-3-medical` (interim results, corpus
  keyterm boosting) and ElevenLabs Flash / Multilingual (a per-org setting)
  over pre-warmed WebSockets. Needs `DEEPGRAM_API_KEY` and
  `ELEVENLABS_API_KEY`.

What the pipeline does that a demo does not: a layered endpoint decision
(acoustic → semantic completeness of a *fresh* partial → 2 s ceiling, with a
commit retracted when the final transcript turns out to be incomplete), a
corpus-derived boost vocabulary and post-STT medical-term correction, a
confirmation gate for ISMP look-alike/sound-alike drug names, speculative
retrieval on partials (hit and wasted rates charted), guardrails in parallel
with retrieval with no TTS byte before the verdict, answer-first voice
rendering (markers become "according to a 2023 meta-analysis in JAMA",
doses and p-values spoken correctly), progressive disclosure, a sentence
pipeline into streaming TTS with real-time pacing, client-side barge-in
measured in the AudioWorklet, "sorry, continue" resuming without
regeneration, resumable sessions, and a per-turn latency waterfall.

Screens: **Voice** (`/app/voice`) — live waveform, partials in grey and finals
in black with corrections marked, the agent transcript with citation chips
synchronized to playback, an unmissable state indicator, the LASA choice card,
and the waterfall per turn; the **Dashboard** aggregates p50/p95 per leg,
speculation economics, confirmations, barge-in stop time, masks and the cost
of interactivity; **Admin → Sharing & API** holds the Flash/Multilingual
setting; the session view shows each spoken turn's waterfall; voice and text
hand off in both directions with the thread intact.

The eval harness (`backend/evals/voice/`) renders 89 spoken fixtures with the
installed voices (golden questions in en-GB and en-IN, hesitation patterns
with real pauses, LASA drug names, the spoken adversarial set, backchannels,
interruptions, 10 dB noise variants), plays them through the real WebSocket
pipeline at real-time pace, and writes `evals/results/voice.json`
(medical-term error rate raw → corrected, endpoint latency, first-audio
percentiles per leg, barge-in stop time, speculation hit/wasted rates,
guardrail parity, LASA outcomes). The public `/methodology` page reads that
file live.

```bash
cd backend
uv sync --group voice
uv run python -m evals.voice.build_fixtures        # renders fixtures/ (Windows voices)
uv run python -m evals.voice.run --in-process      # local DB + local engines → results/voice.json
uv run python -m evals.voice.run --in-process --gate safety --baseline evals/results/voice.json
uv run python -m evals.voice.run --url wss://staging/api/v1/voice/ws --token $VOICE_EVAL_TOKEN
```

The gate has two levels: `safety` (PHI/diagnosis parity and LASA — fails the
build unconditionally) and `all` (adds the latency and quality targets). With
`--baseline`, a p95 first-audio regression above 15% fails too.

## Quality gates

Run what CI runs:

```bash
# Backend (from backend/)
uv run ruff check .
uv run ruff format --check .
uv run mypy --strict app scripts evals
uv run pytest -q                    # includes the RLS tenant-isolation suite
uv run pytest tests/test_rls.py -v  # the isolation proof on its own
uv run python -m scripts.export_openapi --check   # the committed schema is current
uv run python -m evals.voice.run --in-process --gate safety   # spoken guardrail parity
uv run python -m evals.golden.run --gate safety --no-judge      # golden set + adversarial set
uv run python -m evals.load.run --users 10 --duration 20 --ramp ''  # load (needs the API up)

# Frontend (from frontend/)
pnpm lint
pnpm exec tsc --noEmit
pnpm build
pnpm exec playwright test        # 30 end-to-end specs against the real stack

# Everything at once, with a per-phase report (from backend/)
uv run python -m scripts.verify --with-e2e     # writes docs/VERIFICATION.md
```

The integration suites (`test_rls`, `test_ask_flow`, `test_phase9_*`) need the
compose stack up: they resolve `TEST_DATABASE_URL` / `TEST_REDIS_URL` from your
`.env` and **skip** when either is unset, so a bare checkout still runs green.

```bash
docker compose up -d --wait
```

## The evaluation harness

The artifact the rest stands on (Phase 12). `backend/evals/golden/set.jsonl`
holds 165 clinical questions with expert-derived ground truth — relevant
documents from PubMed's MeSH indexing gated on each paper's own title and
conclusion, relevant passages from structured-abstract conclusions, the
authors' conclusion as the reference answer, expected evidence grade, whether
a contradiction is expected, and a set of uncovered topics where abstaining
is the right answer. It is built from the corpus by `evals.golden.build`, and
grows from real usage: a thumbs-down marked *wrong* or *unsupported* is queued
for review (owners see the queue on the dashboard) and a reviewer promotes it
with `python -m evals.golden.promote`.

```bash
uv run python -m evals.golden.run             # retrieval + generation + calibration + safety
uv run python -m evals.ablation.full          # the ten-configuration table
uv run python -m evals.golden.calibrate       # reliability curves + retune recommendation
```

Retrieval (recall@k, precision@k, MRR, nDCG@10) is scored on its own before
generation is scored (RAGAS + a clinical rubric with an LLM judge when keyed;
deterministic signals always), so a regression is placed in the layer it came
from. The ten-row ablation is the production pipeline with stages switched off
one at a time. Everything lands in `backend/evals/results/*.json` and is read
live by the methodology page; see [backend/evals/README.md](backend/evals/README.md)
for what each metric means and what the first full run found.

**CI gate.** The `golden-gate` job loads a corpus snapshot into its Postgres,
embeds it locally, and runs the golden set on every pull request: recall@10 or
faithfulness more than two points below the committed baseline fails the
build, and any PHI or diagnosis-refusal miss fails it unconditionally. A pull
request that deliberately switched the reranker off is the proof that the
gate rejects a regression — the failing check belongs here once the
repository has a remote to open it against.

## Observability, cost, and the public methodology page

Four signals, all inert until a key or endpoint is configured, except the cost
ledger — that one is our own table, so it always records. The whole picture is
in [docs/OBSERVABILITY.md](docs/OBSERVABILITY.md); the short version:

- **One query is one trace.** The browser's `ask.click` span parents the API
  request (W3C `traceparent`), which parents the graph nodes, the retrieval
  stages and the provider calls. Every span carries `request_id`, `tenant_id`,
  `query_id`, `user_id` and `channel` — a `SpanProcessor` copies the structlog
  contextvars onto each span, so a span created deep in retrieval is
  attributable without threading arguments through.
- **Sentry** on both tiers, tagged with the release and the same ids.
- **LangSmith** on every graph run, carrying the prompt versions it ran with;
  thumbs verdicts and golden-set scores attach to the run as feedback.
- **The cost ledger** (`public.cost_events`) records every billable provider
  call as it happens — embedding, rerank, generation, STT, TTS — with the
  units the provider bills and what the caches avoided.

```bash
docker compose --profile observability up -d jaeger   # OTLP 4318, UI 16686
uv run python -m evals.load.run --base-url http://127.0.0.1:8010   # 50 users, then a ramp
uv run python -m scripts.jobs weekly-digest                        # opt-in evidence digest
```

On the offline backend real spend is a truthful **$0**; the dashboard prices
the same units at the list price of the cloud provider each stand-in replaces
and labels that column a projection. The savings line is the measured one:
*semantic caching avoided $X this month*.

**[`/methodology`](http://localhost:3000/methodology)** is public and reads
every number live from `backend/evals/results/*.json` — the golden set, the
ablation table, safety, calibration, voice latency, the load test — with an
architecture diagram and a Limitations section that quotes the same files it
is limited by. Change an eval, re-run it, redeploy, and the page changes.
The marketing page runs one real query with no login
(`POST /api/public/demo`, allowlisted questions, rate-limited per visitor),
including one where the sources disagree.

## Repository layout

```
├── backend/
│   ├── app/
│   │   ├── main.py           # app factory + lifespan (DB pool, Redis)
│   │   ├── config.py         # Pydantic Settings — the only reader of env vars
│   │   ├── api/              # routes (call services, never the DB)
│   │   ├── services/         # business logic
│   │   ├── repositories/     # data access
│   │   ├── graph/            # LangGraph agent graph
│   │   ├── guardrails/       # PHI, refusal, red-flag, grounding
│   │   ├── retrieval/        # chunking, embedding, hybrid search, rerank
│   │   ├── ingestion/        # corpus pipeline
│   │   ├── schemas/          # Pydantic models
│   │   ├── core/             # errors, logging, deps
│   │   └── prompts/          # versioned prompt files
│   ├── migrations/           # numbered SQL files (no UI schema edits, ever)
│   ├── scripts/              # migrate, export_openapi, scheduled jobs
│   ├── evals/                # golden set, adversarial set, load test, runners
│   └── tests/
├── frontend/                 # Next.js 15 App Router
│   └── lib/api-types.ts      # generated from docs/openapi.json — do not edit
├── docs/
│   ├── openapi.json          # generated from the live app
│   ├── ARCHITECTURE.md       # decisions taken — and the ones rejected, with why
│   ├── EVAL.md               # methodology + results, regenerated from the runners
│   ├── OBSERVABILITY.md      # traces, Sentry, LangSmith, the cost ledger, load
│   ├── DEPLOY.md             # how it ships, drains, backs up, and restores
│   ├── VERIFICATION.md       # every phase gate, re-checked, with evidence
│   └── SAFETY.md
├── docker/                   # local-dev container init scripts
├── docker-compose.yml
└── .github/workflows/ci.yml  # lint → typecheck → test → build
```

## Build phases

| Phase | Scope | Status |
|-------|-------|--------|
| 1 | Foundation: config, logging, errors, health, CI, compose | ✅ |
| 2–3 | Schema & tenancy: migrations, RLS isolation, Supabase auth, RBAC | ✅ |
| 4 | Corpus ingestion + chunking strategies (with the ablation) | ✅ |
| 5–6 | Hybrid retrieval (BM25 + dense + fusion + rerank) and the evaluation harness | ✅ |
| 7 | Guardrails: PHI, scope, red-flag, grounding | ✅ |
| 8 | The agent graph: self-correction, contradiction, abstention | ✅ |
| 9 | The API layer: streaming, semantic cache, idempotency, API keys, rate limits, cost, comparison mode, batch, webhooks, Living Answers | ✅ |
| 10 | The frontend: design system, streaming Ask, citation panel, contradiction/abstention/guardrail views, sessions, library, binders, dashboard, admin, ⌘K, Evidence Timeline, comparison table, PICO, export, permalinks, Living Answer diff, dark mode, PWA | ✅ |
| 11 | The voice agent (11A–11H): WebSocket transport + AudioWorklets + resumable sessions, medical-grade STT with corpus boosting, correction and the LASA gate, layered endpointing, speculative orchestration with parallel guardrails, voice rendering, streaming TTS with barge-in, the voice UI and waterfall, the spoken eval harness and CI gate | ✅ |
| 12 | The evaluation harness: the golden set (165 items, expert-derived ground truth), retrieval metrics scored apart from generation, RAGAS + clinical rubric behind an LLM-judge protocol, the ten-configuration ablation, confidence calibration with a documented retuning loop, the adversarial and voice suites as siblings, the feedback → golden-set loop, and the CI gate on a corpus snapshot | ✅ |
| 13 | Observability and the public surface: OpenTelemetry traces browser→API→graph→retrieval→providers, Sentry and LangSmith wiring, the per-tenant cost ledger with explicit cache savings, the Locust load test, the public methodology page (architecture diagram, live eval tables, load results, honest limitations), the no-login demo query, and the opt-in weekly evidence digest | ✅ |
| 14 | Verification and hardening: every phase gate re-proven by a runner (`scripts/verify.py` → [docs/VERIFICATION.md](docs/VERIFICATION.md)), 30 Playwright end-to-end specs including voice with a real microphone, adversarial tenant-isolation tests, security headers and CSP, pgbouncer-safe pooling, a tuned HNSW search path, a tested backup/restore drill, and the deploy pipeline | ✅ |
| 14.5 | Ship it: Fly + Vercel + Supabase cloud, a domain, a status page | ⏸ needs accounts |
