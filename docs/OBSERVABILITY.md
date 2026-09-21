# Observability, cost, and what a deployment can see (Phase 13)

Four signals, each answering a different question about the same request:

| Signal | Answers | Where it goes |
|---|---|---|
| **OpenTelemetry traces** | *Where did the time go, and which providers did this query call?* | any OTLP collector (Jaeger locally) |
| **Sentry** | *What broke, in which release, for which request?* | Sentry (frontend + backend) |
| **LangSmith** | *What did the graph actually do, with which prompt versions — and how did it score?* | LangSmith |
| **The cost ledger** | *What did this tenant spend, per component, and what did the caches avoid?* | `public.cost_events` in Postgres |

Every one of them is off by default and inert without a key or endpoint, so
the repository runs the same locally with nothing configured. The cost ledger
is the exception: it is our own table, so it always records.

## One query is one trace

The trace starts in the browser and ends at the provider calls. The browser's
`ask.click` span is the parent; `lib/telemetry.ts` injects a W3C `traceparent`
onto the request, FastAPI continues it, and the graph nodes and retrieval
stages nest under the server span.

A real trace from a development run (`5734ce0c1328b597cd927c1a560d149a`,
176 spans, offline backend):

```
clinicalcontext-web   ask.click                          92.1 ms
  clinicalcontext-web   POST                            4824.0 ms
    clinicalcontext-api   POST /api/v1/queries          4798.4 ms   http.status_code=200
      graph.classify_query                                 1.0 ms
      graph.retrieve                                     203.8 ms   graph.retrieved=8
        retrieval.embed_query                              2.0 ms   embedder=hashing-lexical
        retrieval.dense                                  161.2 ms   → SELECT 158.2 ms (pgvector)
        retrieval.lexical                                 41.3 ms   → WITH 32.8 ms (BM25)
        retrieval.rerank                                   7.0 ms   reranker=bm25-local candidates=50
      graph.grade_retrieval                                1.0 ms
      graph.detect_contradiction                           1.0 ms
      graph.generate                                       1.0 ms
      graph.verify_grounding                               2.0 ms
      graph.assess_confidence                              0.0 ms
      … 39 SQL spans (asyncpg instrumentation)
```

The wall-clock difference between the 4.8 s request and ~210 ms of graph work
is the SSE stream: the endpoint holds the response open while the answer is
tokenized to the client. On the cloud backend the same tree gains
`provider.cohere.embed`, `provider.cohere.rerank` and
`provider.anthropic.messages` spans — those are instrumented at the provider
call sites (`app/retrieval/embedders.py`, `app/retrieval/rerank.py`,
`app/graph/reasoner.py`), so the trace shows the vendor call itself, not just
the stage that made it.

**Every span carries the ids a log line carries** — `request_id`, `tenant_id`,
`query_id`, `user_id`, `channel` (`text` or `voice`). That is
`ContextAttributes`, a `SpanProcessor` in `app/core/telemetry.py` that copies
the structlog contextvars onto each span as it starts, so a span created deep
in retrieval is attributable without threading arguments through. Voice turns
bind the same context in `app/voice/turn.py`, with `channel=voice`.

### Running it locally

```bash
docker compose --profile observability up -d jaeger   # OTLP/HTTP on 4318, UI on 16686
```

then in `.env` (backend) and `frontend/.env.local`:

```
OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4318
NEXT_PUBLIC_OTEL_EXPORTER_URL=http://localhost:4318
```

Open <http://localhost:16686>, pick service `clinicalcontext-web`, operation
`ask.click`, and one click in the UI is one trace through to the SQL.
`OTEL_EXPORTER_OTLP_ENDPOINT=console` prints spans instead, which is enough to
check instrumentation without a collector. With neither set, tracing is off
and costs nothing.

## Sentry

`SENTRY_DSN` (backend) and `NEXT_PUBLIC_SENTRY_DSN` (frontend) turn it on;
both tag events with the `RELEASE` and, via `sentry_before_send`, with
`request_id`, `tenant_id`, `query_id` and the current `trace_id` — so an error
report links back to the trace above. Without a DSN the SDK is not
initialised. Source-map upload is enabled only when `SENTRY_AUTH_TOKEN` is
present.

## LangSmith

`LANGSMITH_API_KEY` (a placeholder is ignored) enables LangChain tracing for
every graph run. Each run carries, as metadata, the **prompt versions it ran
with**, the release and the AI backend (`app/graph/tracing.py`), and each run
id is persisted on the answer (`answers.langsmith_run_id`). Scores attach to
that run as feedback:

- a thumbs up/down from the UI → `thumbs_up`
- the golden-set runner → `golden.recall_at_10`, `golden.cited_gold`,
  `golden.abstained_correctly`, `golden.grounding_support`

so a run can be read next to the score it earned, against the exact prompts
that produced it. Everything here no-ops without the key.

## The cost ledger

`public.cost_events` (migration 021) records **every billable provider call as
it happens**: component (`embedding`, `rerank`, `generation`, `stt`, `tts`),
provider, model, the units the provider bills (tokens, searches, audio
seconds, characters), the cost at the published list price, and whether a
cache avoided it.

The mechanism is a contextvar collector (`app/services/cost.py`): a request
opens `cost.collecting()`, providers inside call `cost.record(...)`, and the
request flushes the events with its `org_id`/`query_id` (a voice turn adds its
`voice_session_id`). Concurrent requests never see each other's events, and a
provider called outside a request — an eval, a backfill — records nothing.

Two caches write **avoided** units rather than spend:

- the embedding cache records the tokens it did not send;
- the semantic answer cache stores the rerank and generation events of the
  answer it cached and replays them on a hit (the hit still embeds the query,
  so that one is never counted as avoided).

Speculative voice retrieval belongs to the turn that resolves it, used or
wasted — waste that is not recorded is waste that is not managed.

**Offline is $0, and the projection says so.** The local stand-ins genuinely
cost nothing, so `cost_usd` is `0.000000` and the dashboard instead prices the
same units at the list price of the cloud provider each one replaces
(`CLOUD_EQUIVALENT`), labelled *projected at cloud list price*. List prices
and the date they were checked live in one place, `LIST_PRICES` /
`PRICE_CHECKED`.

`GET /api/v1/analytics/cost?days=30` returns spend per component, per
(provider, model) lines, the daily series, and the cache savings split by
cache — including the calendar-month figure the dashboard states as
"semantic caching avoided $X this month". The dashboard card is
`frontend/components/dashboard/cost-card.tsx`.

## Load

`python -m evals.load.run` drives a running API with Locust: 50 concurrent
simulated clinicians for 60 s, then a ramp until the error rate or p95 gives
out. Answers served by the semantic cache are recorded apart from answers the
pipeline produced, so a cache cannot flatter the pipeline's numbers. The
result is written to `backend/evals/results/load.json` with the machine and
backend that produced it, and read live by the methodology page.

```bash
uv run python -m evals.load.run --base-url http://127.0.0.1:8010
uv run python -m evals.load.run --users 50 --duration 60 --ramp 25,50,100 --stage-seconds 30
```

The runner seeds and removes its own enterprise-plan tenant (in a subprocess:
Locust monkey-patches the standard library for gevent, which asyncpg does not
survive — and for the same reason `evals/load/run.py` imports Locust before
anything that imports `ssl`).

## The public surface

`/methodology` reads every number live from `backend/evals/results/*.json`
through `GET /api/public/evals/{name}` — golden set, ablation, calibration,
voice, load — and says so when a file has not been produced. Change an eval,
re-run it, redeploy, and the page changes; nothing on it is typed in.

`POST /api/public/demo` answers one of a few allowlisted questions with the
real pipeline as a fixed demo tenant (migration 022), rate-limited per
visitor and in total. Free text is not accepted: the marketing page is not an
unauthenticated query endpoint.

## The weekly digest

`python -m scripts.jobs weekly-digest` sends opted-in users one notification
(and an email copy when they asked for one) with new evidence in the topics
they have actually asked about — the MeSH headings of the documents their
answers cited, minus the check tags — plus Living Answer changes and their
organisation's week. Nothing is sent in a week with nothing to say. Locally,
`SMTP_HOST=127.0.0.1` / `SMTP_PORT=54325` points at Supabase's mailpit
(<http://127.0.0.1:54324> for the inbox); that port has to be uncommented as
`smtp_port` under `[local_smtp]` in `supabase/config.toml`.
