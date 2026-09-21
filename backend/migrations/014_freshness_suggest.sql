-- 014 — Corpus freshness tracking + autocomplete sources.

-- Per-domain corpus staleness (shared corpus stats: org_id NULL, service-written).
CREATE TABLE public.corpus_freshness (
  domain          text PRIMARY KEY,        -- e.g. cardiology, infectious_disease
  last_query      text,
  last_ingested_at timestamptz,
  newest_publication date,
  document_count  int NOT NULL DEFAULT 0,
  checked_at      timestamptz NOT NULL DEFAULT now()
);

-- MeSH terms for query autocomplete (trigram-indexed; populated from the corpus).
CREATE TABLE public.mesh_terms (
  term            text PRIMARY KEY,
  document_count  int NOT NULL DEFAULT 1
);
CREATE INDEX mesh_terms_trgm ON public.mesh_terms USING gin (term gin_trgm_ops);

-- Autocomplete + freshness are non-sensitive shared reference data: readable by
-- all authenticated users, written by the service role (scheduled jobs).
GRANT SELECT ON public.corpus_freshness, public.mesh_terms TO anon, authenticated;
GRANT SELECT, INSERT, UPDATE, DELETE ON public.corpus_freshness, public.mesh_terms
  TO service_role;

ALTER TABLE public.corpus_freshness ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.mesh_terms ENABLE ROW LEVEL SECURITY;
CREATE POLICY corpus_freshness_readable ON public.corpus_freshness
  FOR SELECT TO anon, authenticated USING (true);
CREATE POLICY mesh_terms_readable ON public.mesh_terms
  FOR SELECT TO anon, authenticated USING (true);

-- Org query history for the "your recent queries" side of autocomplete.
CREATE INDEX queries_raw_query_trgm ON public.queries USING gin (raw_query gin_trgm_ops);
