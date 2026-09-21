# Deployment and operations

What has to be true before this serves a clinician, how to put it there, and
what to do when something goes wrong.

> **Status.** Everything in this document is written and tested as far as a
> machine without vendor accounts can test it: the image builds, the
> migrations apply to an empty database, the restore drill restores, the
> pipeline is wired. Nothing has been deployed — Fly, Vercel, Supabase cloud,
> the domain and the provider keys all need accounts, which is the one thing
> this build stops and asks for rather than inventing.

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

## Deploying

```bash
fly launch --no-deploy --copy-config      # once
fly secrets set DATABASE_URL=... REDIS_URL=... SUPABASE_URL=... ...
fly deploy                                # API + worker, rolling
```

The frontend deploys from Vercel's GitHub integration, or the same commands
the workflow runs. `.github/workflows/deploy.yml` is the real path:

```
merge to main → CI (lint, types, tests, golden-set eval gate)
              → deploy staging      (migrations, API+worker, frontend)
              → smoke test staging  (ready, headers, a real demo answer, live evals)
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
