-- 006 — Row Level Security: the tenant isolation boundary.
--
-- WHY RLS, NOT APPLICATION CODE, IS THE ISOLATION BOUNDARY
-- ---------------------------------------------------------
-- Application-level tenancy ("remember the WHERE org_id = ...") fails open:
-- one forgotten predicate in one new endpoint, one ORM eager-load, one ad-hoc
-- admin script, one SQL injection, or one future consumer of the same
-- database (PostgREST, a reporting job, the Living Answers worker) and
-- another tenant's clinical queries are exposed. RLS fails closed: the
-- policies below are evaluated by Postgres itself on EVERY row access, for
-- every current and future code path, based on a JWT-derived, connection-
-- level context (`request.jwt.claims` + role `authenticated`) that the query
-- author cannot forget to apply and query text cannot override. The
-- application repository layer still scopes every query by tenant (belt and
-- braces, see app/repositories/base.py), but correctness never depends on it:
-- a compromised or buggy app tier degrades to "can only see its own tenant",
-- not "can see everything".
--
-- The one documented exception: `service_role` (BYPASSRLS) — held only by
-- server-side trusted flows (corpus ingestion into the shared corpus, signup
-- provisioning, background Living Answers runs). It is never handed to a
-- client, and append-only tables are protected from it too by triggers
-- (004), which fire regardless of role.
--
-- Placement note: the spec named this helper auth.user_org_id(). Supabase
-- locked the `auth` schema for customer objects (fresh projects reject
-- CREATE FUNCTION there), so it lives in the app schema instead — same
-- contract, portable location.
--
-- The org claim is read from the JWT: top-level `org_id` first (custom
-- access-token hook), falling back to `app_metadata.org_id` (set server-side
-- at signup; users cannot edit app_metadata). Absent claim → NULL → every
-- org-equality predicate below is false → fail closed.

CREATE OR REPLACE FUNCTION app.user_org_id()
RETURNS uuid
LANGUAGE sql
STABLE
AS $$
  SELECT COALESCE(
    NULLIF(current_setting('request.jwt.claims', true), '')::jsonb ->> 'org_id',
    NULLIF(current_setting('request.jwt.claims', true), '')::jsonb
      -> 'app_metadata' ->> 'org_id'
  )::uuid
$$;

-- Privileges ------------------------------------------------------------------
-- Explicit grants keep local compose identical to Supabase's defaults.
-- Row access is then governed entirely by the policies below.
GRANT USAGE ON SCHEMA public TO anon, authenticated, service_role;
GRANT USAGE ON SCHEMA app TO anon, authenticated, service_role;
GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA app TO anon, authenticated, service_role;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public
  TO authenticated, service_role;

-- Infra table: not for clients, regardless of RLS.
REVOKE ALL ON public.schema_migrations FROM authenticated, anon;

-- Belt on top of "no policy exists": the append-only tables lose the
-- privilege outright for authenticated (service_role is stopped by the
-- 004 triggers instead).
REVOKE UPDATE, DELETE ON public.audit_log FROM authenticated;
REVOKE INSERT, UPDATE, DELETE ON public.answer_versions FROM authenticated;

-- Enable RLS everywhere -------------------------------------------------------
ALTER TABLE public.organizations    ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.profiles         ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.org_invites      ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.documents        ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.chunks           ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.query_sessions   ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.queries          ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.answers          ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.retrieval_traces ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.feedback         ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.audit_log        ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.api_keys         ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.followed_answers ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.answer_versions  ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.binders          ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.binder_items     ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.annotations      ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.share_links      ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.notifications    ENABLE ROW LEVEL SECURITY;

-- organizations ---------------------------------------------------------------
-- INSERT/DELETE: service role only (signup provisioning, offboarding).
CREATE POLICY organizations_select_own ON public.organizations
  FOR SELECT TO authenticated
  USING (id = app.user_org_id());

CREATE POLICY organizations_update_own ON public.organizations
  FOR UPDATE TO authenticated
  USING (id = app.user_org_id())
  WITH CHECK (id = app.user_org_id());

-- profiles ----------------------------------------------------------------------
-- INSERT: service role only (created at signup/invite-accept).
CREATE POLICY profiles_select_same_org ON public.profiles
  FOR SELECT TO authenticated
  USING (org_id = app.user_org_id());

CREATE POLICY profiles_update_self ON public.profiles
  FOR UPDATE TO authenticated
  USING (id = auth.uid())
  WITH CHECK (id = auth.uid() AND org_id = app.user_org_id());

-- org_invites -------------------------------------------------------------------
CREATE POLICY org_invites_select_own_org ON public.org_invites
  FOR SELECT TO authenticated
  USING (org_id = app.user_org_id());

CREATE POLICY org_invites_insert_own_org ON public.org_invites
  FOR INSERT TO authenticated
  WITH CHECK (org_id = app.user_org_id());

CREATE POLICY org_invites_update_own_org ON public.org_invites
  FOR UPDATE TO authenticated
  USING (org_id = app.user_org_id())
  WITH CHECK (org_id = app.user_org_id());

CREATE POLICY org_invites_delete_own_org ON public.org_invites
  FOR DELETE TO authenticated
  USING (org_id = app.user_org_id());

-- documents ---------------------------------------------------------------------
-- Two SELECT policies (OR-combined): own tenant rows, plus the shared public
-- corpus (org_id IS NULL). No write policy matches org_id IS NULL, so the
-- shared corpus is read-only for every authenticated user — only the
-- service role (ingestion) writes it. WITH CHECK on update pins org_id: a
-- document can be neither donated to the public corpus nor moved to another
-- tenant by a client.
CREATE POLICY documents_select_own_org ON public.documents
  FOR SELECT TO authenticated
  USING (org_id = app.user_org_id());

CREATE POLICY documents_select_shared ON public.documents
  FOR SELECT TO authenticated
  USING (org_id IS NULL);

CREATE POLICY documents_insert_own_org ON public.documents
  FOR INSERT TO authenticated
  WITH CHECK (org_id = app.user_org_id());

CREATE POLICY documents_update_own_org ON public.documents
  FOR UPDATE TO authenticated
  USING (org_id = app.user_org_id())
  WITH CHECK (org_id = app.user_org_id());

CREATE POLICY documents_delete_own_org ON public.documents
  FOR DELETE TO authenticated
  USING (org_id = app.user_org_id());

-- chunks (mirrors documents) ------------------------------------------------------
CREATE POLICY chunks_select_own_org ON public.chunks
  FOR SELECT TO authenticated
  USING (org_id = app.user_org_id());

CREATE POLICY chunks_select_shared ON public.chunks
  FOR SELECT TO authenticated
  USING (org_id IS NULL);

CREATE POLICY chunks_insert_own_org ON public.chunks
  FOR INSERT TO authenticated
  WITH CHECK (org_id = app.user_org_id());

CREATE POLICY chunks_update_own_org ON public.chunks
  FOR UPDATE TO authenticated
  USING (org_id = app.user_org_id())
  WITH CHECK (org_id = app.user_org_id());

CREATE POLICY chunks_delete_own_org ON public.chunks
  FOR DELETE TO authenticated
  USING (org_id = app.user_org_id());

-- query_sessions ------------------------------------------------------------------
-- No DELETE policy: sessions anchor the clinical query record.
CREATE POLICY query_sessions_select_own_org ON public.query_sessions
  FOR SELECT TO authenticated
  USING (org_id = app.user_org_id());

CREATE POLICY query_sessions_insert_own ON public.query_sessions
  FOR INSERT TO authenticated
  WITH CHECK (org_id = app.user_org_id() AND user_id = auth.uid());

CREATE POLICY query_sessions_update_own_org ON public.query_sessions
  FOR UPDATE TO authenticated
  USING (org_id = app.user_org_id())
  WITH CHECK (org_id = app.user_org_id());

-- queries ---------------------------------------------------------------------------
-- No DELETE policy: queries are the clinical record.
CREATE POLICY queries_select_own_org ON public.queries
  FOR SELECT TO authenticated
  USING (org_id = app.user_org_id());

CREATE POLICY queries_insert_own ON public.queries
  FOR INSERT TO authenticated
  WITH CHECK (org_id = app.user_org_id() AND user_id = auth.uid());

CREATE POLICY queries_update_own_org ON public.queries
  FOR UPDATE TO authenticated
  USING (org_id = app.user_org_id())
  WITH CHECK (org_id = app.user_org_id());

-- answers -----------------------------------------------------------------------
-- SELECT + INSERT only: answers are written once by the pipeline running in
-- the user's context; revisions live in answer_versions, never in place.
CREATE POLICY answers_select_own_org ON public.answers
  FOR SELECT TO authenticated
  USING (org_id = app.user_org_id());

CREATE POLICY answers_insert_own_org ON public.answers
  FOR INSERT TO authenticated
  WITH CHECK (org_id = app.user_org_id());

-- retrieval_traces -----------------------------------------------------------------
CREATE POLICY retrieval_traces_select_own_org ON public.retrieval_traces
  FOR SELECT TO authenticated
  USING (org_id = app.user_org_id());

CREATE POLICY retrieval_traces_insert_own_org ON public.retrieval_traces
  FOR INSERT TO authenticated
  WITH CHECK (org_id = app.user_org_id());

-- feedback ---------------------------------------------------------------------------
CREATE POLICY feedback_select_own_org ON public.feedback
  FOR SELECT TO authenticated
  USING (org_id = app.user_org_id());

CREATE POLICY feedback_insert_own ON public.feedback
  FOR INSERT TO authenticated
  WITH CHECK (org_id = app.user_org_id() AND user_id = auth.uid());

CREATE POLICY feedback_update_own ON public.feedback
  FOR UPDATE TO authenticated
  USING (org_id = app.user_org_id() AND user_id = auth.uid())
  WITH CHECK (org_id = app.user_org_id() AND user_id = auth.uid());

CREATE POLICY feedback_delete_own ON public.feedback
  FOR DELETE TO authenticated
  USING (org_id = app.user_org_id() AND user_id = auth.uid());

-- audit_log -----------------------------------------------------------------------
-- Insert-only, deliberately: SELECT for the org (the app narrows to owners),
-- INSERT within the org, and NO update/delete policy for anyone — combined
-- with the 004 trigger and the privilege REVOKE above.
CREATE POLICY audit_log_select_own_org ON public.audit_log
  FOR SELECT TO authenticated
  USING (org_id = app.user_org_id());

CREATE POLICY audit_log_insert_own_org ON public.audit_log
  FOR INSERT TO authenticated
  WITH CHECK (
    org_id = app.user_org_id()
    AND (user_id = auth.uid() OR user_id IS NULL)
  );

-- api_keys -----------------------------------------------------------------------
CREATE POLICY api_keys_select_own_org ON public.api_keys
  FOR SELECT TO authenticated
  USING (org_id = app.user_org_id());

CREATE POLICY api_keys_insert_own ON public.api_keys
  FOR INSERT TO authenticated
  WITH CHECK (org_id = app.user_org_id() AND created_by = auth.uid());

CREATE POLICY api_keys_update_own_org ON public.api_keys
  FOR UPDATE TO authenticated
  USING (org_id = app.user_org_id())
  WITH CHECK (org_id = app.user_org_id());

CREATE POLICY api_keys_delete_own_org ON public.api_keys
  FOR DELETE TO authenticated
  USING (org_id = app.user_org_id());

-- followed_answers (personal) -------------------------------------------------------
CREATE POLICY followed_answers_select_own ON public.followed_answers
  FOR SELECT TO authenticated
  USING (org_id = app.user_org_id() AND user_id = auth.uid());

CREATE POLICY followed_answers_insert_own ON public.followed_answers
  FOR INSERT TO authenticated
  WITH CHECK (org_id = app.user_org_id() AND user_id = auth.uid());

CREATE POLICY followed_answers_update_own ON public.followed_answers
  FOR UPDATE TO authenticated
  USING (org_id = app.user_org_id() AND user_id = auth.uid())
  WITH CHECK (org_id = app.user_org_id() AND user_id = auth.uid());

CREATE POLICY followed_answers_delete_own ON public.followed_answers
  FOR DELETE TO authenticated
  USING (org_id = app.user_org_id() AND user_id = auth.uid());

-- answer_versions --------------------------------------------------------------------
-- Read-only for tenants; written by the Living Answers service flow only.
CREATE POLICY answer_versions_select_own_org ON public.answer_versions
  FOR SELECT TO authenticated
  USING (org_id = app.user_org_id());

-- binders ------------------------------------------------------------------------
-- visibility='private' binders are creator-only; 'org' binders are org-wide.
CREATE POLICY binders_select_visible ON public.binders
  FOR SELECT TO authenticated
  USING (
    org_id = app.user_org_id()
    AND (visibility = 'org' OR created_by = auth.uid())
  );

CREATE POLICY binders_insert_own ON public.binders
  FOR INSERT TO authenticated
  WITH CHECK (org_id = app.user_org_id() AND created_by = auth.uid());

CREATE POLICY binders_update_own ON public.binders
  FOR UPDATE TO authenticated
  USING (org_id = app.user_org_id() AND created_by = auth.uid())
  WITH CHECK (org_id = app.user_org_id() AND created_by = auth.uid());

CREATE POLICY binders_delete_own ON public.binders
  FOR DELETE TO authenticated
  USING (org_id = app.user_org_id() AND created_by = auth.uid());

-- binder_items ------------------------------------------------------------------
-- Visibility rides on the parent binder: the EXISTS subquery runs under the
-- caller's own RLS, so items of another user's private binder are invisible.
CREATE POLICY binder_items_select_visible ON public.binder_items
  FOR SELECT TO authenticated
  USING (
    org_id = app.user_org_id()
    AND EXISTS (SELECT 1 FROM public.binders b WHERE b.id = binder_id)
  );

CREATE POLICY binder_items_insert_own ON public.binder_items
  FOR INSERT TO authenticated
  WITH CHECK (
    org_id = app.user_org_id()
    AND added_by = auth.uid()
    AND EXISTS (SELECT 1 FROM public.binders b WHERE b.id = binder_id)
  );

CREATE POLICY binder_items_update_visible ON public.binder_items
  FOR UPDATE TO authenticated
  USING (
    org_id = app.user_org_id()
    AND EXISTS (SELECT 1 FROM public.binders b WHERE b.id = binder_id)
  )
  WITH CHECK (org_id = app.user_org_id());

CREATE POLICY binder_items_delete_visible ON public.binder_items
  FOR DELETE TO authenticated
  USING (
    org_id = app.user_org_id()
    AND EXISTS (SELECT 1 FROM public.binders b WHERE b.id = binder_id)
  );

-- annotations -------------------------------------------------------------------
-- Readable org-wide unless attached to a binder item the caller cannot see.
CREATE POLICY annotations_select_visible ON public.annotations
  FOR SELECT TO authenticated
  USING (
    org_id = app.user_org_id()
    AND (
      binder_item_id IS NULL
      OR EXISTS (SELECT 1 FROM public.binder_items bi WHERE bi.id = binder_item_id)
    )
  );

CREATE POLICY annotations_insert_own ON public.annotations
  FOR INSERT TO authenticated
  WITH CHECK (
    org_id = app.user_org_id()
    AND user_id = auth.uid()
    AND (
      binder_item_id IS NULL
      OR EXISTS (SELECT 1 FROM public.binder_items bi WHERE bi.id = binder_item_id)
    )
  );

CREATE POLICY annotations_update_own ON public.annotations
  FOR UPDATE TO authenticated
  USING (org_id = app.user_org_id() AND user_id = auth.uid())
  WITH CHECK (org_id = app.user_org_id() AND user_id = auth.uid());

CREATE POLICY annotations_delete_own ON public.annotations
  FOR DELETE TO authenticated
  USING (org_id = app.user_org_id() AND user_id = auth.uid());

-- share_links --------------------------------------------------------------------
-- The public read path for a shared answer goes through the backend with the
-- service role (slug lookup), so anon needs no policy here.
CREATE POLICY share_links_select_own_org ON public.share_links
  FOR SELECT TO authenticated
  USING (org_id = app.user_org_id());

CREATE POLICY share_links_insert_own ON public.share_links
  FOR INSERT TO authenticated
  WITH CHECK (org_id = app.user_org_id() AND created_by = auth.uid());

CREATE POLICY share_links_update_own_org ON public.share_links
  FOR UPDATE TO authenticated
  USING (org_id = app.user_org_id())
  WITH CHECK (org_id = app.user_org_id());

CREATE POLICY share_links_delete_own_org ON public.share_links
  FOR DELETE TO authenticated
  USING (org_id = app.user_org_id());

-- notifications (personal; created by the service role) ---------------------------
CREATE POLICY notifications_select_own ON public.notifications
  FOR SELECT TO authenticated
  USING (org_id = app.user_org_id() AND user_id = auth.uid());

CREATE POLICY notifications_update_own ON public.notifications
  FOR UPDATE TO authenticated
  USING (org_id = app.user_org_id() AND user_id = auth.uid())
  WITH CHECK (org_id = app.user_org_id() AND user_id = auth.uid());

CREATE POLICY notifications_delete_own ON public.notifications
  FOR DELETE TO authenticated
  USING (org_id = app.user_org_id() AND user_id = auth.uid());
