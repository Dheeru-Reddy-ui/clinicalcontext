-- 027: Learn's tools — the tutor's practice record, and conversations about
-- one paper.
--
-- 1. Two conversation kinds join query_sessions: 'tutor' (lessons with the
--    AI tutor, taught and questioned turn by turn) and 'paper' (questions
--    asked of one uploaded paper). A paper conversation names its document.
--    When the paper is deleted the link is cleared rather than cascaded:
--    the API deletes the paper's conversations itself, under the same rule
--    as every other deletion (settings.delete_conversations) — an answer
--    saved to a binder, shared, or versioned is never taken with it.
--
-- 2. public.tutor_attempts: one row per quiz or case question a person
--    answers — the question, the answer they chose, the right one and why —
--    so Learn can show their accuracy by topic and the questions worth
--    another look. Personal: each person reads, writes and clears only
--    their own rows. The clinical note summarizer stores nothing, so it
--    needs no table.

-- 1. Conversation kinds, and the paper a conversation is about -----------------

ALTER TABLE public.query_sessions DROP CONSTRAINT IF EXISTS query_sessions_kind_check;
ALTER TABLE public.query_sessions ADD CONSTRAINT query_sessions_kind_check
  CHECK (kind IN ('ask', 'chat', 'learn', 'treatment', 'voice', 'tutor', 'paper'));

ALTER TABLE public.query_sessions
  ADD COLUMN IF NOT EXISTS document_id uuid
    REFERENCES public.documents (id) ON DELETE SET NULL;

-- A paper's conversations, newest first, per person.
CREATE INDEX IF NOT EXISTS query_sessions_user_document_idx
  ON public.query_sessions (user_id, document_id, updated_at DESC)
  WHERE document_id IS NOT NULL;

-- 2. The tutor's practice record ------------------------------------------------

CREATE TABLE public.tutor_attempts (
  id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id     uuid NOT NULL REFERENCES auth.users (id) ON DELETE CASCADE,
  org_id      uuid NOT NULL REFERENCES public.organizations (id) ON DELETE CASCADE,
  -- A quiz question, or one step of a clinical case.
  mode        text NOT NULL CHECK (mode IN ('quiz', 'case')),
  level       text NOT NULL CHECK (level IN ('mbbs', 'pg')),
  specialty   text CHECK (specialty IS NULL OR specialty ~ '^[a-z0-9-]{2,64}$'),
  topic       text NOT NULL CHECK (char_length(topic) BETWEEN 1 AND 200),
  question    text NOT NULL CHECK (char_length(question) BETWEEN 1 AND 3000),
  chosen      text NOT NULL CHECK (char_length(chosen) BETWEEN 1 AND 600),
  answer      text NOT NULL CHECK (char_length(answer) BETWEEN 1 AND 600),
  explanation text NOT NULL DEFAULT '' CHECK (char_length(explanation) <= 4000),
  correct     boolean NOT NULL,
  created_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX tutor_attempts_user_created_idx
  ON public.tutor_attempts (user_id, created_at DESC);
CREATE INDEX tutor_attempts_org_idx ON public.tutor_attempts (org_id);

GRANT SELECT, INSERT, DELETE ON public.tutor_attempts TO authenticated, service_role;

ALTER TABLE public.tutor_attempts ENABLE ROW LEVEL SECURITY;

CREATE POLICY tutor_attempts_own_rows ON public.tutor_attempts
  FOR ALL TO authenticated
  USING (user_id = auth.uid() AND org_id = app.user_org_id())
  WITH CHECK (user_id = auth.uid() AND org_id = app.user_org_id());
