-- 011 — Lexeme document-frequency stats for lexical query pruning.
--
-- A bare OR over every query term is slow for broad queries because
-- ts_rank_cd must score every matched chunk, and a common word ("patients",
-- "among", "study") matches a large fraction of the corpus. This table holds
-- each stemmed lexeme's document frequency so the lexical retriever can drop
-- low-information common terms and keep the distinctive ones — bounding both
-- latency and noise. Populated by `python -m app.ingestion.cli build-lexeme-stats`
-- (ts_stat is too slow to run inside a migration); empty is safe — the
-- retriever simply prunes nothing until it is built.

CREATE TABLE public.lexeme_stats (
  lexeme text PRIMARY KEY,
  df     int NOT NULL  -- documents (chunks) whose content_tsv contains the lexeme
);

-- Non-sensitive corpus statistics: readable by everyone, written by the
-- service role only (the build job). RLS kept on for the "every table" invariant.
GRANT SELECT ON public.lexeme_stats TO anon, authenticated;
GRANT SELECT, INSERT, UPDATE, DELETE ON public.lexeme_stats TO service_role;
ALTER TABLE public.lexeme_stats ENABLE ROW LEVEL SECURITY;

CREATE POLICY lexeme_stats_readable ON public.lexeme_stats
  FOR SELECT TO anon, authenticated
  USING (true);
