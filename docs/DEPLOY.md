# Deployment and operations

What has to be true before this serves a clinician, how to put it there, and
what to do when something goes wrong.

> **Status.** Nothing is deployed: Render, Vercel, Supabase, Upstash and
> GitHub need accounts — all free, none needing a card — which is the one
> thing this build stops and asks for rather than inventing. Everything up
> to that point has been *run*, not just written:
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
> | The image under a hard **512 MB** limit (Render's free tier) | 123 MB idle, 214 MB after four uncached answers |
> | The image run as Render runs it (`PORT`, `RENDER_GIT_COMMIT`) | binds the port, `/health` reports the commit, uvicorn is PID 1 |
> | `render.yaml` against Render's published JSON schema | valid |
> | SIGTERM with a request in flight, in-process webhook drain running | answer finished with 200, drain stopped, exit 0 |

## The shape of it

| Piece | Where | Why there |
|---|---|---|
| API (`uvicorn`) | Render, free web service (`render.yaml`) | Needs a long-lived process for SSE and the voice WebSocket, which serverless does not do well. Free, no card; sleeps after 15 idle minutes. Fly.io (`fly.toml`) is the paid alternative with always-on machines. |
| Webhook delivery | inside the API (`WEBHOOK_DRAIN_INTERVAL_SECONDS`) | Deliveries are queued by requests, so the API is awake whenever there is work — the right design for a host that sleeps, not a compromise. Off when a worker process exists. |
| Scheduled jobs | GitHub Actions, `.github/workflows/jobs.yml` | Nightly Living Answers, weekly digest and freshness: about 35 short runs a month, idempotent, free. On Fly, the `worker` process instead. |
| Frontend | Vercel | Next.js App Router, ISR and edge middleware, deployed by its authors' platform. |
| Postgres + pgvector | Supabase | RLS is the tenant boundary; it is a Postgres feature, and Supabase gives it with auth attached. |
| Redis | Upstash | Rate limits, the semantic cache, idempotency keys — all ephemeral, all fine to lose. |
| Traces | any OTLP collector | The app speaks OTLP; Jaeger locally, a vendor in deploy. |
| Errors | Sentry | Frontend and backend, tagged with the release and the request id. |

## Before the first deploy

1. **Secrets go to the platform's secret store, never to a file on a
   server.** Render's environment (the `sync: false` entries in
   `render.yaml`), or `fly secrets set` on Fly; Vercel's environment
   variables for the frontend; GitHub's repository secrets for the pipeline
   and the jobs. The application reads them as environment variables; `.env`
   exists for local development only, and is gitignored. `gitleaks` runs in
   verification against the whole tree (`.gitleaks.toml`) and fails on
   anything that looks like a credential.

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

4. **Set the pool size for the instance count.** `DB_POOL_MAX_SIZE` ×
   instances must stay under the pooler's client limit. `render.yaml` uses
   1–5 for its single free instance; `fly.toml` 2–10 per machine.

5. **Mind the free database's size.** The full development corpus measures
   1,079 MB in Postgres — twice Supabase's free 500 MB, most of it vectors and
   their HNSW index. The golden snapshot (1,972 documents) is about 150 MB;
   a PubMed seed at `--per-query 150` about 275 MB.

6. **Index parameters.** The HNSW index is built `m=16, ef_construction=64`,
   which suits a corpus up to about a million vectors; `hnsw.ef_search` is set
   per connection from `HNSW_EF_SEARCH` (default 100) and **must not be below
   the candidate count** the retrieval pipeline asks for — the tenant filter
   is applied after the index, so a low value silently returns fewer rows.
   `uv run python -m scripts.maintenance analyze` runs `VACUUM ANALYZE` and
   prints the index size and settings.

## First deploy, step by step

The runbook is its own document: **[FIRST-DEPLOY.md](FIRST-DEPLOY.md)** — a
deployment that costs nothing and needs no card, in the order to do it, with
the check to run after each step and what a failure actually means. It also
says what $0 buys: an API that sleeps after fifteen idle minutes and takes a
minute to wake, offline answers, no voice, and a database that pauses after a
quiet week.

The short version, for someone who has done this before:

```bash
gh repo create clinicalcontext --private --source . --push          # Render builds from GitHub
uv --directory backend run python -m scripts.migrate --url "$DIRECT_URL"   # 5432, not the pooler
DATABASE_URL="$DIRECT_URL" uv --directory backend run python -m evals.golden.snapshot load --embedder local
# Render → New → Blueprint → the repo → enter the ten sync:false values → Manual Deploy
# Vercel → Add New → Project → root directory frontend → the four NEXT_PUBLIC_* variables → Deploy
# Supabase → Authentication → URL configuration → the Vercel origin
```

Three things that are not obvious and cost an hour each when missed:

- **All ten secrets are required at boot**, including the provider keys an
  offline deployment never calls. The configuration validates as a whole on
  start rather than failing on the first request that needed a missing
  value; `placeholder` is a fine value for a key you are not using.
- **The corpus is tied to `AI_BACKEND`.** Offline embeds with the local
  hashing embedder, cloud with Cohere, both into 1536 dimensions — so
  embedding a corpus with one and querying it with the other returns noise
  rather than an error.
- **Set the Vercel environment variables before the first production build.**
  The Content-Security-Policy is derived from `NEXT_PUBLIC_API_URL`; the build
  fails without it, which is much better than shipping a policy that blocks
  every call to your own API.

Give me the two URLs afterwards and I will verify the deployment rather than
assume it: readiness, the security headers including HSTS, a real demo answer
with citations, the live eval numbers, the wake-up time of a sleeping
instance, and the end-to-end suite against it. If something fails I will tell
you what failed and what it means, not that it went fine.

## The pipeline

The runbook's first deploy is by hand. `.github/workflows/deploy.yml` is the
one that runs every time after that:

```
merge to main → CI (lint, types, tests, secret scan, golden-set eval gate)
              → a person approves   (a GitHub Environment with a required reviewer)
              → migrations          (direct URL, forward-only, checksummed)
              → the API             (Render's deploy hook; render.yaml has auto-deploy off)
              → wait                (until /health reports the new commit, not the old process)
              → the frontend        (Vercel)
              → smoke test          (ready, headers, a real demo answer, live evals, the page)
              → end-to-end suite    (opt-in: repository variable E2E_AFTER_DEPLOY=true)
```

One environment, because this is the free-tier pipeline; the manual gate
survives. A staging rung is the same job twice with a second set of accounts
(Supabase allows two free projects; Upstash one free database, so staging
would use Render's free Key Value store), and the earlier two-rung Fly
pipeline is in this file's git history if it is ever wanted back.

`/health` reporting the deployed commit is what makes the wait step honest:
Render stamps `RENDER_GIT_COMMIT` into the environment, the app adopts it as
its `release` (the same value on every trace, error and log line), and the
pipeline polls until the value it sees is the SHA it just merged.

The eval gate is what makes this different from a normal pipeline: a pull
request that costs more than two points of recall@10 or faithfulness fails,
and any PHI or diagnosis-refusal miss fails unconditionally. A regression in
answer quality cannot reach the deployment.

`.github/workflows/jobs.yml` is the worker: the nightly Living Answers sweep
and the weekly digest and corpus-freshness pass, on a cron, from the same
repository secrets. About 35 short runs a month.

## Health, drain, and restart

- `/health` — the process is alive. Container healthcheck.
- `/ready` — Postgres and Redis answered. Platform readiness probe; a machine
  that fails it is taken out of rotation rather than restarted.
- **Draining.** `uvicorn --timeout-graceful-shutdown 60` (the
  `GRACEFUL_SHUTDOWN_SECONDS` the container reads) stops accepting
  connections on SIGTERM and finishes what it has; a streamed answer can take
  tens of seconds. Render's `maxShutdownDelaySeconds: 90` and Fly's
  `kill_timeout = "90s"` are deliberately longer, so a deploy never cuts an
  answer in half. Verified by sending SIGTERM with a request in flight: 200,
  then exit 0.
- The voice WebSocket registry closes its sessions in the lifespan shutdown,
  so a redeploy ends voice sessions cleanly instead of dropping sockets.

## Logs, traces, errors

Logs are structured JSON on stdout, one object per line, every line carrying
`request_id` and — after authentication — `tenant_id`. Any platform log drain
that parses JSON makes them queryable; Render and Fly each keep their own log
store and can forward to a vendor. The same ids are on the OpenTelemetry spans and on Sentry
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

- **Accounts.** GitHub, Render, Vercel, Supabase, Upstash — all free, none
  needing a card — and optionally the vendor keys (Cohere, Anthropic,
  Deepgram, ElevenLabs, Sentry, LangSmith). Every code path that uses them is
  written and inert without them.
- **A status page.** The probes exist (`/health`, `/ready`) and the
  methodology page already publishes the eval numbers; a public status page
  needs a host to point at the probes.
- **The two voice gate checks** that the offline speech stack cannot meet
  (medical-term error rate, endpoint latency). They are what Deepgram
  `nova-3-medical` is for; see [VERIFICATION.md](VERIFICATION.md).
