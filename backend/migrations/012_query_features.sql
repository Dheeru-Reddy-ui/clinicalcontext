-- 012 — Query/answer feature columns: comparison mode, PICO, contextualization,
-- semantic-cache accounting.

ALTER TABLE public.queries
  ADD COLUMN mode text NOT NULL DEFAULT 'standard'
    CHECK (mode IN ('standard', 'comparison')),
  -- Structured PICO (Population/Intervention/Comparison/Outcome), when provided.
  ADD COLUMN pico jsonb,
  -- For multi-turn follow-ups: the self-contained query after contextualization.
  ADD COLUMN contextualized_query text;

ALTER TABLE public.answers
  -- A cache hit records a real answers row flagged cached (cost_usd = 0), with
  -- the provider cost it saved, so analytics can show the saving.
  ADD COLUMN cached boolean NOT NULL DEFAULT false,
  ADD COLUMN cache_saved_usd numeric(10, 6),
  -- Comparison-mode structured table (entities × outcomes, per-cell citations).
  ADD COLUMN comparison_table jsonb;
