-- 004 — Query domain: sessions, queries, answers, traces, feedback, audit,
-- API keys, living answers, binders, annotations, share links, notifications.
--
-- Deletion philosophy:
--   * queries/answers are the clinical record — no authenticated DELETE
--     policies exist (006), and answer_versions/audit_log are append-only,
--     enforced by triggers that even the service role cannot bypass.
--   * user deletion never destroys org history: historical user_id columns
--     are ON DELETE SET NULL; personal rows (notifications, follows) cascade.
--   * audit_log carries no FKs at all — audit rows must outlive the users
--     and orgs they describe, and FK cascades would either delete or mutate
--     them (both are forbidden here).

CREATE TYPE public.query_type AS ENUM (
  'therapy', 'diagnosis', 'prognosis', 'etiology', 'guideline_comparison', 'other'
);

CREATE TYPE public.query_status AS ENUM (
  'pending', 'running', 'completed', 'failed', 'blocked'
);

CREATE TYPE public.confidence_level AS ENUM ('high', 'moderate', 'low');

CREATE TYPE public.feedback_rating AS ENUM ('up', 'down');

CREATE TYPE public.feedback_reason AS ENUM (
  'incorrect', 'outdated', 'missing_evidence', 'citation_mismatch', 'unclear', 'other'
);

CREATE TYPE public.binder_visibility AS ENUM ('private', 'org');

CREATE TYPE public.binder_item_type AS ENUM ('answer', 'passage');

-- Sessions group a user's queries (the History screen's unit of navigation).
CREATE TABLE public.query_sessions (
  id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id     uuid NOT NULL REFERENCES public.organizations (id) ON DELETE CASCADE,
  user_id    uuid REFERENCES auth.users (id) ON DELETE SET NULL,
  title      text,
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE public.queries (
  id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  session_id        uuid NOT NULL REFERENCES public.query_sessions (id) ON DELETE CASCADE,
  org_id            uuid NOT NULL REFERENCES public.organizations (id) ON DELETE CASCADE,
  user_id           uuid REFERENCES auth.users (id) ON DELETE SET NULL,
  raw_query         text NOT NULL,
  normalized_query  text,
  query_type        public.query_type,
  guardrail_verdict jsonb NOT NULL DEFAULT '{}'::jsonb,
  status            public.query_status NOT NULL DEFAULT 'pending',
  created_at        timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE public.answers (
  id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  query_id         uuid NOT NULL REFERENCES public.queries (id) ON DELETE CASCADE,
  org_id           uuid NOT NULL REFERENCES public.organizations (id) ON DELETE CASCADE,
  content          text NOT NULL,
  citations        jsonb NOT NULL DEFAULT '[]'::jsonb,
  confidence       public.confidence_level,
  evidence_grade   public.evidence_grade,
  has_contradiction boolean NOT NULL DEFAULT false,
  abstained        boolean NOT NULL DEFAULT false,
  model            text NOT NULL,
  prompt_version   text NOT NULL,
  latency_ms       int,
  input_tokens     int,
  output_tokens    int,
  cost_usd         numeric(10, 6),
  langsmith_run_id text,
  created_at       timestamptz NOT NULL DEFAULT now()
);

-- One row per retrieval stage per query (bm25 / dense / fused / reranked / …).
-- stage stays text, not enum: stages evolve with the pipeline and traces are
-- diagnostic data, not domain state.
CREATE TABLE public.retrieval_traces (
  id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  query_id    uuid NOT NULL REFERENCES public.queries (id) ON DELETE CASCADE,
  org_id      uuid NOT NULL REFERENCES public.organizations (id) ON DELETE CASCADE,
  stage       text NOT NULL,
  chunk_ids   uuid[] NOT NULL DEFAULT '{}',
  scores      double precision[] NOT NULL DEFAULT '{}',
  duration_ms double precision,
  created_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE public.feedback (
  id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  answer_id  uuid NOT NULL REFERENCES public.answers (id) ON DELETE CASCADE,
  org_id     uuid NOT NULL REFERENCES public.organizations (id) ON DELETE CASCADE,
  user_id    uuid REFERENCES auth.users (id) ON DELETE SET NULL,
  rating     public.feedback_rating NOT NULL,
  reason     public.feedback_reason,
  comment    text,
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (answer_id, user_id)
);

-- Append-only. No FKs by design (see header). Immutability enforced by
-- trigger below on top of "no UPDATE/DELETE policy exists" (006).
CREATE TABLE public.audit_log (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id        uuid NOT NULL,
  user_id       uuid,
  action        text NOT NULL,
  resource_type text,
  resource_id   uuid,
  payload       jsonb NOT NULL DEFAULT '{}'::jsonb,
  ip            inet,
  created_at    timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE public.api_keys (
  id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id       uuid NOT NULL REFERENCES public.organizations (id) ON DELETE CASCADE,
  name         text NOT NULL,
  key_hash     text NOT NULL UNIQUE,   -- sha256 of the full key; plaintext never stored
  key_prefix   text NOT NULL,          -- first chars shown in the UI ("cck_a1b2…")
  scopes       text[] NOT NULL DEFAULT '{}',
  last_used_at timestamptz,
  revoked_at   timestamptz,
  created_by   uuid REFERENCES auth.users (id) ON DELETE SET NULL,
  created_at   timestamptz NOT NULL DEFAULT now()
);

-- Living Answers: a user follows an answer; background re-runs diff against it.
CREATE TABLE public.followed_answers (
  id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  answer_id    uuid NOT NULL REFERENCES public.answers (id) ON DELETE CASCADE,
  org_id       uuid NOT NULL REFERENCES public.organizations (id) ON DELETE CASCADE,
  user_id      uuid NOT NULL REFERENCES auth.users (id) ON DELETE CASCADE,
  notify_email boolean NOT NULL DEFAULT true,
  created_at   timestamptz NOT NULL DEFAULT now(),
  UNIQUE (answer_id, user_id)
);

-- Immutable version history for Living Answers. RESTRICT (not CASCADE) on
-- answer_id: an answer with recorded versions cannot be deleted — deleting
-- history is exactly what this table exists to prevent.
CREATE TABLE public.answer_versions (
  id                          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  answer_id                   uuid NOT NULL REFERENCES public.answers (id) ON DELETE RESTRICT,
  org_id                      uuid NOT NULL REFERENCES public.organizations (id) ON DELETE RESTRICT,
  version                     int NOT NULL CHECK (version >= 1),
  content                     text NOT NULL,
  citations                   jsonb NOT NULL DEFAULT '[]'::jsonb,
  confidence                  public.confidence_level,
  diff                        jsonb,
  superseded_by_document_ids  uuid[] NOT NULL DEFAULT '{}',
  created_at                  timestamptz NOT NULL DEFAULT now(),
  UNIQUE (answer_id, version)
);

CREATE TABLE public.binders (
  id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id      uuid NOT NULL REFERENCES public.organizations (id) ON DELETE CASCADE,
  created_by  uuid REFERENCES auth.users (id) ON DELETE SET NULL,
  title       text NOT NULL,
  description text,
  visibility  public.binder_visibility NOT NULL DEFAULT 'private',
  created_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE public.binder_items (
  id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  binder_id  uuid NOT NULL REFERENCES public.binders (id) ON DELETE CASCADE,
  org_id     uuid NOT NULL REFERENCES public.organizations (id) ON DELETE CASCADE,
  item_type  public.binder_item_type NOT NULL,
  answer_id  uuid REFERENCES public.answers (id) ON DELETE CASCADE,
  chunk_id   uuid REFERENCES public.chunks (id) ON DELETE CASCADE,
  position   int NOT NULL DEFAULT 0,
  added_by   uuid REFERENCES auth.users (id) ON DELETE SET NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  CHECK (
    (item_type = 'answer'  AND answer_id IS NOT NULL AND chunk_id IS NULL) OR
    (item_type = 'passage' AND chunk_id  IS NOT NULL AND answer_id IS NULL)
  )
);

CREATE TABLE public.annotations (
  id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id          uuid NOT NULL REFERENCES public.organizations (id) ON DELETE CASCADE,
  user_id         uuid REFERENCES auth.users (id) ON DELETE SET NULL,
  binder_item_id  uuid REFERENCES public.binder_items (id) ON DELETE CASCADE,
  chunk_id        uuid REFERENCES public.chunks (id) ON DELETE CASCADE,
  highlight_range jsonb,
  body            text NOT NULL,
  parent_id       uuid REFERENCES public.annotations (id) ON DELETE CASCADE,  -- threads
  created_at      timestamptz NOT NULL DEFAULT now(),
  CHECK (binder_item_id IS NOT NULL OR chunk_id IS NOT NULL)
);

CREATE TABLE public.share_links (
  id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id     uuid NOT NULL REFERENCES public.organizations (id) ON DELETE CASCADE,
  answer_id  uuid NOT NULL REFERENCES public.answers (id) ON DELETE CASCADE,
  slug       text NOT NULL UNIQUE,
  enabled    boolean NOT NULL DEFAULT true,
  created_by uuid REFERENCES auth.users (id) ON DELETE SET NULL,
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE public.notifications (
  id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id     uuid NOT NULL REFERENCES public.organizations (id) ON DELETE CASCADE,
  user_id    uuid NOT NULL REFERENCES auth.users (id) ON DELETE CASCADE,
  type       text NOT NULL,        -- e.g. answer_updated, org_invite, system
  payload    jsonb NOT NULL DEFAULT '{}'::jsonb,
  read_at    timestamptz,
  created_at timestamptz NOT NULL DEFAULT now()
);

-- Append-only enforcement. Fires before RLS is even consulted and applies to
-- every role including service_role — only a superuser deliberately using
-- session_replication_role = 'replica' (a break-glass maintenance act) can
-- skip it.
CREATE OR REPLACE FUNCTION app.forbid_mutation()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
  RAISE EXCEPTION '% is append-only: % denied', TG_TABLE_NAME, TG_OP
    USING ERRCODE = 'raise_exception';
END;
$$;

CREATE TRIGGER audit_log_append_only
  BEFORE UPDATE OR DELETE ON public.audit_log
  FOR EACH ROW EXECUTE FUNCTION app.forbid_mutation();

CREATE TRIGGER answer_versions_append_only
  BEFORE UPDATE OR DELETE ON public.answer_versions
  FOR EACH ROW EXECUTE FUNCTION app.forbid_mutation();
