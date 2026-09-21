-- 009 — Lexical retrieval support: a clinical text-search config + a medical
-- synonym table for query-time abbreviation expansion.

-- A named configuration derived from english. It behaves like english today
-- (same parser + stemmer, so its lexemes match the english-built content_tsv),
-- but gives lexical search a stable, project-owned config name that custom
-- dictionaries can later attach to without touching call sites.
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_ts_config WHERE cfgname = 'clinical_english'
  ) THEN
    CREATE TEXT SEARCH CONFIGURATION public.clinical_english (COPY = pg_catalog.english);
  END IF;
END
$$;

-- Query-time synonym expansion. Portable (no server-side dictionary files,
-- which Supabase does not expose): the lexical searcher rewrites a query into
-- OR-ed variants using these rows. `term` is matched as a whole word in the
-- query and replaced by `expansion` to form an additional search variant.
CREATE TABLE public.search_synonyms (
  id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  term       text NOT NULL,       -- abbreviation / short form (lowercased)
  expansion  text NOT NULL,       -- full form
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (term, expansion)
);

CREATE INDEX search_synonyms_term_idx ON public.search_synonyms (lower(term));

-- Reference data, not tenant data: readable by everyone, writable by the
-- service role only.
ALTER TABLE public.search_synonyms ENABLE ROW LEVEL SECURITY;
GRANT SELECT ON public.search_synonyms TO authenticated, anon;

CREATE POLICY search_synonyms_readable ON public.search_synonyms
  FOR SELECT TO authenticated, anon
  USING (true);

INSERT INTO public.search_synonyms (term, expansion) VALUES
  ('afib', 'atrial fibrillation'),
  ('af', 'atrial fibrillation'),
  ('mi', 'myocardial infarction'),
  ('ami', 'acute myocardial infarction'),
  ('chf', 'congestive heart failure'),
  ('hf', 'heart failure'),
  ('hfref', 'heart failure with reduced ejection fraction'),
  ('cad', 'coronary artery disease'),
  ('acs', 'acute coronary syndrome'),
  ('htn', 'hypertension'),
  ('ckd', 'chronic kidney disease'),
  ('esrd', 'end stage renal disease'),
  ('aki', 'acute kidney injury'),
  ('dm', 'diabetes mellitus'),
  ('t2dm', 'type 2 diabetes mellitus'),
  ('t1dm', 'type 1 diabetes mellitus'),
  ('dka', 'diabetic ketoacidosis'),
  ('cap', 'community acquired pneumonia'),
  ('uti', 'urinary tract infection'),
  ('copd', 'chronic obstructive pulmonary disease'),
  ('pe', 'pulmonary embolism'),
  ('dvt', 'deep vein thrombosis'),
  ('vte', 'venous thromboembolism'),
  ('doac', 'direct oral anticoagulant'),
  ('noac', 'non vitamin k oral anticoagulant'),
  ('ssri', 'selective serotonin reuptake inhibitor'),
  ('snri', 'serotonin norepinephrine reuptake inhibitor'),
  ('mdd', 'major depressive disorder'),
  ('gad', 'generalized anxiety disorder'),
  ('ect', 'electroconvulsive therapy'),
  ('tsh', 'thyroid stimulating hormone'),
  ('rct', 'randomized controlled trial'),
  ('nsaid', 'nonsteroidal anti inflammatory drug'),
  ('bp', 'blood pressure'),
  ('egfr', 'estimated glomerular filtration rate');
