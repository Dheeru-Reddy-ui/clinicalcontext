-- ============================================================================
-- LOCAL / CI PARITY SHIM — NEVER APPLIED TO SUPABASE
-- ============================================================================
-- Supabase ships an `auth` schema (users table, uid()/jwt() helpers) and the
-- `anon` / `authenticated` / `service_role` roles. Plain Postgres does not.
-- This file recreates that minimal surface so the SAME numbered migrations
-- and the SAME RLS policies run identically on docker-compose, CI, and
-- Supabase. It is idempotent and intentionally NOT a numbered migration:
-- scripts/migrate.py applies it only with --local-shim, and refuses to if it
-- detects a real Supabase instance (supabase_auth_admin role present).
--
-- The JWT context works exactly like Supabase/PostgREST: each request(-scoped
-- transaction) runs as role `authenticated` with the JWT claims JSON in the
-- `request.jwt.claims` setting; auth.uid()/auth.jwt() read from it.
-- ============================================================================

-- Roles ----------------------------------------------------------------------
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') THEN
    CREATE ROLE anon NOLOGIN;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') THEN
    CREATE ROLE authenticated NOLOGIN;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'service_role') THEN
    -- Mirrors Supabase: service_role bypasses RLS. Server-side only, never
    -- exposed to clients.
    CREATE ROLE service_role NOLOGIN BYPASSRLS;
  END IF;
END
$$;

-- Schemas --------------------------------------------------------------------
CREATE SCHEMA IF NOT EXISTS auth;
-- Supabase installs extensions into this schema; some SQL may qualify names.
CREATE SCHEMA IF NOT EXISTS extensions;

GRANT USAGE ON SCHEMA auth TO anon, authenticated, service_role;
GRANT USAGE ON SCHEMA extensions TO anon, authenticated, service_role;

-- Minimal auth.users (Supabase manages the real one) --------------------------
CREATE TABLE IF NOT EXISTS auth.users (
  id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  email              text UNIQUE,
  raw_app_meta_data  jsonb NOT NULL DEFAULT '{}'::jsonb,
  raw_user_meta_data jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at         timestamptz NOT NULL DEFAULT now()
);

-- JWT helpers, byte-compatible with Supabase's --------------------------------
CREATE OR REPLACE FUNCTION auth.jwt()
RETURNS jsonb
LANGUAGE sql
STABLE
AS $$
  SELECT NULLIF(current_setting('request.jwt.claims', true), '')::jsonb
$$;

CREATE OR REPLACE FUNCTION auth.uid()
RETURNS uuid
LANGUAGE sql
STABLE
AS $$
  SELECT (NULLIF(current_setting('request.jwt.claims', true), '')::jsonb ->> 'sub')::uuid
$$;

CREATE OR REPLACE FUNCTION auth.role()
RETURNS text
LANGUAGE sql
STABLE
AS $$
  SELECT NULLIF(current_setting('request.jwt.claims', true), '')::jsonb ->> 'role'
$$;

GRANT EXECUTE ON FUNCTION auth.jwt(), auth.uid(), auth.role()
  TO anon, authenticated, service_role;
