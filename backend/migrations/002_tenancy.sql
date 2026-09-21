-- 002 — Tenancy: organizations, profiles, invites.
--
-- Membership model: one org per user. The profile row is keyed by the
-- auth.users id and carries the org and the app-level role. The org on the
-- user's JWT (app_metadata.org_id, mirrored by the signup flow) is what RLS
-- trusts; the profile row is the queryable source of truth for the app.

CREATE TYPE public.org_role AS ENUM ('owner', 'clinician', 'viewer');
CREATE TYPE public.org_plan AS ENUM ('free', 'pro', 'enterprise');

CREATE TABLE public.organizations (
  id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  name       text NOT NULL CHECK (char_length(name) BETWEEN 1 AND 200),
  slug       text NOT NULL UNIQUE
             CHECK (slug ~ '^[a-z0-9]+(-[a-z0-9]+)*$' AND char_length(slug) BETWEEN 3 AND 50),
  plan       public.org_plan NOT NULL DEFAULT 'free',
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE public.profiles (
  id         uuid PRIMARY KEY REFERENCES auth.users (id) ON DELETE CASCADE,
  org_id     uuid NOT NULL REFERENCES public.organizations (id) ON DELETE CASCADE,
  role       public.org_role NOT NULL DEFAULT 'clinician',
  full_name  text,
  specialty  text,
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE public.org_invites (
  id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id      uuid NOT NULL REFERENCES public.organizations (id) ON DELETE CASCADE,
  email       text NOT NULL CHECK (position('@' IN email) > 1),
  role        public.org_role NOT NULL DEFAULT 'clinician',
  token       text NOT NULL UNIQUE,
  expires_at  timestamptz NOT NULL,
  accepted_at timestamptz,
  created_at  timestamptz NOT NULL DEFAULT now()
);

-- One live (unaccepted) invite per address per org.
CREATE UNIQUE INDEX org_invites_pending_unique
  ON public.org_invites (org_id, lower(email))
  WHERE accepted_at IS NULL;
