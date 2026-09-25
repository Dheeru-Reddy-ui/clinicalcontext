-- 026: personal settings, and owner-only writes enforced by the database.
--
-- 1. public.user_preferences holds the Settings page's personal choices —
--    the ones that should follow a person from laptop to phone: who the
--    assistant writes for by default, how answers open, the read-aloud voice
--    and pace, whether a spoken conversation keeps listening, and what Learn
--    shows first. One row per person, written on first save; no row means
--    the defaults, so nothing is backfilled.
--
-- 2. Owner-only writes, enforced below the API. The API has always required
--    the owner role to change the organization, send invites, and manage API
--    keys and webhooks — but the grants and policies underneath let ANY
--    member do those writes directly, through the Supabase Data API, which
--    serves the public schema to anyone holding a signed-in user's token. A
--    viewer could have made themselves an owner (profiles.role), raised the
--    plan, invited a second account as owner, or minted a full-scope API key
--    (which the API maps to the owner role). From here:
--      - profiles:      a person may change only their own name and specialty;
--      - organizations: owners only, and never the plan or the slug;
--      - org_invites, api_keys: owners only, for every operation;
--      - webhooks:      owners write; members read them, minus the secret.
--    app.is_org_owner() reads the caller's own profile row as a security
--    definer with a fixed search_path, so these policies do not depend on the
--    profiles policies beside them. The service role (signup, invite
--    acceptance, the owner-checked service paths) is unaffected: it bypasses
--    RLS and keeps its table grants.

-- 1. Personal settings ----------------------------------------------------------

CREATE TABLE public.user_preferences (
  user_id              uuid PRIMARY KEY REFERENCES auth.users (id) ON DELETE CASCADE,
  org_id               uuid NOT NULL REFERENCES public.organizations (id) ON DELETE CASCADE,
  -- Who answers are written for when a conversation starts.
  audience             text NOT NULL DEFAULT 'patient'
                       CHECK (audience IN ('patient', 'clinician', 'student')),
  sources_open         boolean NOT NULL DEFAULT false,
  show_timeline        boolean NOT NULL DEFAULT true,
  -- The read-aloud voice: a short name the API checks against the voices it
  -- offers (so a new voice needs no migration), and the playback pace.
  voice_name           text NOT NULL DEFAULT 'thalia' CHECK (voice_name ~ '^[a-z]{2,20}$'),
  voice_rate           numeric(3, 2) NOT NULL DEFAULT 1.00 CHECK (voice_rate BETWEEN 0.75 AND 1.50),
  voice_continuous     boolean NOT NULL DEFAULT true,
  -- Learn: which subjects to list, the default teaching depth, and pinned ones.
  learn_scope          text NOT NULL DEFAULT 'all' CHECK (learn_scope IN ('all', 'mbbs', 'pg')),
  learn_depth          text NOT NULL DEFAULT 'auto' CHECK (learn_depth IN ('auto', 'mbbs', 'pg')),
  followed_specialties text[] NOT NULL DEFAULT '{}'
                       CHECK (cardinality(followed_specialties) <= 60),
  created_at           timestamptz NOT NULL DEFAULT now(),
  updated_at           timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX user_preferences_org_idx ON public.user_preferences (org_id);

GRANT SELECT, INSERT, UPDATE ON public.user_preferences TO authenticated, service_role;
GRANT DELETE ON public.user_preferences TO service_role;

ALTER TABLE public.user_preferences ENABLE ROW LEVEL SECURITY;

CREATE POLICY user_preferences_own_row ON public.user_preferences
  FOR ALL TO authenticated
  USING (user_id = auth.uid() AND org_id = app.user_org_id())
  WITH CHECK (user_id = auth.uid() AND org_id = app.user_org_id());

-- 2. Owner-only writes -----------------------------------------------------------

CREATE OR REPLACE FUNCTION app.is_org_owner()
RETURNS boolean
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = ''
AS $$
  SELECT EXISTS (
    SELECT 1
    FROM public.profiles AS p
    WHERE p.id = auth.uid()
      AND p.org_id = app.user_org_id()
      AND p.role = 'owner'
  )
$$;
REVOKE ALL ON FUNCTION app.is_org_owner() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION app.is_org_owner() TO authenticated, service_role;

-- profiles: your own name and specialty — never your role or organization.
REVOKE UPDATE ON public.profiles FROM authenticated;
GRANT UPDATE (full_name, specialty) ON public.profiles TO authenticated;

-- organizations: owners only, and only the settings the product lets them change.
DROP POLICY organizations_update_own ON public.organizations;
CREATE POLICY organizations_update_owner ON public.organizations
  FOR UPDATE TO authenticated
  USING (id = app.user_org_id() AND app.is_org_owner())
  WITH CHECK (id = app.user_org_id() AND app.is_org_owner());
REVOKE UPDATE ON public.organizations FROM authenticated;
GRANT UPDATE (name, voice_tts_quality, public_sharing_enabled) ON public.organizations
  TO authenticated;

-- org_invites: an invite carries a role and a token; only owners see or make them.
DROP POLICY org_invites_select_own_org ON public.org_invites;
DROP POLICY org_invites_insert_own_org ON public.org_invites;
DROP POLICY org_invites_update_own_org ON public.org_invites;
DROP POLICY org_invites_delete_own_org ON public.org_invites;
CREATE POLICY org_invites_owner ON public.org_invites
  FOR ALL TO authenticated
  USING (org_id = app.user_org_id() AND app.is_org_owner())
  WITH CHECK (org_id = app.user_org_id() AND app.is_org_owner());

-- api_keys: a key acts with the role of its scope, so only owners hold the pen.
DROP POLICY api_keys_select_own_org ON public.api_keys;
DROP POLICY api_keys_insert_own ON public.api_keys;
DROP POLICY api_keys_update_own_org ON public.api_keys;
DROP POLICY api_keys_delete_own_org ON public.api_keys;
CREATE POLICY api_keys_owner_select ON public.api_keys
  FOR SELECT TO authenticated
  USING (org_id = app.user_org_id() AND app.is_org_owner());
CREATE POLICY api_keys_owner_insert ON public.api_keys
  FOR INSERT TO authenticated
  WITH CHECK (org_id = app.user_org_id() AND created_by = auth.uid() AND app.is_org_owner());
CREATE POLICY api_keys_owner_update ON public.api_keys
  FOR UPDATE TO authenticated
  USING (org_id = app.user_org_id() AND app.is_org_owner())
  WITH CHECK (org_id = app.user_org_id() AND app.is_org_owner());
CREATE POLICY api_keys_owner_delete ON public.api_keys
  FOR DELETE TO authenticated
  USING (org_id = app.user_org_id() AND app.is_org_owner());

-- webhooks: they send organization events to an outside address, so only
-- owners add, change or remove them. Every member still READS them: a
-- member's own request queues the deliveries its work produced, in its own
-- transaction (017), and has to see which webhooks subscribe. What members
-- no longer read is the signing secret — it stays with the service role
-- (the delivery worker) and the owner who was shown it once.
DROP POLICY webhooks_own_org ON public.webhooks;
CREATE POLICY webhooks_select_own_org ON public.webhooks
  FOR SELECT TO authenticated
  USING (org_id = app.user_org_id());
CREATE POLICY webhooks_owner_insert ON public.webhooks
  FOR INSERT TO authenticated
  WITH CHECK (org_id = app.user_org_id() AND app.is_org_owner());
CREATE POLICY webhooks_owner_update ON public.webhooks
  FOR UPDATE TO authenticated
  USING (org_id = app.user_org_id() AND app.is_org_owner())
  WITH CHECK (org_id = app.user_org_id() AND app.is_org_owner());
CREATE POLICY webhooks_owner_delete ON public.webhooks
  FOR DELETE TO authenticated
  USING (org_id = app.user_org_id() AND app.is_org_owner());
REVOKE SELECT ON public.webhooks FROM authenticated;
GRANT SELECT (id, org_id, url, events, enabled, created_by, created_at) ON public.webhooks
  TO authenticated;
