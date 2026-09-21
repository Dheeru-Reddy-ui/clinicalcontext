# Deployment and operations

What has to be true before this serves a clinician, how to put it there, and
what to do when something goes wrong.

> **Status.** Nothing is deployed: Fly, Vercel, Supabase cloud, the domain
> and the provider keys need accounts, which is the one thing this build stops
> and asks for rather than inventing. Everything up to that point has been
> *run*, not just written:
>
> | Checked | Result |
> |---|---|
> | `docker build -f backend/Dockerfile .` | builds, 1.36 GB |
> | The image against real Postgres and Redis | `/ready` ok, both dependencies healthy |
> | Security headers in `ENVIRONMENT=production` | all six, including HSTS |
> | The pipeline's smoke test, verbatim | passes — demo answered with 6 citations, live evals served |
> | The worker process in the image | all three jobs ran on their schedules |
> | SIGTERM with a request in flight | answer finished with 200, then exit 0 |
> | Every migration against an empty database | 23 applied, 34 tables, 83 policies, none without RLS |
> | `scripts.maintenance restore-drill` | 34.7 MB dump restored and verified |
> | `flyctl config validate` | **not run — needs your login** (do this first) |

## The shape of it

| Piece | Where | Why there |
|---|---|---|
| API (`uvicorn`) | Fly.io, process `api` | Needs a long-lived process for SSE and the voice WebSocket, which serverless does not do well. |
| Scheduled jobs | Fly.io, process `worker` | Same image, separate process: a nightly sweep must not compete with a clinician's request for the event loop. |
| Frontend | Vercel | Next.js App Router, ISR and edge middleware, deployed by its authors' platform. |
| Postgres + pgvector | Supabase | RLS is the tenant boundary; it is a Postgres feature, and Supabase gives it with auth attached. |
| Redis | Upstash | Rate limits, the semantic cache, idempotency keys — all ephemeral, all fine to lose. |
| Traces | any OTLP collector | The app speaks OTLP; Jaeger locally, a vendor in deploy. |
| Errors | Sentry | Frontend and backend, tagged with the release and the request id. |

## Before the first deploy

1. **Secrets go to the platform's secret store, never to a file on a
   server.** `fly secrets set KEY=value` for the API and worker, Vercel's
   environment variables for the frontend. The application reads them as
   environment variables; `.env` exists for local development only, and is
   gitignored. `gitleaks` runs in verification against the whole tree
   (`.gitleaks.toml`) and fails on anything that looks like a credential.

2. **Use the right Postgres URL.** Migrations need the **direct** connection
   (port 5432) — the transaction pooler cannot run them. The application
   should use the **transaction pooler** (port 6543) so many machines share
   few server connections. The app detects the pooler from the DSN and turns
   off asyncpg's prepared-statement cache; without that, concurrency produces
   `prepared statement _pg1 already exists` under load and nowhere else
   (`app/config.py`, `db_uses_transaction_pooler`).

3. **Apply migrations before the code that needs them.** The deploy workflow
   runs `scripts.migrate` first. Migrations are forward-only and checksummed;
   an edited migration that has already been applied fails the run rather
   than silently diverging.

4. **Set the pool size for the machine count.** `DB_POOL_MAX_SIZE` × machines
   must stay under the pooler's client limit. The defaults (2–10 per machine)
   suit a small deployment.

5. **Index parameters.** The HNSW index is built `m=16, ef_construction=64`,
   which suits a corpus up to about a million vectors; `hnsw.ef_search` is set
   per connection from `HNSW_EF_SEARCH` (default 100) and **must not be below
   the candidate count** the retrieval pipeline asks for — the tenant filter
   is applied after the index, so a low value silently returns fewer rows.
   `uv run python -m scripts.maintenance analyze` runs `VACUUM ANALYZE` and
   prints the index size and settings.

## First deploy, step by step

You create the accounts and set the secrets — I never see their values, and
nothing below asks you to paste one into a chat. Each step says what to
check before moving on.

### 1. Accounts (about 20 minutes)

| Service | What to create | What you need from it |
|---|---|---|
| [Supabase](https://supabase.com) | Two projects: `clinicalcontext-staging`, `clinicalcontext` | Project URL, anon key, service-role key, and **both** database URLs (direct :5432 for migrations, pooler :6543 for the app) |
| [Fly.io](https://fly.io) | An account (a card is required even on the free allowance) | `flyctl` logged in |
| [Vercel](https://vercel.com) | Import the repo, root directory `frontend` | Project ID, org ID, a token |
| [Upstash](https://upstash.com) | Two Redis databases, staging and production | `rediss://` URLs |
| A registrar | A domain, or use the free `.fly.dev` and `.vercel.app` hosts to start | — |

Vendor keys, all optional to start — without them the deployment runs the
offline backend and the numbers stay the ones in this repository:
[Cohere](https://dashboard.cohere.com/api-keys),
[Anthropic](https://console.anthropic.com),
[Deepgram](https://console.deepgram.com),
[ElevenLabs](https://elevenlabs.io),
[Sentry](https://sentry.io), [LangSmith](https://smith.langchain.com).

### 2. Database

```bash
# Direct connection (port 5432), not the pooler — migrations need it.
export DATABASE_URL='postgresql://postgres:...@db.<ref>.supabase.co:5432/postgres'
uv --directory backend run python -m scripts.migrate
```

Check: it prints `applied: 001_extensions.sql` … `023_per_tenant_document_dedup.sql`
and nothing else. Re-running prints only `skipped (already applied)`.

Then load a corpus — without one the app answers nothing:

```bash
# The demo corpus, straight from PubMed (respects NCBI rate limits).
uv --directory backend run python -m app.ingestion.seed --per-query 600

# Or the CI-sized snapshot, if you want something small and deterministic.
uv --directory backend run python -m evals.golden.snapshot load --embedder local
```

### 3. Secrets

```bash
fly launch --no-deploy --copy-config --name clinicalcontext-api
fly secrets set \
  DATABASE_URL='postgresql://postgres:...@...pooler.supabase.com:6543/postgres' \
  REDIS_URL='rediss://...' \
  SUPABASE_URL='https://<ref>.supabase.co' \
  SUPABASE_ANON_KEY='...' \
  SUPABASE_SERVICE_ROLE_KEY='...' \
  RELEASE="$(git rev-parse --short HEAD)"
```

Note the **pooler** URL here and the **direct** URL in step 2 — the app
detects the pooler from the DSN and turns off asyncpg's statement cache.

In Vercel's project settings: `NEXT_PUBLIC_API_URL`,
`NEXT_PUBLIC_SUPABASE_URL`, `NEXT_PUBLIC_SUPABASE_ANON_KEY`,
`NEXT_PUBLIC_RELEASE`.

In the GitHub repository's secrets (for the pipeline): `FLY_API_TOKEN`,
`FLY_API_TOKEN_STAGING`, `VERCEL_TOKEN`, `VERCEL_ORG_ID`,
`VERCEL_PROJECT_ID`, `STAGING_DATABASE_URL`, `PRODUCTION_DATABASE_URL`,
`STAGING_SUPABASE_URL`, `STAGING_SUPABASE_ANON_KEY`,
`STAGING_SUPABASE_SERVICE_ROLE_KEY`.

### 4. Deploy

```bash
fly config validate             # the one check that needs your login
fly deploy                      # API + worker
```

The image itself has already been built and run against a real Postgres and
Redis (see Status above), so a failure here is about the account, the
secrets or the region — not the Dockerfile.

Then in Supabase → Authentication → URL configuration, set the site URL to
the Vercel domain and add it to the redirect allow-list, or magic links will
bounce.

### 5. Tell me it is up

Give me the two URLs and I will verify the deployment rather than assume it:

- `/ready` returns ok and names Postgres and Redis
- the security headers are present, including HSTS (which is off locally by
  design)
- the public demo answers a real question, with citations
- `/methodology` serves the eval numbers from the deployed files
- the end-to-end suite runs against the deployment
  (`E2E_BASE_URL=… E2E_API_URL=… DATABASE_URL=… pnpm exec playwright test --project=app`)
- one query produces one trace, end to end, if you have added an OTLP endpoint

If something fails I will tell you what failed and what it means, not that it
went fine.

## The pipeline

Step 4 above is the manual path. `.github/workflows/deploy.yml` is the one
that runs every time:

```
merge to main → CI (lint, types, tests, secret scan, golden-set eval gate)
              → deploy staging      (migrations, API+worker, frontend)
              → smoke test staging  (ready, headers, a real demo answer, live evals)
              → end-to-end suite against staging
              → manual promote      (a GitHub Environment with a required reviewer)
              → production          (migrations, API+worker, frontend, smoke)
```

The eval gate is what makes this different from a normal pipeline: a pull
request that costs more than two points of recall@10 or faithfulness fails,
and any PHI or diagnosis-refusal miss fails unconditionally. A regression in
answer quality cannot reach staging, let alone production.

## Health, drain, and restart

- `/health` — the process is alive. Container healthcheck.
- `/ready` — Postgres and Redis answered. Platform readiness probe; a machine
  that fails it is taken out of rotation rather than restarted.
- **Draining.** `uvicorn --timeout-graceful-shutdown 60` stops accepting
  connections on SIGTERM and finishes what it has; a streamed answer can take
  tens of seconds. Fly's `kill_timeout = "90s"` is deliberately longer, so a
  deploy never cuts an answer in half.
- The voice WebSocket registry closes its sessions in the lifespan shutdown,
  so a redeploy ends voice sessions cleanly instead of dropping sockets.

## Logs, traces, errors

Logs are structured JSON on stdout, one object per line, every line carrying
`request_id` and — after authentication — `tenant_id`. Any platform log drain
that parses JSON makes them queryable; Fly ships to its own log store and can
forward to a vendor. The same ids are on the OpenTelemetry spans and on Sentry
events, so one id moves between all three. See
[OBSERVABILITY.md](OBSERVABILITY.md).

## Backups

Supabase takes automated backups; that is necessary and not sufficient,
because an untested backup is a hope. The drill is a command:

```bash
uv run python -m scripts.maintenance restore-drill
```

It dumps the live database, restores it into a throwaway database, and
compares the restored copy against the counts taken before and after the dump
(a live system moves under a snapshot, so the check is that the restore falls
inside that window). It also checks the RLS policy count survived and that a
document reads back, then drops the scratch database.

Run on 2026-09-20 against the development database: a 34.7 MB dump, 9,997
documents, 44,143 chunks and their embeddings, 83 RLS policies — all restored,
verified, dropped. **PASSED.** Run it against production monthly and after any
migration that rewrites data.

Restoring for real is the same command's first half plus a target:

```bash
uv run python -m scripts.maintenance backup --out ./restore-me.dump
pg_restore --no-owner --no-privileges -d "$TARGET_DATABASE_URL" ./restore-me.dump
```

## What is still open

- **Accounts.** Fly, Vercel, Supabase cloud, Upstash, the domain, and the
  vendor keys (Cohere, Anthropic, Deepgram, ElevenLabs, Sentry, LangSmith).
  Every code path that uses them is written and inert without them.
- **A status page.** The probes exist (`/health`, `/ready`) and the
  methodology page already publishes the eval numbers; a public status page
  needs a host to point at the probes.
- **The two voice gate checks** that the offline speech stack cannot meet
  (medical-term error rate, endpoint latency). They are what Deepgram
  `nova-3-medical` is for; see [VERIFICATION.md](VERIFICATION.md).
