-- 001 — Extensions and foundation schemas.
--
-- The runner sets `search_path = public, extensions` before applying, so
-- CREATE EXTENSION resolves the same way on local compose and on Supabase
-- (where dashboard-enabled extensions live in the `extensions` schema and
-- these IF NOT EXISTS statements become no-ops).

CREATE EXTENSION IF NOT EXISTS vector;       -- pgvector: embeddings + HNSW
CREATE EXTENSION IF NOT EXISTS pg_trgm;      -- trigram search on titles
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";  -- per spec; defaults below use the
                                             -- built-in gen_random_uuid(), which
                                             -- needs no extension and no search_path

-- Application-owned helper schema. Custom functions (JWT claim readers,
-- integrity triggers) live here — NOT in `auth`: Supabase locked the auth
-- schema for customer objects (fresh projects reject CREATE there), and NOT
-- bare in `public` where they would mix with tables.
CREATE SCHEMA IF NOT EXISTS app;
