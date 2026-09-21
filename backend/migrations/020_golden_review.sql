-- 020 — Phase 12 (the evaluation harness): the feedback → golden-set loop.
--
-- A thumbs-down whose reason is 'wrong' or 'unsupported' is a claim that the
-- system failed on a real question. Each such feedback row is queued here
-- for review; a reviewer promotes it into the golden set with
-- `python -m evals.golden.promote` (which records the outcome on the row),
-- or rejects it. The eval set grows from real usage, one reviewed case at a
-- time, and the queue is the audit trail of what was and was not promoted.
--
-- Rows are created by a trigger on feedback (SECURITY DEFINER: the reviewer
-- queue is service-owned; the clinician who clicked the thumb never sees or
-- writes it directly). Owners can read their own org's queue on the
-- dashboard; only the service role changes status.

CREATE TYPE public.review_status AS ENUM ('pending', 'promoted', 'rejected');

CREATE TABLE public.golden_reviews (
  id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  feedback_id uuid NOT NULL UNIQUE REFERENCES public.feedback (id) ON DELETE CASCADE,
  answer_id   uuid NOT NULL REFERENCES public.answers (id) ON DELETE CASCADE,
  org_id      uuid NOT NULL REFERENCES public.organizations (id) ON DELETE CASCADE,
  status      public.review_status NOT NULL DEFAULT 'pending',
  golden_id   text,            -- the golden item id, once promoted
  reviewer    text,
  note        text,
  created_at  timestamptz NOT NULL DEFAULT now(),
  reviewed_at timestamptz
);
CREATE INDEX golden_reviews_status_idx ON public.golden_reviews (status, created_at);
CREATE INDEX golden_reviews_org_idx ON public.golden_reviews (org_id, created_at DESC);

CREATE OR REPLACE FUNCTION app.enqueue_golden_review()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, app
AS $$
BEGIN
  IF NEW.rating = 'down' AND NEW.reason IN ('wrong', 'unsupported') THEN
    INSERT INTO public.golden_reviews (feedback_id, answer_id, org_id)
    VALUES (NEW.id, NEW.answer_id, NEW.org_id)
    ON CONFLICT (feedback_id) DO NOTHING;
  END IF;
  RETURN NEW;
END;
$$;

CREATE TRIGGER feedback_enqueue_golden_review
  AFTER INSERT OR UPDATE OF rating, reason ON public.feedback
  FOR EACH ROW EXECUTE FUNCTION app.enqueue_golden_review();

GRANT SELECT ON public.golden_reviews TO authenticated;
GRANT SELECT, INSERT, UPDATE, DELETE ON public.golden_reviews TO service_role;

ALTER TABLE public.golden_reviews ENABLE ROW LEVEL SECURITY;

CREATE POLICY golden_reviews_select_own_org ON public.golden_reviews
  FOR SELECT TO authenticated
  USING (org_id = app.user_org_id());
