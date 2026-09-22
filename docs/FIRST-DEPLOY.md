# First deploy, one step at a time — for $0

[DEPLOY.md](DEPLOY.md) is the reference: what runs where, and why. This is the
runbook for a deployment that costs nothing and asks for no card: the
commands in order, what each one should print, and what to do when it prints
something else.

**You create the accounts and set the secrets. I never see their values, and
nothing here asks you to paste one into a chat.** Commands are PowerShell,
because that is the shell you are in; they work in bash with `$env:X = 'y'`
changed to `export X='y'`.

Allow about ninety minutes, most of it waiting on a corpus.

---

## What runs where, and what it costs

Every piece below is the same technology the system was built and verified
on. One host changed: Fly.io bills from the first second and needs a card, so
the API runs on Render's free tier instead.

| Piece | Service | Free tier | Card? |
|---|---|---|---|
| API | **Render** free web service | 512 MB, sleeps after 15 idle minutes, wakes in ~1 min, 750 instance-hours a month | no |
| Frontend | **Vercel** Hobby | fine | no |
| Postgres + pgvector + auth | **Supabase** free | 2 projects, 500 MB; **pauses after 7 idle days** | no |
| Redis | **Upstash** free | 1 database, 256 MB, 500K commands a month | no |
| CI, deploys, scheduled jobs | **GitHub** Actions | unlimited minutes for a public repository; 2,000 a month for a private one | no |
| Models | offline backend | local embedder and reranker, deterministic reasoner — $0 by construction | — |
| Errors, traces (optional) | Sentry free, LangSmith free, any free OTLP endpoint | fine | no |

The numbers were checked on the vendors' pricing pages on 2026-09-21, and
the API was run under a hard 512 MB limit before this was written: **123 MB
idle, 214 MB after four uncached answers** with citations. It fits with room
to spare.

### What you accept for $0

- **The first request after fifteen quiet minutes waits about a minute**
  while Render wakes the instance (it shows a loading page). Every request
  after that is normal speed.
- **No worker process.** The API delivers its own webhooks while it is awake
  (deliveries are only ever queued by requests, so it always is). The nightly
  and weekly jobs run on a GitHub Actions schedule instead — step 8.
- **No voice.** Cloud voice needs Deepgram and ElevenLabs keys; the image
  carries no offline speech stack. The voice page fails when opened and
  nothing else depends on it.
- **Offline answers.** Cloud answers need an Anthropic key, which is paid.
  Everything else — retrieval, citations, contradictions, guardrails, the
  published numbers — is the real system.
- **Supabase pauses a free project after 7 days without activity.** The
  nightly job connects every day, which should keep it awake; if it pauses
  anyway, one click in the dashboard restores it.
- **No SMTP from a free Render instance** (outbound 25/465/587 are blocked),
  so the weekly digest is in-app only. That is also its default.

---

## Step 0 — Decide two names, and one repository

Everything below refers to these:

- Render service name → the API lives at `https://<name>.onrender.com`.
  `render.yaml` says `clinicalcontext-api`; change it there if you want
  another.
- Vercel project name → the app lives at `https://<name>.vercel.app`.
  `render.yaml` assumes `clinicalcontext` for CORS; change
  `CORS_ORIGINS` and `APP_PUBLIC_URL` there if you pick another.

And the repository has to be on GitHub: Render builds from it, and the
scheduled jobs run there. Nothing has been pushed yet.

```powershell
gh auth login
gh repo create clinicalcontext --private --source . --push
```

(Or create the repository in the GitHub UI, then
`git remote add origin <url>` and `git push -u origin main`.) A public
repository gets unlimited Actions minutes; a private one gets 2,000 a month,
and every push runs the full CI (tests, the golden-set eval, the frontend
build) — tens of minutes each — so a private repository budgets its pushes,
and a public one does not.

**Check:** `git remote -v` shows GitHub, and the Actions tab shows CI running
on your push.

---

## Step 1 — Accounts, and one file to hold the values (20 minutes)

| # | Service | Create | Collect |
|---|---|---|---|
| 1 | [Supabase](https://supabase.com) | one project, region near Render's `frankfurt` (or change both) | Project URL, anon key, service-role key, **direct** DB URL (5432), **transaction pooler** DB URL (6543) |
| 2 | [Upstash](https://upstash.com) | one Redis database, same region | the `rediss://…` URL |
| 3 | [Render](https://render.com) | an account, connected to GitHub | nothing yet |
| 4 | [Vercel](https://vercel.com) | an account, connected to GitHub | nothing yet |

In Supabase the connection strings are under **Project Settings → Database →
Connection string**. You need two of the three shown:

- **Direct connection**, port 5432 — for migrations and the jobs. If your
  network has no IPv6 this one may refuse to connect; use the **Session
  pooler** string (also port 5432) instead, which works the same way.
- **Transaction pooler**, port 6543 — for the running API. Not
  interchangeable: it cannot run migrations, and the app detects it from the
  DSN and turns off asyncpg's statement cache accordingly.

### Put them in one file, not in your shell history

`.env.*` is gitignored and allow-listed in the secret scanner, so this never
reaches a commit:

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
```

Every one of these except the project URL and the anon key is a secret. The
anon key ships in the browser bundle by design (RLS is what protects the
data), but there is no reason to circulate it either.

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

Then, in the Supabase SQL editor:

```sql
select count(*) from pg_policies where schemaname = 'public';
```

**Check:** 83. Those rows are the application's entire tenant isolation.

---

## Step 3 — A corpus (10 minutes, or a couple of hours)

Without documents the app answers nothing — correctly, by abstaining, which
looks exactly like a broken deployment.

Documents and questions must be embedded by the *same* embedder. The free
deployment is offline, so the corpus is embedded with the local embedder;
switching to Cohere later means re-embedding. Both write 1536-dimension
vectors, so a mismatch does not raise an error — it silently returns noise.

Point the tooling at Supabase, with the **direct** URL — this writes a great
many rows:

```powershell
$env:DATABASE_URL = "<DIRECT_DATABASE_URL>"
```

**Option A — the snapshot (about 10 minutes).** 1,972 documents and 6,323
chunks: the golden set's relevant documents plus a fixed sample of
distractors — the same corpus CI evaluates against, so the CI numbers
describe what you deployed. About 150 MB in Postgres.

```powershell
uv --directory backend run python -m evals.golden.snapshot load --embedder local
```

**Option B — a seed straight from PubMed (an hour or two).** Seventeen
queries across four clinical domains, rate-limited politely. The full
development corpus (`--per-query 600`, 9,996 documents) measures **1,079 MB**
in Postgres — twice Supabase's free 500 MB, most of it the vectors and their
index. `--per-query 150` yields up to 2,550 documents, about 275 MB.

```powershell
uv --directory backend run python -m app.ingestion.seed --per-query 150 --embedder local
```

**Check**, in the Supabase SQL editor:

```sql
select
  (select count(*) from documents)        as documents,
  (select count(*) from chunks)           as chunks,
  (select count(*) from chunk_embeddings) as embeddings;
```

Embeddings must be non-zero and close to the chunk count. Then clear the
variable, so a later command cannot point at production by accident:

```powershell
Remove-Item Env:DATABASE_URL
```

---

## Step 4 — Render: the API (15 minutes)

Render reads [`render.yaml`](../render.yaml) and creates the service from it:
Docker build from `backend/Dockerfile`, the free plan, `/ready` as the health
check, a 90-second shutdown window so an in-flight answer finishes, and the
non-secret configuration. If you changed either name in step 0, edit and
commit the file first.

1. Render Dashboard → **New → Blueprint** → choose the repository → **Apply**.
2. Render asks for every value marked `sync: false`. All ten are required at
   boot: the backend validates its configuration as a whole and refuses to
   start with one missing, rather than failing on the first call that needed
   it. The five provider keys are unused on the offline track — enter
   `placeholder` for each and they stay unused.

   | Variable | Value |
   |---|---|
   | `DATABASE_URL` | the **pooled** URL (6543) |
   | `REDIS_URL` | from Upstash |
   | `SUPABASE_URL`, `SUPABASE_ANON_KEY`, `SUPABASE_SERVICE_ROLE_KEY` | from Supabase |
   | `COHERE_API_KEY`, `ANTHROPIC_API_KEY`, `LANGSMITH_API_KEY`, `DEEPGRAM_API_KEY`, `ELEVENLABS_API_KEY` | `placeholder` |

3. The blueprint turns automatic deploys **off** — the pipeline deploys after
   CI is green and migrations have run. For this first time: open the
   service → **Manual Deploy → Deploy latest commit**. The build takes about
   five minutes.
4. Service → **Settings → Deploy Hook** → copy the URL somewhere safe. Step 8
   needs it. It is a secret: anyone holding it can trigger a deploy.

**Check** — the API answers and knows both dependencies are alive:

```powershell
curl.exe -fsS https://clinicalcontext-api.onrender.com/ready
```

Expect JSON naming `database` and `redis`, both `ok`. A 503 means one is
unreachable: `/ready` is deliberately honest, and Render keeps the instance
out of rotation until it is true.

The security headers, via a GET with the headers dumped (`-I` sends a HEAD,
which tells you less):

```powershell
curl.exe -fsS -D - -o NUL https://clinicalcontext-api.onrender.com/health
```

**Check:** `strict-transport-security` and `x-content-type-options: nosniff`
among them, and a body whose `release` is your commit — Render stamps it.

And that the public demo actually answers. It takes the **id** of one of four
curated questions (`GET /api/public/demo/questions` lists them), not free
text, so an unauthenticated endpoint cannot be used as a free model:

```powershell
curl.exe -fsS -X POST https://clinicalcontext-api.onrender.com/api/public/demo -H "content-type: application/json" -d "{\"question_id\":\"aspirin-primary-prevention\"}"
```

**Check:** an answer with a non-empty `citations` array and `"blocked": null`.
An abstention instead means the corpus is empty — back to step 3.

---

## Step 5 — Vercel: the frontend (10 minutes)

Vercel Dashboard → **Add New → Project** → the repository → **Root Directory:
`frontend`**. Before the first build, add the environment variables — the
Content-Security-Policy is derived from the API origin, so a build without it
would ship a policy that blocks every request to your own API. The build
fails instead, on purpose, and fails again if a value still points at
localhost.

| Variable | Value |
|---|---|
| `NEXT_PUBLIC_API_URL` | `https://clinicalcontext-api.onrender.com` — no trailing slash |
| `NEXT_PUBLIC_SUPABASE_URL` | from step 1 |
| `NEXT_PUBLIC_SUPABASE_ANON_KEY` | from step 1 |
| `NEXT_PUBLIC_RELEASE` | the output of `git rev-parse --short HEAD` |

Then **Deploy**.

**Check:** open the deployment. The landing page renders, and the browser
console has no `Refused to connect` errors. If it does, the CSP and
`NEXT_PUBLIC_API_URL` disagree — fix the variable and redeploy.

---

## Step 6 — Join the two ends (5 minutes)

**The API's CORS allow-list.** If the Vercel URL is not what `render.yaml`
says, edit `CORS_ORIGINS` and `APP_PUBLIC_URL` there, commit, push, and
Manual Deploy again. `CORS_ORIGINS` is the only thing standing between your
API and any other website's JavaScript; it has to be the exact origin, scheme
included, no trailing slash.

**Supabase's redirect allow-list.** Dashboard → **Authentication → URL
Configuration**. Set **Site URL** to your Vercel origin and add
`https://<your-app>.vercel.app/**` to **Redirect URLs**. Without this, magic
links bounce to localhost and sign-in silently fails for everyone but you.

**Email is optional, and only these two things need it.** Sign-up and
password sign-in work with no mail provider at all: the API creates the
account itself with the service-role key (`backend/app/services/signup.py`),
so Supabase never has to send a confirmation. What still needs email is the
**magic link** and **forgot password** — a password reset cannot be done
safely any other way. Both say plainly when delivery is not configured.

The reason it works this way: Supabase's built-in mailer only delivers to
the addresses of your own Supabase team, two messages an hour, and answers
`500 Error sending confirmation email` for everyone else. That is Supabase's
stated policy, not a bug in the app, and it made the deployment's first
sign-up impossible.

The trade-off is that an address is not proven to belong to whoever typed
it — the same posture as Supabase's own "Confirm email" switch turned off.
It is acceptable here because the product holds no patient data and a new
account grants nothing but an empty organisation.

**To turn email on anyway** (free, no card) — needed for magic links and
password resets:

1. [Brevo](https://www.brevo.com) → free account → **Senders & IP → Senders**
   → add and verify the address you will send from (they email you a link).
2. Brevo → **SMTP & API → SMTP** → note the server (`smtp-relay.brevo.com`),
   port `587`, your login, and generate an **SMTP key**.
3. Supabase → **Project Settings → Authentication → SMTP Settings** → enable
   **Custom SMTP** → host, port, username, the SMTP key as the password,
   and the verified address as sender. Save.
4. Supabase → **Authentication → Rate Limits** → emails per hour: raise from
   the default 30 if you expect more sign-ups than that.

Worth doing at the same time: **Authentication → Email Templates** — add the
line `Your code: {{ .Token }}` to the *Magic Link* and *Reset Password*
templates. Every "check your email" screen accepts that 6-digit code as well
as the link, which is the way through when a link is opened on a different
device from the one that asked for it.

**To require verified emails once SMTP works**, have the sign-up form call
`supabase.auth.signUp` directly again (it is a few lines in
`frontend/app/(auth)/signup/page.tsx`) and delete the `/api/public/signup`
endpoint with its service.

**Check:** sign up on the deployed frontend and land in the app. That
exercises the API, Supabase auth, the cookie flags, CORS, and the tenant
provisioning that runs on first sign-in.

---

## Step 7 — Tell me it is up

Send me:

1. **The two URLs** — the Render one and the Vercel one.
2. Nothing else. No connection string, no key.

With those I will check, and tell you plainly what fails if anything does:

- `/ready` names the database and Redis, and both are healthy
- all six security headers are present, HSTS included (it is off locally by design)
- the public demo answers a real question, with citations
- `/methodology` serves eval numbers from the deployed files
- the wake-up time of a sleeping instance, measured rather than assumed

For the **full end-to-end suite against the deployment** I need the direct
database URL and the service-role key, which are secrets. Leave them in
`backend/.env.deploy` and say so; I will pass the file to Playwright rather
than read the values, and the suite creates and deletes its own throwaway
tenants.

---

## Step 8 — The pipeline and the jobs (15 minutes, once)

Deploys so far were by hand. From here, a merge to `main` goes: CI green → a
person approves → migrations → API → wait for the new release to answer →
frontend → smoke test. The scheduled jobs run nightly and weekly.

GitHub → repository → **Settings → Secrets and variables → Actions**:

| Secret | Value | Used by |
|---|---|---|
| `RENDER_DEPLOY_HOOK_URL` | from step 4 | deploy |
| `PRODUCTION_DATABASE_URL` | the **direct** URL (5432) | deploy (migrations), jobs |
| `VERCEL_TOKEN`, `VERCEL_ORG_ID`, `VERCEL_PROJECT_ID` | Vercel → Account Settings → Tokens; the ids are in the project's `.vercel/project.json` after `vercel link`, or in the project settings | deploy |
| `REDIS_URL`, `SUPABASE_URL`, `SUPABASE_ANON_KEY`, `SUPABASE_SERVICE_ROLE_KEY` | from step 1 | jobs (and the E2E job, if enabled) |

| Variable | Value |
|---|---|
| `API_URL`, `WEB_URL` | only if your hostnames differ from the defaults in `deploy.yml` |
| `E2E_AFTER_DEPLOY` | `true` to run the browser suite against the deployment after each deploy; unset to skip |

Then **Settings → Environments → New environment: `production`** → **Required
reviewers** → yourself. That is the manual gate: the deploy job waits for
your approval every time.

**Check:** Actions → **Scheduled jobs → Run workflow → `living-answers`**. It
should finish green in a couple of minutes and print a tally. Then push a
trivial commit and watch **Deploy** wait for your approval, deploy, wait for
the new release, and smoke-test it.

---

## When it goes wrong

| Symptom | What it actually is |
|---|---|
| Render build fails on `COPY backend/pyproject.toml` | `dockerContext` must be `.` (the repository root) — it is, in `render.yaml`; check the service was created from the blueprint, not by hand. |
| Instance exits at boot, logs show `ValidationError` | One of the ten required variables is missing. The message names it. |
| `/ready` says `redis: error` | Upstash URL wrong, or `redis://` where it needs `rediss://` (TLS). |
| `prepared statement _pg1 already exists` | The app is on the transaction pooler but did not detect it. Set `DB_DISABLE_STATEMENT_CACHE=true`. |
| Migrations hang or error oddly | You used the transaction pooler (6543). Use the direct or session string (5432). |
| Direct connection times out from your machine | Supabase direct is IPv6-only on new projects. Use the session pooler string. |
| Every question abstains | Empty corpus — step 3. |
| First request takes a minute | The free instance was asleep. Expected; the next one is fast. |
| Blank frontend, `Refused to connect` in the console | `NEXT_PUBLIC_API_URL` disagrees with the deployed API origin. |
| Browser calls fail with a CORS error | `CORS_ORIGINS` in `render.yaml` is not exactly the Vercel origin. |
| Magic links go to localhost | Supabase redirect allow-list — step 6. |
| Magic link / reset email never arrives | Supabase's built-in mailer only sends to your own team's addresses, 2/hour. Custom SMTP — step 6. Sign-up itself does not need email. |
| Sign-up hangs for a minute or two | The free-tier API had gone to sleep. The form wakes it when it loads; the wait is the instance starting. |
| "That link was opened in a different browser" | The link was requested on one device and opened on another. Enter the 6-digit code from the email instead. |
| Voice page fails immediately | Expected without Deepgram + ElevenLabs. |
| Everything worked, now `/ready` fails on the database | Free Supabase project paused after 7 idle days. Restore it in the dashboard. |
| Deploy job times out "waiting for release" | Render's build failed or is slow — the service's Events tab says which. |
| All free services suspended mid-month | 750 instance-hours used, which needs two services or an always-awake one. Resets on the 1st. |
| Rate-limited (429) while testing | Working as designed. The response carries `Retry-After`. |

## After it is up

- **A status page.** Point any free uptime monitor at `/ready` — it is the
  honest probe, it checks both dependencies, and it needs no authentication.
  Note that a monitor pinging every few minutes also keeps the instance
  awake, which spends the 750 hours; every 15+ minutes lets it sleep.
- **The restore drill**, monthly and after any migration that rewrites data:
  `uv run python -m scripts.maintenance restore-drill` against the direct
  URL. An untested backup is a hope, not a backup.
- **Moving off the free tier** is one line in `render.yaml` (`plan`), or
  `fly.toml` for a host with a real worker process and always-on machines.
  Nothing else changes.
