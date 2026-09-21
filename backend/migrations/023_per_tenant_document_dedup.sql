-- 023 — Phase 14: document de-duplication is per tenant, not global.
--
-- `documents.content_hash` was UNIQUE across the whole table, which made one
-- tenant's private corpus observable from another: uploading a PDF that
-- another organisation had already uploaded returned "this document already
-- exists in another tenant's corpus". No content, id or title crossed the
-- boundary, but *existence* did — enough for one hospital to test whether a
-- specific document had been uploaded by anyone else. Found by the Phase 14
-- E2E isolation suite.
--
-- It was also wrong as product behaviour: two hospitals that upload the same
-- guideline should each own their copy, with their own annotations and their
-- own retention.
--
-- So: the shared corpus (org_id IS NULL) still de-duplicates globally — the
-- ingestion pipeline's resumability depends on it — and each organisation
-- de-duplicates within itself.

ALTER TABLE public.documents DROP CONSTRAINT documents_content_hash_key;

CREATE UNIQUE INDEX documents_shared_content_hash_idx
  ON public.documents (content_hash)
  WHERE org_id IS NULL;

CREATE UNIQUE INDEX documents_org_content_hash_idx
  ON public.documents (org_id, content_hash)
  WHERE org_id IS NOT NULL;
