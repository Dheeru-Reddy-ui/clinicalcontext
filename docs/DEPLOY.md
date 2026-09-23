# Deploying ClinicalContext

**Start here.** This is the only page you need to put a change live.

1. Read [Where things are right now](#where-things-are-right-now) — two minutes.
2. Do [Part 1](#part-1--put-the-newest-api-live). That makes sign-up work on the live site.
3. [Part 2](#part-2--let-the-app-send-emails-optional) and [Part 3](#part-3--make-every-push-deploy-itself-optional) are optional. Do them when you have time, in any order.

> **One rule throughout.** Passwords and keys never go in a chat, and never in a file in this repository — it is public. Each one goes into a settings page on Render, Supabase, Vercel or GitHub, and this page says which.

**On this page**

- [Where things are right now](#where-things-are-right-now)
- [Part 1 — Put the newest API live](#part-1--put-the-newest-api-live) · *do this first, 5 minutes*
- [Part 2 — Let the app send emails](#part-2--let-the-app-send-emails-optional) · *optional, 15 minutes*
- [Part 3 — Make every push deploy itself](#part-3--make-every-push-deploy-itself-optional) · *optional, 20 minutes*
- [Part 4 — Turn on voice](#part-4--turn-on-voice-optional) · *optional, 10 minutes*
- [Every day: how a change goes live](#every-day-how-a-change-goes-live)
- [If something goes wrong](#if-something-goes-wrong)
- [Words on this page](#words-on-this-page)
- [Reference for engineers](#reference-for-engineers) · *not needed to deploy*

---

## Where things are right now

*Checked on 23 September 2026.*

The app is a few services that work together. Most are already fine.

| Piece | What it does | Where it lives | State |
|---|---|---|---|
| **Website** | The pages people see | Vercel — [clinicalcontext-euev.vercel.app](https://clinicalcontext-euev.vercel.app) | Up to date. Updates itself every time you push. |
| **API** | The server behind the website. Answers questions, creates accounts. | Render — [clinicalcontext-api.onrender.com](https://clinicalcontext-api.onrender.com/health) | Up to date. Does **not** update itself — see Part 1, or set up Part 3. |
| **Database and logins** | Stores documents, users and organisations | Supabase | Working. All 23 database updates are applied. |
| **Cache** | Makes repeated questions fast; rate limits | Upstash | Working. |
| **Automatic deploys** | Ships the API after every test passes | GitHub Actions | Working. After tests pass, a deploy waits for your approval, then ships the API. The website ships itself through Vercel. |
| **Email** | Magic-link sign-in and forgot-password emails | Supabase + an email provider | Not set up. Optional — Part 2. Sign-up and password sign-in do **not** need it. |
| **Voice** | Ask out loud, hear the answer | Deepgram | Not set up. Optional — Part 4. The Voice page says so and disables Start until it is. |

**Why do the two move separately?** Vercel rebuilds the website on its own every time you push to `main`. Render is deliberately set to wait until it is told (`autoDeployTrigger: "off"` in `render.yaml`), so a broken API can never go live on its own — either the deploy pipeline tells it (Part 3) or you do (Part 1).

### The four websites you will use

| Website | Link | What you do there |
|---|---|---|
| Render | https://dashboard.render.com | Ship the API |
| Supabase | https://supabase.com/dashboard/project/hyqnrsqrldrdjmnaksjk | Email settings, the database address |
| GitHub | https://github.com/Dheeru-Reddy-ui/clinicalcontext | Secrets, watching tests and deploys |
| Vercel | https://vercel.com/dashboard | The website's settings (rarely needed) |

---

## Part 1 — Put the newest API live

**About 5 minutes, mostly waiting. No passwords needed.** This is how the API
ships by hand: after a backend change, or any time `/health` shows an older
commit than GitHub. Part 3 does it for you.

### Steps

1. Open https://dashboard.render.com and sign in.
2. Click the service named **clinicalcontext-api**.
3. In the top-right corner, click **Manual Deploy**, then **Deploy latest commit**.
4. Wait while it builds — about 5 minutes. A log scrolls past. It is finished when the status near the top says **Live**.

### Check it worked

Open https://clinicalcontext-api.onrender.com/health. You will see one line like this:

```json
{"status":"ok","service":"clinicalcontext-backend","version":"0.1.0","release":"911987a98d45…"}
```

The start of `release` is the commit the API is running. It should match the newest commit on https://github.com/Dheeru-Reddy-ui/clinicalcontext — the short code shown next to the latest commit message. If it still shows the old one, the new version is not live yet: wait a minute and refresh.

> **The API falls asleep.** On the free plan it sleeps after 15 minutes with no visitors. The first visit after that takes up to two minutes to wake it, and the site says so while it waits. This is normal.

### Try it

1. Open https://clinicalcontext-euev.vercel.app/signup in a private (incognito) window.
2. Enter a name, any email address, and a password of at least 8 characters. Click **Sign up**.
3. You should arrive at **Set up your workspace** straight away. There is no email to wait for.
4. Type an organisation name and click **Create organization**. You are in.

What you might see instead:

- *"Sign-up is not available on this server yet"* — the API is still the old version. Go back to step 4 of the steps above.
- *"An account with this email already exists"* — that email is taken. Use **Sign in**, or **Reset password** (which needs Part 2).

---

## Part 2 — Let the app send emails (optional)

**About 15 minutes. Free, no card needed.**

You only need this for two buttons on the sign-in page: **Email me a magic link** and **Forgot your password?**. Signing up and signing in with a password already work without it.

**Why it is needed.** Supabase's built-in email only reaches people on your own Supabase team, and only two emails an hour. Everyone else gets nothing. Connecting a real email service fixes that. These steps use **Brevo**, which is free for 300 emails a day.

### 2a. Get sending details from Brevo

1. Create a free account at https://www.brevo.com.
2. Add yourself as a sender: click your account name (top right) → **Settings** → **Senders, Domains, IPs** → **Senders** → **Add a sender**. Enter a *From name* (`ClinicalContext`) and your email address as *From email*. Click **Save**. Brevo emails a 6-digit code to that address — enter it to verify.
3. Open the sending page: account name → **Settings** → **SMTP & API** → **SMTP** tab. Note two things shown there:
   - **SMTP server:** `smtp-relay.brevo.com`, port `587`
   - **Login:** an email address, or something like `8a1b2c@smtp-brevo.com`
4. On the same tab click **Generate a new SMTP key**. Name it `supabase`, keep the **Standard** type, and copy the key. **Brevo shows it only once** — keep it somewhere safe until the next step.

> Sending from a Gmail address works, but some messages may land in spam. A domain you own, verified in Brevo, fixes that later; it is not needed to start.

### 2b. Connect Brevo to Supabase

1. Open https://supabase.com/dashboard/project/hyqnrsqrldrdjmnaksjk/auth/smtp
2. Switch on **Enable custom SMTP** and fill in:

   | Field | Value |
   |---|---|
   | Sender email | the address you verified in Brevo |
   | Sender name | `ClinicalContext` |
   | Host | `smtp-relay.brevo.com` |
   | Port | `587` |
   | Username | the Brevo **Login** from 2a |
   | Password | the Brevo **SMTP key** from 2a |

3. Click **Save**.
4. Supabase starts you at 30 emails an hour. If you need more, raise it at https://supabase.com/dashboard/project/hyqnrsqrldrdjmnaksjk/auth/rate-limits

### 2c. Tell Supabase where your website is

Links inside emails must lead back to your website, and Supabase only allows addresses you have listed.

1. Open https://supabase.com/dashboard/project/hyqnrsqrldrdjmnaksjk/auth/url-configuration
2. **Site URL:** `https://clinicalcontext-euev.vercel.app` → **Save**.
3. **Redirect URLs** → **Add URL** → `https://clinicalcontext-euev.vercel.app/**` → **Save**.

### 2d. Put a 6-digit code in the emails (recommended)

Every "check your email" screen in the app also accepts a 6-digit code. It is the way through when someone asks for a link on a laptop but opens the email on a phone — the link only works in the browser that asked for it; the code works anywhere.

1. Open https://supabase.com/dashboard/project/hyqnrsqrldrdjmnaksjk/auth/templates
2. Open **Magic Link**. Add this line to the message: `Your code: {{ .Token }}` → **Save**.
3. Do the same for **Reset Password**.

### Try it

Go to https://clinicalcontext-euev.vercel.app/login, click **Forgot your password?**, enter your email and send. The email should arrive within a minute — check spam if not. Its link (or code) leads to a page where you choose a new password.

> **Want every new account to confirm its email address?** Today sign-up creates the account straight away, with no confirmation email — that is what made sign-up work without an email service. Once Part 2 is done you can switch confirmation back on; it is a code change in `frontend/app/(auth)/signup/page.tsx`, described in `backend/app/services/signup.py`. Not needed to run the app.

---

## Part 3 — Make every push deploy itself (optional)

**About 20 minutes, done once.** After this you never click *Manual Deploy* again.

**What changes.** You push to `main` → GitHub runs every test (about 25 minutes) → if all pass, GitHub updates the database, ships the API, waits until the new version answers, and checks the live site works.

To do that, GitHub needs a few **secrets**: values it keeps hidden and gives only to the deploy job. You type each one into GitHub once.

### 3a. Add the two deploy secrets

Open https://github.com/Dheeru-Reddy-ui/clinicalcontext/settings/secrets/actions. For each row below: click **New repository secret** → type the **Name** exactly as shown → paste the value → **Add secret**.

| # | Name (type it exactly) | Where the value comes from |
|---|---|---|
| 1 | `PRODUCTION_DATABASE_URL` | Supabase → your project → the **Connect** button at the top of the page → choose **Session pooler** → copy the string. Replace `[YOUR-PASSWORD]` with your database password. It should look like `postgresql://postgres.hyqnrsqrldrdjmnaksjk:[YOUR-PASSWORD]@aws-0-eu-central-1.pooler.supabase.com:5432/postgres` — note **port 5432**. See the box below for why this one. |
| 2 | `RENDER_DEPLOY_HOOK_URL` | Render → **clinicalcontext-api** (the one ending in `-api`) → **Settings** → scroll to **Deploy Hook** → copy. It must look like `https://api.render.com/deploy/srv-dap2mulg1s2s7396sveg?key=…` — that `srv-…` is the API's id. Not the page address in your browser bar, and not `clinicalcontext-api.onrender.com`. Treat it like a password: anyone who has it can redeploy your API. |

> **Why "Session pooler" and not "Direct connection"?** On Supabase's free plan the direct connection only works over IPv6, and GitHub's machines only have IPv4 — so the deploy would fail with *"network is unreachable"*. The session pooler works over IPv4 and can run database updates. It has been checked: a dry run of all 23 updates through it reports *database is up to date*.
>
> Don't use port **6543** either: that is the *transaction* pooler, which the API itself uses, and it cannot run database updates.

> **The website needs no secret here.** Vercel builds and publishes it by itself on every push to `main`. Only the API is deployed by this pipeline.

### 3b. Add four more for the nightly jobs

Three background jobs run on a schedule: refreshing saved answers every night, and a weekly digest and corpus check. They need the database secret from 3a plus these four. **All four values are already in Render**, so copying them is quickest: Render → **clinicalcontext-api** → **Environment** → click the eye icon next to each → copy.

| Name (type it exactly) | Copy from Render's Environment |
|---|---|
| `REDIS_URL` | `REDIS_URL` |
| `SUPABASE_URL` | `SUPABASE_URL` |
| `SUPABASE_ANON_KEY` | `SUPABASE_ANON_KEY` |
| `SUPABASE_SERVICE_ROLE_KEY` | `SUPABASE_SERVICE_ROLE_KEY` |

If any of them is missing the jobs don't fail — they skip, and the run's page names what is missing.

### 3c. (Recommended) Make deploys wait for your OK

Right now a green test run deploys immediately. For a clinical app, a human check before anything goes live is worth one minute:

1. Open https://github.com/Dheeru-Reddy-ui/clinicalcontext/settings/environments and click **Production**.
2. Tick **Required reviewers**, add yourself, click **Save protection rules**.

From then on each deploy pauses. The run on the **Actions** page shows a **Review deployments** button; click it and approve.

### Check it worked

1. Open https://github.com/Dheeru-Reddy-ui/clinicalcontext/actions, click **Deploy** in the left list, then **Run workflow** → **Run workflow**.
2. Click the run that appears. Its first step, *Check the deploy secrets are present*, should say `both deploy secrets are present`. If it names a missing secret, add it (3a) and run again.
3. The whole run takes 5–10 minutes. A green tick means the API and website are both on the newest commit.

---

## Part 4 — Turn on voice (optional)

**About 10 minutes. Free, no card needed.**

Voice mode lets people ask out loud and hear the answer. It needs a speech service, because the free server is far too small to run speech recognition itself: until this is done the Voice page says *"Voice isn't available on this server yet"* and the Start button is disabled.

This uses **Deepgram**: one free account covers both halves — `nova-3-medical` hears the question (a model trained on medical vocabulary, so drug names come through) and Aura-2 speaks the answer. New accounts get **$200 of credit with no card and no expiry**, which is tens of thousands of minutes of listening.

### 4a. Get a Deepgram key

1. Sign up at https://console.deepgram.com/signup (email or Google; no card).
2. Top-left, pick your project from the **Projects** menu, then click **Settings** → **API Keys**.
3. Click **Create a New API Key**. Friendly name: `clinicalcontext-voice`. Permissions: **Member**. Expiration: **Never**. Click **Create Key**.
4. **Copy the key now** — Deepgram shows it only once — then click **Got it**.

### 4b. Give it to the API

1. Render → **clinicalcontext-api** → **Environment**.
2. Find `DEEPGRAM_API_KEY` → **Edit** → paste the key → make sure nothing follows it (no space, no line break).
3. Find `VOICE_BACKEND` → change `offline` to `cloud`.
4. Click **Save, rebuild, and deploy**. It takes about 5 minutes.

### Check it worked

1. Open https://clinicalcontext-euev.vercel.app/app/voice. The *"isn't available"* notice should be gone.
2. Click **Test microphone** and say something. The bar should move and the text should change to *"Your voice is coming through."* If the browser asks for the microphone, choose **Allow**.
3. Click **Start listening** and ask: *"What is first-line anticoagulation in non-valvular atrial fibrillation?"* You should see your words appear, then hear a cited answer. Talk over it to interrupt.

> **What each part does if something is wrong.** The mic check needs nothing from the server — if the bar does not move, the problem is the browser or the microphone, and the message under it says which. If the bar moves but voice says it is unavailable, the key or `VOICE_BACKEND` is not set on Render.

---

## Every day: how a change goes live

**Before you push**, you can run the same checks GitHub runs, on your own machine, in about 8 minutes:

```bash
python scripts/preflight.py
```

If it ends with `All green`, GitHub's tests will pass too. `python scripts/preflight.py --fast` skips the two slowest checks when you just want a quick look.

### If you have not done Part 3

1. Push to `main`.
2. The website updates by itself within about 2 minutes.
3. The API does not. If your change touched anything in `backend/`, do [Part 1](#part-1--put-the-newest-api-live) again.

**If your change adds a file to `backend/migrations/`**, apply it to the database *before* step 3 — the new API may need it. In PowerShell, from the repository folder:

```powershell
cd backend
$env:MIGRATE_URL = Read-Host "Paste the Session pooler string (Part 3a, row 1)"
.venv\Scripts\python -m scripts.migrate --url $env:MIGRATE_URL --dry-run   # shows what will run
.venv\Scripts\python -m scripts.migrate --url $env:MIGRATE_URL             # runs it
Remove-Item Env:MIGRATE_URL
```

`Read-Host` keeps the password out of your command history. The command is safe to repeat: it only applies what has not been applied yet.

### If you have done Part 3

1. Push to `main`.
2. Watch https://github.com/Dheeru-Reddy-ui/clinicalcontext/actions. **CI** runs first (about 25 minutes).
3. When CI is green, **Deploy** starts by itself — or waits for your approval, if you did 3c. It applies database updates for you.

Either way, the website updates a few minutes before the API does. During those minutes the sign-up form says plainly that the server is older than the page, rather than failing strangely.

---

## If something goes wrong

| What you see | What it means | What to do |
|---|---|---|
| Sign-up says *"not available on this server yet"* | The API is older than the website | [Part 1](#part-1--put-the-newest-api-live) |
| *"The server is starting up"*, or *"Waking the API"* on the home page | The free API was asleep | Wait up to two minutes. It carries on by itself. |
| Sign-up says *"An account with this email already exists"* | That email already has an account | Sign in instead, or reset the password (needs Part 2) |
| A red ✗ next to **CI** on GitHub | A test failed | Click it; the red step shows the error. `python scripts/preflight.py` reproduces it on your machine. |
| **Deploy** stops at *Check the deploy secrets are present* | A secret is missing or its name is misspelled | Add the secret it names ([Part 3a](#3a-add-the-two-deploy-secrets)) |
| **Deploy** fails at *Apply migrations* with *network is unreachable* | `PRODUCTION_DATABASE_URL` is the *Direct connection* string | Replace it with the **Session pooler** string ([Part 3a](#3a-add-the-two-deploy-secrets), row 1) |
| **Deploy** stops at *Check the deploy secrets are present* with *not a Render deploy hook* | `RENDER_DEPLOY_HOOK_URL` holds some other address — a dashboard page, the API's own URL | Copy the real hook ([Part 3a](#3a-add-the-two-deploy-secrets), row 2). The step prints which `srv-…` a hook targets; for the API it is `srv-dap2mulg1s2s7396sveg`. |
| **Deploy** fails at *Deploy the API* with 401 or 404 | The hook was regenerated in Render, or points at a service that no longer exists | Copy it again from Render → **clinicalcontext-api** → Settings → Deploy Hook |
| **Deploy** times out at *Wait for the new release to answer* | Render never built the commit — usually a hook for a different service | Check the service id the secrets step printed, then Render → **clinicalcontext-api** → **Events** |
| Sign-up on the live site says *"could not be reached"* (503) | A secret in Render has a stray newline or space from being pasted | Render → **clinicalcontext-api** → **Environment**, re-paste the value, **Save**. Since the config now trims whitespace, this only bites a deployment older than that fix. |
| `/health` still shows the old `release` after a deploy | Render is still building, or the build failed | Render → **clinicalcontext-api** → **Events**. A failed build shows its log. |
| Magic-link or reset email never arrives | Email isn't set up, or the email went to spam | [Part 2](#part-2--let-the-app-send-emails-optional); check the spam folder |
| The email link says *"opened in a different browser"* | Links only work in the browser that asked for them | Type the 6-digit code instead ([Part 2d](#2d-put-a-6-digit-code-in-the-emails-recommended)) |
| The email link opens `localhost` | Supabase doesn't know your website's address | [Part 2c](#2c-tell-supabase-where-your-website-is) |
| *"Error sending confirmation email"* or *"Email address not authorized"* | Supabase is still using its built-in email | [Part 2b](#2b-connect-brevo-to-supabase) |
| Voice page says *"Voice isn't available on this server yet"* | No speech service is set up | [Part 4](#part-4--turn-on-voice-optional) |
| The microphone test bar does not move | The browser blocked the mic, or the wrong one is chosen | Read the message under the bar; pick another microphone from the list |
| Voice worked, then says it is unavailable | The Deepgram key was deleted, or the $200 credit ran out | Deepgram console → **Usage**; make a new key ([Part 4a](#4a-get-a-deepgram-key)) |

---

## Words on this page

- **Push** — send your saved changes to GitHub (`git push`).
- **Commit** — one saved change, known by a short code such as `911987a`.
- **Deploy** — replace the live version of something with a newer one.
- **CI** — GitHub automatically running every test on each push. Nothing deploys unless it passes.
- **Secret** — a password-like value kept in a website's settings, never in the code.
- **Database update** (also called a **migration**) — a numbered file in `backend/migrations/` that changes the database's structure. Each one runs once, in order, and is never edited afterwards.
- **Pooler** — a doorway into the database that lets many connections share a few. Supabase has two: **session** (port 5432, can run database updates) and **transaction** (port 6543, what the API uses day to day).

---

## Reference for engineers

Everything below explains *why* it is built this way. None of it is needed to deploy.

### What runs where

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
| Account creation | the API, `POST /api/public/signup` | Supabase's built-in mailer refuses addresses outside the project team, so a browser-side `signUp` that sends a confirmation email failed for every real user. The API creates the user with the service-role key and sends nothing; `backend/app/services/signup.py` states the trade-off. |

### Rules the configuration depends on

1. **Secrets go to the platform's secret store, never to a file on a server.** Render's environment (the `sync: false` entries in `render.yaml`), or `fly secrets set` on Fly; Vercel's environment variables for the frontend; GitHub's repository secrets for the pipeline and the jobs. The application reads them as environment variables; `.env` exists for local development only, and is gitignored. `gitleaks` runs in CI against the whole tree (`.gitleaks.toml`) and fails on anything that looks like a credential.

2. **Use the right Postgres URL.** There are three doors into the same database:
   - **Transaction pooler** (pooler host, port 6543) — what the API uses. Many instances share few server connections. The app detects it from the DSN and turns off asyncpg's prepared-statement cache; without that, concurrency produces `prepared statement _pg1 already exists` under load and nowhere else (`app/config.py`, `db_uses_transaction_pooler`).
   - **Session pooler** (pooler host, port 5432) — what migrations use from GitHub Actions. A real session, so DDL and prepared statements work, and reachable over IPv4.
   - **Direct connection** (`db.<ref>.supabase.co:5432`) — also fine for migrations, but IPv6-only on the free plan, so unreachable from GitHub's IPv4-only runners.

   Migrations cannot run through the transaction pooler.

3. **Apply migrations before the code that needs them.** The deploy workflow runs `scripts.migrate` first. Migrations are forward-only and checksummed; an edited migration that has already been applied fails the run rather than silently diverging.

4. **Set the pool size for the instance count.** `DB_POOL_MAX_SIZE` × instances must stay under the pooler's client limit. `render.yaml` uses 1–5 for its single free instance; `fly.toml` 2–10 per machine.

5. **Mind the free database's size.** The full development corpus measures 1,079 MB in Postgres — twice Supabase's free 500 MB, most of it vectors and their HNSW index. The golden snapshot (1,972 documents) is about 150 MB; a PubMed seed at `--per-query 150` about 275 MB.

6. **Index parameters.** The HNSW index is built `m=16, ef_construction=64`, which suits a corpus up to about a million vectors; `hnsw.ef_search` is set per connection from `HNSW_EF_SEARCH` (default 100) and **must not be below the candidate count** the retrieval pipeline asks for — the tenant filter is applied after the index, so a low value silently returns fewer rows. `uv run python -m scripts.maintenance analyze` runs `VACUUM ANALYZE` and prints the index size and settings.

7. **All ten API secrets are required at boot**, including the provider keys an offline deployment never calls. The configuration validates as a whole on start rather than failing on the first request that needed a missing value; `placeholder` is a fine value for a key you are not using.

8. **The corpus is tied to `AI_BACKEND`.** Offline embeds with the local hashing embedder, cloud with Cohere, both into 1536 dimensions — so embedding a corpus with one and querying it with the other returns noise rather than an error.

9. **Either generation of Supabase key works, and they are sent differently.** Legacy `anon`/`service_role` keys are JWTs (`eyJ…`); the new `sb_publishable_`/`sb_secret_` keys are not, and Supabase refuses them as `Authorization: Bearer` with *"invalid JWT … invalid number of segments"*. Every server-side call builds its headers through `app/core/supabase_keys.py`, which puts the secret key on `apikey` always and on `Authorization` only when it is a JWT. The local CLI issues both kinds, so both are tested against a real Supabase. Supabase retires the legacy keys at the end of 2026.

10. **Set the Vercel environment variables before the first production build.** The Content-Security-Policy is derived from `NEXT_PUBLIC_API_URL`; the build fails without it, which is much better than shipping a policy that blocks every call to your own API.

### Rebuilding from nothing

This deployment exists. If it ever has to be rebuilt from empty accounts, **[FIRST-DEPLOY.md](FIRST-DEPLOY.md)** is the complete runbook — every command in order, what it should print, and what a failure means. The short version:

```bash
gh repo create clinicalcontext --private --source . --push                  # Render builds from GitHub
uv --directory backend run python -m scripts.migrate --url "$SESSION_URL"   # session pooler, 5432
DATABASE_URL="$SESSION_URL" uv --directory backend run python -m evals.golden.snapshot load --embedder local
# Render → New → Blueprint → the repo → enter the ten sync:false values → Manual Deploy
# Vercel → Add New → Project → root directory frontend → the four NEXT_PUBLIC_* variables → Deploy
# Supabase → Authentication → URL configuration → the Vercel origin
```

### The pipeline

`.github/workflows/deploy.yml`, once the Part 3 secrets exist:

```
push to main → CI (lint, types, schema, tests over the seeded snapshot, secret scan, golden-set gate)
             → secrets check  (names any missing one, before anything runs)
             → a person approves   (only if the Production environment has a required reviewer)
             → migrations     (session pooler, forward-only, checksummed)
             → the API        (Render's deploy hook with ref=<the tested commit>; must return a deploy id)
             → wait           (until /health reports the new commit, not the old process)
             (the website: Vercel's Git integration builds every push itself)
             → smoke test     (ready, headers, a real demo answer, live evals, the page)
             → end-to-end     (opt-in: repository variable E2E_AFTER_DEPLOY=true)
```

The website is not deployed by this job: Vercel's Git integration builds every push to `main` on its own. The sign-up form handles the window in which the website is newer than the API.

CI's pytest step loads `evals/golden/snapshot` first, because the integration suites answer real questions and assert citations, contradictions, cache hits and HNSW index use; a runner's database starts empty. `scripts/preflight.py` runs the same set of checks locally, except that local pytest uses your own database's corpus.

One environment, because this is the free-tier pipeline. A staging rung is the same job twice with a second set of accounts (Supabase allows two free projects; Upstash one free database, so staging would use Render's free Key Value store).

Every step works on one commit, `DEPLOY_SHA`: the one CI tested when CI triggered the run, the branch head when a person ran it. The checkout, the migrations, the Render build (the hook's `ref` parameter) and the wait all use it, because the newest commit on `main` may have arrived after the one that passed. The deploy step also requires Render's own proof that a deploy started — a deploy id, or a 202 for one queued — rather than merely the absence of an HTTP error, since any URL that answers would satisfy the latter.

`/health` reporting the deployed commit is what makes the wait step honest: Render stamps `RENDER_GIT_COMMIT` into the environment, the app adopts it as its `release` (the same value on every trace, error and log line), and the pipeline polls until the value it sees is the SHA it just merged.

The eval gate is what makes this different from a normal pipeline: a change that costs more than two points of recall@10 or faithfulness fails, and any PHI or diagnosis-refusal miss fails unconditionally. A regression in answer quality cannot reach the deployment.

`.github/workflows/jobs.yml` is the worker: the nightly Living Answers sweep and the weekly digest and corpus-freshness pass, on a cron, from the same repository secrets. About 35 short runs a month. It checks its secrets first and skips with a notice when any is missing.

### Health, drain, and restart

- `/health` — the process is alive, and which commit it is. Container healthcheck.
- `/ready` — Postgres and Redis answered. Platform readiness probe; a machine that fails it is taken out of rotation rather than restarted.
- **Draining.** `uvicorn --timeout-graceful-shutdown 60` (the `GRACEFUL_SHUTDOWN_SECONDS` the container reads) stops accepting connections on SIGTERM and finishes what it has; a streamed answer can take tens of seconds. Render's `maxShutdownDelaySeconds: 90` and Fly's `kill_timeout = "90s"` are deliberately longer, so a deploy never cuts an answer in half. Verified by sending SIGTERM with a request in flight: 200, then exit 0.
- The voice WebSocket registry closes its sessions in the lifespan shutdown, so a redeploy ends voice sessions cleanly instead of dropping sockets.

### Logs, traces, errors

Logs are structured JSON on stdout, one object per line, every line carrying `request_id` and — after authentication — `tenant_id`. Any platform log drain that parses JSON makes them queryable; Render and Fly each keep their own log store and can forward to a vendor. The same ids are on the OpenTelemetry spans and on Sentry events, so one id moves between all three. See [OBSERVABILITY.md](OBSERVABILITY.md).

### Backups

Supabase takes automated backups; that is necessary and not sufficient, because an untested backup is a hope. The drill is a command:

```bash
uv run python -m scripts.maintenance restore-drill
```

It dumps the live database, restores it into a throwaway database, and compares the restored copy against the counts taken before and after the dump (a live system moves under a snapshot, so the check is that the restore falls inside that window). It also checks the RLS policy count survived and that a document reads back, then drops the scratch database.

Run on 2026-09-20 against the development database: a 34.7 MB dump, 9,997 documents, 44,143 chunks and their embeddings, 83 RLS policies — all restored, verified, dropped. **PASSED.** Run it against production monthly and after any migration that rewrites data.

Restoring for real is the same command's first half plus a target:

```bash
uv run python -m scripts.maintenance backup --out ./restore-me.dump
pg_restore --no-owner --no-privileges -d "$TARGET_DATABASE_URL" ./restore-me.dump
```

### Verified before the first deploy

| Checked | Result |
|---|---|
| `docker build -f backend/Dockerfile .` | builds, 1.36 GB |
| The image against real Postgres and Redis | `/ready` ok, both dependencies healthy |
| Security headers in `ENVIRONMENT=production` | all six, including HSTS |
| The pipeline's smoke test, verbatim | passes — demo answered with 6 citations, live evals served |
| The worker process in the image | all three jobs ran on their schedules |
| SIGTERM with a request in flight | answer finished with 200, then exit 0 |
| Every migration against an empty database | 23 applied, 34 tables, 83 policies, none without RLS |
| `scripts.maintenance restore-drill` | 34.7 MB dump restored and verified |
| The image under a hard **512 MB** limit (Render's free tier) | 123 MB idle, 214 MB after four uncached answers |
| The image run as Render runs it (`PORT`, `RENDER_GIT_COMMIT`) | binds the port, `/health` reports the commit, uvicorn is PID 1 |
| `render.yaml` against Render's published JSON schema | valid |
| SIGTERM with a request in flight, in-process webhook drain running | answer finished with 200, drain stopped, exit 0 |

### Still open

- **Vendor keys** (Cohere, Anthropic, Deepgram, ElevenLabs, Sentry, LangSmith) — optional. Every code path that uses them is written and inert without them; the deployment runs offline until they are set.
- **A status page.** The probes exist (`/health`, `/ready`) and the methodology page already publishes the eval numbers; a public status page needs a host to point at the probes.
- **The two voice gate checks** that the offline speech stack cannot meet (medical-term error rate, endpoint latency). They are what Deepgram `nova-3-medical` is for; see [VERIFICATION.md](VERIFICATION.md).
