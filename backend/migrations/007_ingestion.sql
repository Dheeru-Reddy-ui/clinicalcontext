-- 007 — Ingestion support: document metadata/classification, dead letters.

-- Source-native metadata (MeSH terms, publication types, language, ...).
ALTER TABLE public.documents
  ADD COLUMN metadata jsonb NOT NULL DEFAULT '{}'::jsonb;

-- The classification decision, with its reasoning — never just the grade:
-- { study_type, evidence_grade, method: publication_types|llm|unclassified,
--   reasoning, signals: [...], model?, prompt_version?, classified_at }
ALTER TABLE public.documents
  ADD COLUMN classification jsonb NOT NULL DEFAULT '{}'::jsonb;

-- Dead-letter table for the ingestion pipeline: a document that repeatedly
-- fails any stage lands here with enough payload to replay it. Service-role
-- only (RLS enabled, no policies — same posture as schema_migrations).
CREATE TABLE public.ingestion_failures (
  id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  source          text NOT NULL,            -- pubmed | pmc | guideline | uploaded
  external_id     text,                     -- PMID / PMC id / file name when known
  stage           text NOT NULL,            -- fetch | parse | classify | persist | embed
  error           text NOT NULL,
  payload         jsonb NOT NULL DEFAULT '{}'::jsonb,
  attempts        int NOT NULL DEFAULT 1,
  first_failed_at timestamptz NOT NULL DEFAULT now(),
  last_failed_at  timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX ingestion_failures_source_idx
  ON public.ingestion_failures (source, external_id);

ALTER TABLE public.ingestion_failures ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON public.ingestion_failures FROM authenticated, anon;
