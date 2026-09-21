-- 003 — Corpus: documents and chunks.
--
-- org_id semantics (the core of the corpus model):
--   * org_id IS NULL     → shared public corpus (PubMed/PMC/guidelines),
--                          readable by every authenticated user, writable only
--                          by the service role (ingestion pipeline).
--   * org_id IS NOT NULL → a tenant's private upload, visible to that org only.

CREATE TYPE public.source_type AS ENUM ('pubmed', 'pmc', 'guideline', 'uploaded');

CREATE TYPE public.study_type AS ENUM (
  'systematic_review',
  'meta_analysis',
  'randomized_controlled_trial',
  'cohort_study',
  'case_control_study',
  'case_series',
  'case_report',
  'clinical_guideline',
  'narrative_review',
  'other'
);

CREATE TYPE public.evidence_grade AS ENUM ('A', 'B', 'C', 'D');

CREATE TABLE public.documents (
  id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id           uuid REFERENCES public.organizations (id) ON DELETE CASCADE,  -- NULL = shared corpus
  source_type      public.source_type NOT NULL,
  external_id      text,           -- source-native id (PMID, PMC id, guideline ref)
  title            text NOT NULL,
  abstract         text,
  authors          jsonb NOT NULL DEFAULT '[]'::jsonb,
  journal          text,
  publication_date date,
  doi              text,
  pmid             text,
  url              text,
  study_type       public.study_type,
  evidence_grade   public.evidence_grade,
  storage_path     text,           -- Supabase Storage path for source PDFs
  -- Globally unique: the same content is never ingested twice, even across
  -- tenants — dedupe is deliberate; a private upload of a public paper
  -- resolves to the existing row at ingestion time.
  content_hash     text NOT NULL UNIQUE,
  ingested_at      timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE public.chunks (
  id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  document_id  uuid NOT NULL REFERENCES public.documents (id) ON DELETE CASCADE,
  -- Denormalized from the parent document so RLS and tenant filters never
  -- need a join. NEVER trusted from the caller: the trigger below overwrites
  -- it with the parent document's org_id on every insert/update.
  org_id       uuid REFERENCES public.organizations (id) ON DELETE CASCADE,
  chunk_index  int NOT NULL CHECK (chunk_index >= 0),
  content      text NOT NULL,
  content_tsv  tsvector GENERATED ALWAYS AS (to_tsvector('english', content)) STORED,
  token_count  int NOT NULL CHECK (token_count > 0),
  section      text,               -- e.g. abstract / methods / results / recommendations
  embedding    vector(1536),       -- Cohere embed-v4.0; NULL until the embed step runs
  metadata     jsonb NOT NULL DEFAULT '{}'::jsonb,
  UNIQUE (document_id, chunk_index)
);

-- Integrity: a chunk's org_id always equals its document's org_id. A mismatch
-- would let a private document leak through a "public" chunk (or vice versa),
-- so the value is derived, not accepted. Runs under the caller's RLS: an
-- authenticated user inserting a chunk for a document they cannot see gets
-- org_id NULL and then fails the chunks WITH CHECK policy.
CREATE OR REPLACE FUNCTION app.sync_chunk_org()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
  SELECT d.org_id INTO NEW.org_id FROM public.documents d WHERE d.id = NEW.document_id;
  RETURN NEW;
END;
$$;

CREATE TRIGGER chunks_sync_org
  BEFORE INSERT OR UPDATE OF document_id, org_id ON public.chunks
  FOR EACH ROW EXECUTE FUNCTION app.sync_chunk_org();

-- If a document ever moves org (service-role operation), carry the chunks along.
CREATE OR REPLACE FUNCTION app.sync_chunks_of_document()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
  UPDATE public.chunks SET org_id = NEW.org_id WHERE document_id = NEW.id;
  RETURN NEW;
END;
$$;

CREATE TRIGGER documents_sync_chunk_org
  AFTER UPDATE OF org_id ON public.documents
  FOR EACH ROW
  WHEN (OLD.org_id IS DISTINCT FROM NEW.org_id)
  EXECUTE FUNCTION app.sync_chunks_of_document();
