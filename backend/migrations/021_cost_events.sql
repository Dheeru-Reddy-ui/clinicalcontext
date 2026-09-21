-- 021 — Phase 13: the cost ledger.
--
-- answers.cost_usd is the generation cost of one answer; it says nothing
-- about embedding, reranking, speech-to-text or text-to-speech, and nothing
-- about a voice turn that never produced an answer. This ledger records every
-- billable provider call as it happens — component, provider, model, the
-- units the provider bills (tokens, searches, audio seconds, characters) and
-- the cost at the published rate — so the dashboard can show real spend per
-- tenant broken out by component, next to what the semantic cache avoided.
--
-- Offline providers (the local embedder, BM25, faster-whisper, the OS voice)
-- record their real units at a truthful $0; the same rows priced at cloud
-- list rates give the "what this would cost" projection, labelled as such.

CREATE TYPE public.cost_component AS ENUM ('embedding', 'rerank', 'generation', 'stt', 'tts');

CREATE TABLE public.cost_events (
  id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id           uuid NOT NULL REFERENCES public.organizations (id) ON DELETE CASCADE,
  query_id         uuid REFERENCES public.queries (id) ON DELETE SET NULL,
  voice_session_id uuid,
  component        public.cost_component NOT NULL,
  provider         text NOT NULL,           -- cohere | anthropic | deepgram | elevenlabs | local
  model            text NOT NULL,
  units            numeric(14, 3) NOT NULL, -- what the provider bills, in `unit`
  unit             text NOT NULL,           -- tokens | searches | seconds | characters
  cost_usd         numeric(12, 6) NOT NULL DEFAULT 0,
  cached           boolean NOT NULL DEFAULT false,  -- served from a cache: units avoided
  created_at       timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX cost_events_org_created_idx ON public.cost_events (org_id, created_at DESC);
CREATE INDEX cost_events_query_idx ON public.cost_events (query_id) WHERE query_id IS NOT NULL;

GRANT SELECT, INSERT ON public.cost_events TO authenticated, service_role;

ALTER TABLE public.cost_events ENABLE ROW LEVEL SECURITY;

CREATE POLICY cost_events_select_own_org ON public.cost_events
  FOR SELECT TO authenticated
  USING (org_id = app.user_org_id());

CREATE POLICY cost_events_insert_own_org ON public.cost_events
  FOR INSERT TO authenticated
  WITH CHECK (org_id = app.user_org_id());
