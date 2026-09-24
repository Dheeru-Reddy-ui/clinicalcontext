-- 025: conversations, and drug labels in the shared corpus.
--
-- The chat assistant keeps its conversations in the same session/query/answer
-- record as Ask (one audit trail, one RLS boundary), so a session now says
-- which surface it belongs to, and a question says who it was written for —
-- a patient reads plain language, a clinician the technical answer, a
-- student the teaching version. Existing rows are Ask sessions.
--
-- Drug labels (openFDA's structured product labels) join the shared corpus
-- as their own source and study type: a label is the regulator's word on
-- dosing and contraindications, not evidence of efficacy, so it is neither
-- graded nor filed as a guideline.
--
-- ADD VALUE inside the runner's transaction is fine on Postgres 12+; nothing
-- in this file uses the new values.

ALTER TYPE public.source_type ADD VALUE IF NOT EXISTS 'drug_label';
ALTER TYPE public.study_type ADD VALUE IF NOT EXISTS 'drug_label';

ALTER TABLE public.query_sessions
  ADD COLUMN IF NOT EXISTS kind text NOT NULL DEFAULT 'ask'
    CHECK (kind IN ('ask', 'chat', 'learn', 'treatment', 'voice')),
  ADD COLUMN IF NOT EXISTS updated_at timestamptz NOT NULL DEFAULT now();

ALTER TABLE public.queries
  ADD COLUMN IF NOT EXISTS audience text
    CHECK (audience IS NULL OR audience IN ('patient', 'clinician', 'student'));

-- The conversation list: newest activity first, per person.
CREATE INDEX IF NOT EXISTS query_sessions_user_kind_updated_idx
  ON public.query_sessions (user_id, kind, updated_at DESC);
