-- 016 — Record what the SOURCE has that we don't.
--
-- Freshness is only meaningful as a comparison: "our newest paper is from
-- March" says nothing on its own. The weekly re-query asks PubMed for each
-- seed domain's top results and counts how many of those PMIDs are missing
-- locally, so /v1/corpus/freshness can answer "is this domain stale?" with a
-- real number instead of a vibe.

ALTER TABLE public.corpus_freshness
  ADD COLUMN source_new_count int NOT NULL DEFAULT 0,
  ADD COLUMN source_checked_count int NOT NULL DEFAULT 0,
  ADD COLUMN source_newest_publication date;
