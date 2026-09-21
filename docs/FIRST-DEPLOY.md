# First deploy, one step at a time

[DEPLOY.md](DEPLOY.md) is the reference: what runs where, and why. This is the
runbook — the commands in order, what each one should print, and what to do
when it prints something else.

**You create the accounts and set the secrets. I never see their values, and
nothing here asks you to paste one into a chat.** Commands are PowerShell,
because that is the shell you are in; they work in bash with `$env:X = 'y'`
changed to `export X='y'`.

Allow about ninety minutes, most of it waiting on a corpus.

---

## Before you start: two decisions

### 1. Offline answers, or cloud answers?

Both are real systems. The difference is which models run.

| | **Offline** (`AI_BACKEND=offline`) | **Cloud** (`AI_BACKEND=cloud`) |
|---|---|---|
| Paid keys | none | Cohere + Anthropic |
| Retrieval | local hashing embedder, BM25 reranker | Cohere embed-v4.0 + rerank-v3.5 |
| Generation | deterministic heuristic reasoner | Claude |
| Cost ledger | a truthful $0 | real per-query spend |
| Published eval numbers | the ones in this repository | re-run yours; they will differ |
| Voice | **not available in the deployed image** | needs Deepgram + ElevenLabs too |

Start offline if you want the deployment up today without a card at two more
vendors. Switching later is one `fly secrets set` plus re-embedding the corpus
(step 3 explains why the corpus is tied to this choice).

> Voice is the one feature that cannot run offline in the cloud: the container
> deliberately leaves out faster-whisper and its ONNX runtime (~2 GB). Without
> Deepgram and ElevenLabs keys the voice page fails when you open it, and
> everything else works. Nothing else in the app depends on it.

### 2. What it will cost

| Service | Free tier | What to watch |
|---|---|---|
| Fly.io | card required, small monthly allowance | `min_machines_running = 1` and a 1 GB API machine ≈ **a few dollars a month**. Set it to `0` in `fly.toml` to scale to zero and pay near nothing — the first request after idle then takes a few seconds. |
| Supabase | 2 free projects | **A free project pauses after 7 days with no traffic**, and a paused project makes the demo look broken. Either keep it warm or accept unpausing it by hand. |
| Upstash | free Redis | command-count limited; fine for this traffic. |
| Vercel | free hobby | fine. |
| Cohere / Anthropic | pay as you go | cloud track only. |

---

## Step 0 — The two CLIs (10 minutes)

```powershell
iwr https://fly.io/install.ps1 -useb | iex
fly auth login
npm i -g vercel
vercel login
```

The Fly installer prints the folder it installed into; add it to `PATH` if
`fly version` is not found in a new terminal.

**Check:** `fly version` and `vercel whoami` both answer.

---

## Step 1 — Accounts, and one file to hold the values (20 minutes)

Decide your two names first — everything else refers to them:

- Fly app name, e.g. `clinicalcontext-api` → the API lives at
  `https://<name>.fly.dev`
- Vercel project name, e.g. `clinicalcontext` → the app lives at
  `https://<name>.vercel.app`

| # | Service | Create | Collect |
|---|---|---|---|
| 1 | [Supabase](https://supabase.com) | one project (add a second, `-staging`, later if you want a staging rung) | Project URL, anon key, service-role key, **direct** DB URL (5432), **transaction pooler** DB URL (6543) |
| 2 | [Upstash](https://upstash.com) | one Redis database, same region as Fly | the `rediss://…` URL |
| 3 | [Fly.io](https://fly.io) | account (card required even on the free allowance) | nothing to copy |
| 4 | [Vercel](https://vercel.com) | nothing yet — step 6 creates the project | nothing to copy |

In Supabase the connection strings are under **Project Settings → Database →
Connection string**. You need two of the three shown:

- **Direct connection**, port 5432 — for migrations. If your network has no
  IPv6 this one may refuse to connect; use the **Session pooler** string
  (also port 5432) instead, which works the same way for migrations.
- **Transaction pooler**, port 6543 — for the running app. Not
  interchangeable: it cannot run migrations, and the app detects it from the
  DSN and turns off asyncpg's statement cache accordingly.

Cloud track only: [Cohere](https://dashboard.cohere.com/api-keys) and
[Anthropic](https://console.anthropic.com). Voice as well:
[Deepgram](https://console.deepgram.com), [ElevenLabs](https://elevenlabs.io).
Optional and free: [Sentry](https://sentry.io),
[LangSmith](https://smith.langchain.com).

### Put them in one file, not in your shell history

`.env.*` is gitignored, so this never reaches a commit:

```powershell
notepad backend/.env.deploy
```

```dotenv
# Not committed. Not pasted into a chat. Delete it when the deploy is done.
SUPABASE_URL=https://<ref>.supabase.co
SUPABASE_ANON_KEY=
SUPABASE_SERVICE_ROLE_KEY=
DIRECT_DATABASE_URL=postgresql://postgres:<password>@db.<ref>.supabase.co:5432/postgres
POOLED_DATABASE_URL=postgresql://postgres.<ref>:<password>@aws-0-<region>.pooler.supabase.com:6543/postgres
REDIS_URL=rediss://default:<token>@<name>.upstash.io:6379
# Cloud track only — leave blank to deploy offline.
COHERE_API_KEY=
ANTHROPIC_API_KEY=
DEEPGRAM_API_KEY=
ELEVENLABS_API_KEY=
```

Which of these are secret: every one with a password or a token in it — both
database URLs, the Redis URL, the service-role key, every provider key. The
project URL and the anon key are public by design (the anon key ships in the
browser bundle; RLS is what protects the data), but there is no reason to
circulate them either.

---

## Step 2 — Migrations (5 minutes)

Against the **direct** URL, never the transaction pooler:

```powershell
uv --directory backend run python -m scripts.migrate --url "<DIRECT_DATABASE_URL>"
```

**Check:** it prints `applied: 001_extensions.sql` through
`applied: 023_per_tenant_document_dedup.sql` — 23 files. Run it a second time
and every line says `skipped (already applied)`; that is the checksum ledger
working, not a failure.

Then, in the Supabase SQL editor, confirm the tenant boundary exists:

```sql
select count(*) from pg_policies where schemaname = 'public';
```

**Check:** 83. Those rows are the application's entire tenant isolation.

---

## Step 3 — A corpus (10 minutes, or a couple of hours)

Without documents the app answers nothing — correctly, by abstaining, which
looks exactly like a broken deployment.

**The rule that matters:** documents and questions must be embedded by the
*same* embedder. Offline uses a local hashing embedder; cloud uses Cohere.
Both write 1536-dimension vectors, so a mismatch does not raise an error — it
silently returns noise. Pick the row that matches your decision above:

| Your `AI_BACKEND` | Embed the corpus with |
|---|---|
| `offline` | `--embedder local` |
| `cloud` | `--embedder cohere` (needs `COHERE_API_KEY`, and costs a few dollars for the full corpus) |

Point the tooling at Supabase for these commands. Use the **direct** URL —
this writes a great many rows:

```powershell
$env:DATABASE_URL = "<DIRECT_DATABASE_URL>"
```

**Option A — the small deterministic snapshot (about 10 minutes).** The same
corpus the eval suite uses, so the published numbers describe what you
actually deployed:

```powershell
uv --directory backend run python -m evals.golden.snapshot load --embedder local
```

**Option B — the full seed, straight from PubMed (hours).** Seventeen queries
across four clinical domains, rate-limited politely:

```powershell
uv --directory backend run python -m app.ingestion.seed --per-query 600 --embedder local
```

`--embedder` defaults to whatever `AI_BACKEND` says in your local `.env`,
which is easy to get wrong from a deploy shell — pass it explicitly.

**Check**, in the Supabase SQL editor:

```sql
select
  (select count(*) from documents)        as documents,
  (select count(*) from chunks)           as chunks,
  (select count(*) from chunk_embeddings) as embeddings;
```

Embeddings must be non-zero and close to the chunk count. If `documents` is
large and `embeddings` is 0, the backfill was skipped — that is the Cohere key
missing on the cloud track. Re-run with `--embedder local`.

Then clear the variable, so a later command cannot point at production by
accident:

```powershell
Remove-Item Env:DATABASE_URL
```

---

## Step 4 — Fly: the app and its secrets (15 minutes)

### 4a. Edit the non-secret config

`fly.toml` at the repository root. Change these four, and `app` /
`primary_region` if you want something other than `clinicalcontext-api` in
London:

```toml
[env]
  AI_BACKEND = "offline"      # or "cloud"
  VOICE_BACKEND = "offline"   # "cloud" only if you have Deepgram + ElevenLabs
  CORS_ORIGINS = "https://<your-vercel-project>.vercel.app"
  APP_PUBLIC_URL = "https://<your-vercel-project>.vercel.app"
```

`CORS_ORIGINS` is an allow-list of browser origins, and it is the only thing
standing between your API and any other website's JavaScript. It has to be the
exact origin: scheme included, no trailing slash.

### 4b. Create the app

```powershell
fly launch --no-deploy --copy-config --name clinicalcontext-api
```

Say **no** to every offer to create a Postgres, create a Redis, or tweak the
configuration — you already have all three, and the config is deliberate.

### 4c. Set the secrets

All ten are required at boot: the backend validates its whole configuration on
start and refuses to run with one missing, rather than failing hours later on
the first call that needed it. On the offline track the four provider keys are
genuinely unused — set them to `placeholder` and they stay unused.

```powershell
fly secrets set `
  DATABASE_URL="<POOLED_DATABASE_URL>" `
  REDIS_URL="<REDIS_URL>" `
  SUPABASE_URL="<SUPABASE_URL>" `
  SUPABASE_ANON_KEY="<SUPABASE_ANON_KEY>" `
  SUPABASE_SERVICE_ROLE_KEY="<SUPABASE_SERVICE_ROLE_KEY>" `
  COHERE_API_KEY="placeholder" `
  ANTHROPIC_API_KEY="placeholder" `
  LANGSMITH_API_KEY="placeholder" `
  DEEPGRAM_API_KEY="placeholder" `
  ELEVENLABS_API_KEY="placeholder" `
  RELEASE="$(git rev-parse --short HEAD)"
```

Note the **pooled** URL here and the **direct** URL in step 2. Many machines
sharing few server connections is the point of the pooler; running migrations
through it is the thing it cannot do.

**Check:**

```powershell
fly config validate
fly secrets list
```

`fly secrets list` prints names and digests, never values. Ten names.

---

## Step 5 — Deploy the API (10 minutes)

```powershell
fly deploy
fly scale count api=1 worker=1
fly status
```

The image has already been built and run against a real Postgres and Redis on
this machine, so a failure here is about the account, the secrets or the
region — not the Dockerfile. `fly logs` is the first place to look.

**Check** — the API answers, and knows both its dependencies are alive:

```powershell
curl.exe -fsS https://clinicalcontext-api.fly.dev/ready
```

Expect JSON naming `postgres` and `redis`, both ok. A 503 means one of them is
unreachable: `/ready` is deliberately honest, and Fly keeps the machine out of
rotation until it is true.

That the security headers are there (a GET with the headers dumped — `-I`
sends a HEAD, which tells you less):

```powershell
curl.exe -fsS -D - -o NUL https://clinicalcontext-api.fly.dev/health
```

**Check:** `strict-transport-security` and `x-content-type-options: nosniff`
among them. HSTS appears only when `ENVIRONMENT` is staging or production,
which `fly.toml` sets — locally it is deliberately absent.

Then that the public demo can actually answer — one call that exercises
retrieval, generation and citations. The demo takes the **id** of one of four
curated questions (`GET /api/public/demo/questions` lists them), not free
text, so that an unauthenticated endpoint cannot be used as a free model:

```powershell
curl.exe -fsS -X POST https://clinicalcontext-api.fly.dev/api/public/demo -H "content-type: application/json" -d "{\"question_id\":\"aspirin-primary-prevention\"}"
```

**Check:** an answer with a non-empty `citations` array and `"blocked": null`.
An abstention instead means the corpus is empty or embedded with the wrong
embedder — back to step 3.

And that the eval numbers the methodology page reads are being served:

```powershell
curl.exe -fsS https://clinicalcontext-api.fly.dev/api/public/evals/golden
```

---

## Step 6 — Vercel: the frontend (10 minutes)

From `frontend/`:

```powershell
cd frontend
vercel link
```

Choose or create the project; when it asks for the root directory, it is the
directory you are in.

Set the environment variables. `vercel env add` prompts for the value, so it
does not land in your shell history:

```powershell
vercel env add NEXT_PUBLIC_API_URL production
vercel env add NEXT_PUBLIC_SUPABASE_URL production
vercel env add NEXT_PUBLIC_SUPABASE_ANON_KEY production
vercel env add NEXT_PUBLIC_RELEASE production
```

- `NEXT_PUBLIC_API_URL` → `https://clinicalcontext-api.fly.dev`, no trailing slash
- `NEXT_PUBLIC_SUPABASE_URL` / `..._ANON_KEY` → from step 1
- `NEXT_PUBLIC_RELEASE` → the output of `git rev-parse --short HEAD`

These must be set **before** the first production build, on purpose: the
Content-Security-Policy is derived from the API origin, so a build without it
would ship a policy that blocks every request to your own API — a blank app
with nothing in the server logs. The build fails instead, and it fails again
if any of these still point at localhost.

```powershell
vercel --prod
```

**Check:** open the deployment. The landing page renders, and the browser
console has no `Refused to connect` errors. If it does, the CSP and
`NEXT_PUBLIC_API_URL` disagree — fix the variable and redeploy.

---

## Step 7 — Join the two ends (5 minutes)

Two things still point at the wrong place.

**The API's CORS allow-list**, if the Vercel URL is not what you guessed in
step 4a. Edit `fly.toml`, then:

```powershell
fly deploy
```

**Supabase's redirect allow-list.** In the dashboard: **Authentication → URL
Configuration**. Set **Site URL** to your Vercel origin and add it to
**Redirect URLs**. Without this, magic links bounce to localhost and sign-in
silently fails for everyone but you.

**Check:** sign in with a magic link, end to end, from the deployed frontend.
That one flow exercises Supabase auth, the cookie flags, CORS, and the tenant
provisioning that runs on first sign-in.

---

## Step 8 — Tell me it is up

Send me:

1. **The two URLs** — the Fly one and the Vercel one.
2. **Which track** you deployed — offline or cloud.

With those I will check, and tell you plainly what fails if anything does:

- `/ready` names Postgres and Redis, and both are healthy
- all six security headers are present, HSTS included (it is off locally by design)
- the public demo answers a real question, with citations
- `/methodology` serves eval numbers from the deployed files
- one query produces one trace end to end, if you added an OTLP endpoint

For the **full end-to-end suite against the deployment** I need a database URL
and the service-role key, which are secrets. Leave them in
`backend/.env.deploy` (step 1 — gitignored) and say so; I will pass the file to
Playwright rather than read the values, and the suite creates and deletes its
own throwaway tenants.

---

## When it goes wrong

| Symptom | What it actually is |
|---|---|
| Machine exits at boot, logs show `ValidationError` | One of the ten required variables is missing. The message names it. |
| `prepared statement _pg1 already exists` under load | The app is on a transaction pooler but did not detect it. Set `DB_DISABLE_STATEMENT_CACHE=true`. |
| Migrations hang or error oddly | You used the transaction pooler (6543). Use the direct or session string (5432). |
| Direct connection times out from your machine | Supabase direct is IPv6-only on new projects. Use the session pooler string. |
| Every question abstains | Empty corpus, or embedded with a different embedder than `AI_BACKEND`. Step 3. |
| Blank frontend, `Refused to connect` in the console | `NEXT_PUBLIC_API_URL` disagrees with the deployed API origin. |
| Browser calls fail with a CORS error | `CORS_ORIGINS` in `fly.toml` is not exactly the Vercel origin. |
| Magic links go to localhost | Supabase redirect allow-list — step 7. |
| Voice page fails immediately | Expected without Deepgram + ElevenLabs: the image carries no offline speech stack. |
| Health check red, `/ready` 503 | Postgres or Redis unreachable. `fly logs` names which. |
| Everything worked, now 404s | Free Supabase project paused after 7 days idle. Unpause it in the dashboard. |
| Rate-limited (429) while testing | Working as designed. The response carries `Retry-After`. |

## After it is up

- **A status page.** Point any uptime monitor at `/ready` — it is the honest
  probe, it checks both dependencies, and it needs no authentication.
- **Keep the free Supabase project from pausing**, or accept that the public
  demo goes down after a quiet week.
- **The restore drill**, monthly and after any migration that rewrites data:
  `uv run python -m scripts.maintenance restore-drill`. An untested backup is
  a hope, not a backup.
- **A staging rung.** Repeat steps 1–7 with a second Supabase project, a
  second Upstash database and `clinicalcontext-api-staging`, and
  `.github/workflows/deploy.yml` will run migrations, deploy, smoke-test and
  run the browser suite on every merge, holding production behind a manual
  approval.
