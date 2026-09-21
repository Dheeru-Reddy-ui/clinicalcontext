-- 005 — Indexes.
--
-- Retrieval indexes first (the product), then btrees on every FK column that
-- lacks one plus the hot-path timestamps. UNIQUE constraints already index
-- their leading columns, so e.g. chunks.document_id and feedback.answer_id
-- need no extra btree.

-- Dense retrieval: HNSW over cosine distance (Cohere embeddings are unit-norm;
-- cosine matches how they are compared downstream).
CREATE INDEX chunks_embedding_hnsw
  ON public.chunks
  USING hnsw (embedding vector_cosine_ops)
  WITH (m = 16, ef_construction = 64);

-- Lexical retrieval: generated tsvector column.
CREATE INDEX chunks_content_tsv_gin
  ON public.chunks
  USING gin (content_tsv);

-- Library title search (typo-tolerant).
CREATE INDEX documents_title_trgm
  ON public.documents
  USING gin (title gin_trgm_ops);

-- Tenancy ---------------------------------------------------------------------
CREATE INDEX profiles_org_id_idx        ON public.profiles (org_id);
CREATE INDEX org_invites_org_id_idx     ON public.org_invites (org_id);

-- Corpus ----------------------------------------------------------------------
CREATE INDEX documents_org_id_idx       ON public.documents (org_id);
CREATE INDEX chunks_org_id_idx          ON public.chunks (org_id);
-- Ingestion dedupe/lookups by source identifiers.
CREATE INDEX documents_pmid_idx         ON public.documents (pmid) WHERE pmid IS NOT NULL;
-- Recency-aware ranking reads publication_date constantly.
CREATE INDEX documents_publication_date_idx ON public.documents (publication_date);

-- Query domain ------------------------------------------------------------------
CREATE INDEX query_sessions_org_id_idx  ON public.query_sessions (org_id);
CREATE INDEX query_sessions_user_id_idx ON public.query_sessions (user_id);
CREATE INDEX queries_session_id_idx     ON public.queries (session_id);
CREATE INDEX queries_org_id_idx         ON public.queries (org_id);
CREATE INDEX queries_user_id_idx        ON public.queries (user_id);
CREATE INDEX queries_created_at_idx     ON public.queries (created_at);
CREATE INDEX answers_query_id_idx       ON public.answers (query_id);
CREATE INDEX answers_org_id_idx         ON public.answers (org_id);
CREATE INDEX retrieval_traces_query_id_idx ON public.retrieval_traces (query_id);
CREATE INDEX retrieval_traces_org_id_idx   ON public.retrieval_traces (org_id);
CREATE INDEX feedback_org_id_idx        ON public.feedback (org_id);
CREATE INDEX feedback_user_id_idx       ON public.feedback (user_id);

-- Audit reads are always "this org, this time window".
CREATE INDEX audit_log_org_created_idx  ON public.audit_log (org_id, created_at);

CREATE INDEX api_keys_org_id_idx        ON public.api_keys (org_id);
CREATE INDEX api_keys_created_by_idx    ON public.api_keys (created_by);
CREATE INDEX followed_answers_org_id_idx  ON public.followed_answers (org_id);
CREATE INDEX followed_answers_user_id_idx ON public.followed_answers (user_id);
CREATE INDEX answer_versions_org_id_idx   ON public.answer_versions (org_id);
CREATE INDEX binders_org_id_idx         ON public.binders (org_id);
CREATE INDEX binders_created_by_idx     ON public.binders (created_by);
CREATE INDEX binder_items_binder_id_idx ON public.binder_items (binder_id);
CREATE INDEX binder_items_org_id_idx    ON public.binder_items (org_id);
CREATE INDEX binder_items_answer_id_idx ON public.binder_items (answer_id);
CREATE INDEX binder_items_chunk_id_idx  ON public.binder_items (chunk_id);
CREATE INDEX binder_items_added_by_idx  ON public.binder_items (added_by);
CREATE INDEX annotations_org_id_idx     ON public.annotations (org_id);
CREATE INDEX annotations_user_id_idx    ON public.annotations (user_id);
CREATE INDEX annotations_binder_item_id_idx ON public.annotations (binder_item_id);
CREATE INDEX annotations_chunk_id_idx   ON public.annotations (chunk_id);
CREATE INDEX annotations_parent_id_idx  ON public.annotations (parent_id);
CREATE INDEX share_links_org_id_idx     ON public.share_links (org_id);
CREATE INDEX share_links_answer_id_idx  ON public.share_links (answer_id);
CREATE INDEX share_links_created_by_idx ON public.share_links (created_by);
CREATE INDEX notifications_org_id_idx   ON public.notifications (org_id);
CREATE INDEX notifications_user_id_idx  ON public.notifications (user_id);
-- The notification badge query: unread for one user, newest first.
CREATE INDEX notifications_unread_idx
  ON public.notifications (user_id, created_at DESC)
  WHERE read_at IS NULL;
