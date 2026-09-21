-- 022 — Phase 13: the public demo tenant and weekly-digest preferences.
--
-- The marketing page runs one real query without a login. Nothing in the
-- pipeline can run without a tenant (RLS, the ledger, the answer row), so
-- the demo runs as a fixed, real tenant with a fixed, real user — created
-- here with stable ids so the API can refer to them without configuration,
-- and rate-limited per client address at the endpoint. The user is a viewer
-- with no credential: nobody can log in as it, and everything the demo
-- writes (queries, answers, cost events) is ordinary tenant data that can be
-- inspected or purged like any other tenant's.
--
-- The weekly evidence digest is opt-in per user: in-app always when opted
-- in, email additionally when the user says so. `last_sent_at` is what the
-- job reads to pick the week's window and to never send twice.

INSERT INTO public.organizations (id, name, slug, plan)
VALUES ('00000000-0000-4000-8000-000000000d30', 'Public demo', 'public-demo', 'pro')
ON CONFLICT (id) DO NOTHING;

INSERT INTO auth.users (id, email, raw_user_meta_data)
VALUES (
  '00000000-0000-4000-8000-000000000d31',
  'demo@clinicalcontext.invalid',
  '{"full_name": "Public demo"}'::jsonb
)
ON CONFLICT (id) DO NOTHING;

INSERT INTO public.profiles (id, org_id, role, full_name)
VALUES (
  '00000000-0000-4000-8000-000000000d31',
  '00000000-0000-4000-8000-000000000d30',
  'viewer',
  'Public demo'
)
ON CONFLICT (id) DO NOTHING;

CREATE TABLE public.digest_preferences (
  user_id      uuid PRIMARY KEY REFERENCES auth.users (id) ON DELETE CASCADE,
  org_id       uuid NOT NULL REFERENCES public.organizations (id) ON DELETE CASCADE,
  enabled      boolean NOT NULL DEFAULT false,   -- the in-app digest
  email        boolean NOT NULL DEFAULT false,   -- and an email copy
  last_sent_at timestamptz,
  created_at   timestamptz NOT NULL DEFAULT now(),
  updated_at   timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX digest_preferences_enabled_idx ON public.digest_preferences (org_id)
  WHERE enabled;

GRANT SELECT, INSERT, UPDATE ON public.digest_preferences TO authenticated, service_role;

ALTER TABLE public.digest_preferences ENABLE ROW LEVEL SECURITY;

CREATE POLICY digest_preferences_own_row ON public.digest_preferences
  FOR ALL TO authenticated
  USING (user_id = auth.uid() AND org_id = app.user_org_id())
  WITH CHECK (user_id = auth.uid() AND org_id = app.user_org_id());
