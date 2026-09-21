-- 018 — Phase 10 (frontend) support.
--
-- 1. answers.reasoning: the structured extras of an AnswerResult that the UI
--    needs *after* the live SSE stream is gone — on history, permalink, and
--    binder pages. Until now only ``has_contradiction`` (a bool) survived
--    persistence; the positions, the axis, the explanation, the sub-questions
--    searched, the retrieval grade, the rewrite count, and the per-node prompt
--    versions were all dropped. The contradiction view, the abstention view,
--    the evidence-timeline stance colouring, and the latency-honesty footer
--    are impossible to render honestly on reload without them.
--
--    Shape (all keys optional, written by app.repositories.answers):
--      contradiction    {detected, axis, explanation, positions:[{stance,markers,year}]}
--      sub_questions    [text]
--      retrieval_grade  sufficient | insufficient | irrelevant
--      rewrite_count    int
--      generation_mode  llm | extractive
--      escalation_banner text | null
--      prompt_versions  {node: "name.vN"}
--      query_type, is_multi_hop
--
-- 2. organizations.public_sharing_enabled: the tenant-wide kill switch for
--    read-only public answer permalinks (/a/{slug}). Off → every existing
--    link for the org resolves to a clean 404, without touching the rows.

ALTER TABLE public.answers
  ADD COLUMN reasoning jsonb NOT NULL DEFAULT '{}'::jsonb;

ALTER TABLE public.organizations
  ADD COLUMN public_sharing_enabled boolean NOT NULL DEFAULT true;
