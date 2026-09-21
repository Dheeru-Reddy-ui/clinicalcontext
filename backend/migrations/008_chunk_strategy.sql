-- 008 — Multiple chunking strategies coexisting per document.
--
-- A `strategy` label lets the same document be chunked several ways at once
-- (fixed / recursive / semantic / structural) so they can be compared head to
-- head. The uniqueness key gains `strategy`, and `embed_text` lets a strategy
-- embed something other than the stored `content` (structural prepends the
-- document title + section header so a chunk is never context-free, while the
-- citation the user sees stays the clean passage).

ALTER TABLE public.chunks
  ADD COLUMN strategy text NOT NULL DEFAULT 'structural';

-- When set, embed_text is what gets embedded; when NULL, embed `content`.
ALTER TABLE public.chunks
  ADD COLUMN embed_text text;

-- Was UNIQUE (document_id, chunk_index); now scoped per strategy.
ALTER TABLE public.chunks
  DROP CONSTRAINT chunks_document_id_chunk_index_key;

ALTER TABLE public.chunks
  ADD CONSTRAINT chunks_document_strategy_index_key
  UNIQUE (document_id, strategy, chunk_index);

CREATE INDEX chunks_strategy_idx ON public.chunks (strategy);

-- The existing corpus was chunked by the section-aware Phase 4 chunker, which
-- is the structural strategy's boundary logic — the DEFAULT above already
-- labels those rows 'structural'; no backfill needed.
