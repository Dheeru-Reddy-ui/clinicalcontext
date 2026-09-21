-- 009 — Move embeddings into their own narrow table.
--
-- WHY: a vector(1536) column is ~6KB per row — roughly 75% of a chunk's
-- width. With it inline, any lexical scan that ranks matched chunks drags
-- hundreds of MB of embeddings through the heap it never needs, and the
-- planner (correctly) avoids the GIN index for a broad query, so full-text
-- ranking over the seed corpus ran ~400-1400 ms. Splitting embeddings into a
-- dedicated table keeps `chunks` narrow (fast FTS + library/citation reads)
-- and gives dense search its own HNSW index. org_id + strategy are
-- denormalized so dense search filters and RLS apply without a join back.

CREATE TABLE public.chunk_embeddings (
  chunk_id  uuid PRIMARY KEY REFERENCES public.chunks (id) ON DELETE CASCADE,
  org_id    uuid REFERENCES public.organizations (id) ON DELETE CASCADE,  -- NULL = shared corpus
  strategy  text NOT NULL,
  embedding vector(1536) NOT NULL
);

-- Move the existing vectors in place (no re-embedding needed).
INSERT INTO public.chunk_embeddings (chunk_id, org_id, strategy, embedding)
SELECT id, org_id, strategy, embedding
FROM public.chunks
WHERE embedding IS NOT NULL;

-- Drop the column (this also drops chunks_embedding_hnsw from migration 005).
ALTER TABLE public.chunks DROP COLUMN embedding;

-- Dense retrieval index lives here now.
CREATE INDEX chunk_embeddings_hnsw
  ON public.chunk_embeddings
  USING hnsw (embedding vector_cosine_ops)
  WITH (m = 16, ef_construction = 64);
CREATE INDEX chunk_embeddings_strategy_idx ON public.chunk_embeddings (strategy);
CREATE INDEX chunk_embeddings_org_id_idx ON public.chunk_embeddings (org_id);

-- RLS mirrors chunks exactly: read own-org + shared corpus; writes are
-- service-role only (the embedding backfill). Keeps the "every public table
-- has RLS" invariant intact.
GRANT SELECT, INSERT, UPDATE, DELETE ON public.chunk_embeddings TO authenticated, service_role;
ALTER TABLE public.chunk_embeddings ENABLE ROW LEVEL SECURITY;

CREATE POLICY chunk_embeddings_select_own_org ON public.chunk_embeddings
  FOR SELECT TO authenticated
  USING (org_id = app.user_org_id());

CREATE POLICY chunk_embeddings_select_shared ON public.chunk_embeddings
  FOR SELECT TO authenticated
  USING (org_id IS NULL);

CREATE POLICY chunk_embeddings_insert_own_org ON public.chunk_embeddings
  FOR INSERT TO authenticated
  WITH CHECK (org_id = app.user_org_id());

CREATE POLICY chunk_embeddings_update_own_org ON public.chunk_embeddings
  FOR UPDATE TO authenticated
  USING (org_id = app.user_org_id())
  WITH CHECK (org_id = app.user_org_id());

CREATE POLICY chunk_embeddings_delete_own_org ON public.chunk_embeddings
  FOR DELETE TO authenticated
  USING (org_id = app.user_org_id());
