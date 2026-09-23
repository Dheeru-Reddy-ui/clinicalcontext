-- 024: rewrite stored text that carried patient identifiers.
--
-- The PHI gate blocked these questions, but the query row, the new session's
-- title and the voice transcript had already been written — word for word —
-- because they are written before the gate runs. The autocomplete and the
-- dashboard's top questions read from queries.raw_query, so a blocked name
-- and date of birth could be shown back to other members of the
-- organisation. The application now stores a placeholder instead
-- (app/guardrails/phi.py, withhold_phi); this rewrites what was already
-- written to the same placeholder.
--
-- Scoped by the gate's own recorded verdict, so only text the gate actually
-- blocked for PHI is touched. The audit log is not: it records entity types
-- and counts only, never values, and is append-only by design.
-- Idempotent: rows already withheld are left alone.

-- Session titles first: they are matched against the query text they were
-- made from, which the next statement replaces.
UPDATE public.query_sessions s
SET title = '[withheld: contained patient identifiers]'
WHERE s.title IS DISTINCT FROM '[withheld: contained patient identifiers]'
  AND EXISTS (
    SELECT 1
    FROM public.queries q
    WHERE q.session_id = s.id
      AND q.guardrail_verdict ->> 'blocked_by' = 'phi'
      AND left(q.raw_query, 120) = s.title
  );

UPDATE public.queries
SET raw_query = '[withheld: contained patient identifiers]',
    normalized_query = NULL,
    contextualized_query = NULL
WHERE guardrail_verdict ->> 'blocked_by' = 'phi'
  AND raw_query IS DISTINCT FROM '[withheld: contained patient identifiers]';

UPDATE public.voice_turns
SET transcript_raw = '[withheld: contained patient identifiers]',
    transcript_final = '[withheld: contained patient identifiers]'
WHERE blocked_by = 'phi'
  AND (
    transcript_raw IS DISTINCT FROM '[withheld: contained patient identifiers]'
    OR transcript_final IS DISTINCT FROM '[withheld: contained patient identifiers]'
  );
