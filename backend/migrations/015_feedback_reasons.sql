-- 015 — Align feedback reasons with the product taxonomy.
--
-- The 004 enum was a first guess. The reasons clinicians actually need to give
-- are about the *answer's* failure mode: it was wrong, it wasn't supported by
-- the cited evidence, the evidence was outdated, it was incomplete, or — the
-- one that matters most for a system built to abstain — it should have
-- abstained instead of answering.
--
-- Recreated rather than ALTER TYPE ... ADD VALUE so the enum ends up with
-- exactly these five members (and because the runner applies each migration
-- inside a transaction). Existing rows are mapped, never dropped.

ALTER TABLE public.feedback ALTER COLUMN reason TYPE text USING reason::text;

DROP TYPE public.feedback_reason;

CREATE TYPE public.feedback_reason AS ENUM (
  'wrong', 'unsupported', 'outdated', 'incomplete', 'should_have_abstained'
);

ALTER TABLE public.feedback
  ALTER COLUMN reason TYPE public.feedback_reason
  USING (
    CASE reason
      WHEN 'incorrect'         THEN 'wrong'
      WHEN 'missing_evidence'  THEN 'unsupported'
      WHEN 'citation_mismatch' THEN 'unsupported'
      WHEN 'unclear'           THEN 'incomplete'
      WHEN 'outdated'          THEN 'outdated'
      ELSE NULL
    END
  )::public.feedback_reason;
