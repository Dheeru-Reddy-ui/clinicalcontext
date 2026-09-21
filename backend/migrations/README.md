# Migrations

Numbered, forward-only SQL files: `NNN_description.sql`, applied in order by
`scripts/migrate.py`, which records `(version, sha256)` in
`public.schema_migrations`. Applied migrations are **immutable** — the runner
aborts on checksum drift; fix forward with a new numbered file. No schema
edits ever happen through the Supabase UI.

## Applying

```bash
# Local compose / CI (plain Postgres needs the Supabase-parity auth shim):
uv run python -m scripts.migrate --local-shim

# Supabase (use the DIRECT / session-mode connection string, port 5432 —
# not the transaction pooler):
uv run python -m scripts.migrate --url "postgresql://postgres:...@db.<ref>.supabase.co:5432/postgres"

# Preview without changing anything:
uv run python -m scripts.migrate --dry-run
```

`--local-shim` applies `local_shim.sql` first: it recreates the minimal
Supabase surface (auth schema, `auth.uid()`/`auth.jwt()`, the
`anon`/`authenticated`/`service_role` roles) so the same migrations and RLS
policies run identically everywhere. The runner refuses the shim if the
target looks like a real Supabase instance.

## Files

| File | Contents |
|------|----------|
| `001_extensions.sql` | pgvector, pg_trgm, uuid-ossp; the `app` helper schema |
| `002_tenancy.sql` | organizations, profiles, org_invites |
| `003_corpus.sql` | documents, chunks (tsvector generated column, vector(1536)), chunk→document org-sync triggers |
| `004_queries.sql` | query domain, api_keys, living answers, binders, annotations, share links, notifications; append-only triggers |
| `005_indexes.sql` | HNSW (cosine), GIN tsv, trigram title, FK btrees |
| `006_rls.sql` | RLS on every table + all policies + grants — the tenant isolation boundary (see its header comment) |

## Conventions

- Every table gets RLS enabled in the same phase it is created (006 covers
  the initial set; future migrations enable RLS inline).
- `org_id IS NULL` on documents/chunks means the shared public corpus:
  readable by all authenticated users, writable only by the service role.
- Append-only tables (`audit_log`, `answer_versions`) are protected by
  triggers that bind every role including `service_role`; the only bypass is
  a superuser deliberately setting `session_replication_role = 'replica'`.
