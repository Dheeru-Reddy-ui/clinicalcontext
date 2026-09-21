-- 019 — Phase 11 (voice agent).
--
-- 1. voice_turns: one row per spoken turn. The text path already records the
--    query/answer (queries + answers); this table records what is *specific*
--    to voice — the raw and corrected transcripts, every correction the
--    medical-term pass made, the LASA confirmation (requested / chosen / how),
--    the per-leg latency waterfall, speculation and barge-in accounting, and
--    the "cost of interactivity" (tokens and audio seconds thrown away by a
--    cancel). The dashboard and the public methodology page aggregate these;
--    nothing on either surface is hand-typed.
--
--    latency (jsonb, ms per leg; keys follow the spec's waterfall table):
--      endpoint_decision, transcript_final, guardrails, retrieval,
--      llm_first_token, first_sentence, tts_ttfb, client_playback,
--      total_first_audio (server-side up to first audio byte sent) and
--      client_first_audio (client-reported, includes buffering/playback).
--
-- 2. organizations.voice_tts_quality: the per-org Flash-vs-Multilingual
--    trade-off (11F.1). 'flash' is the latency setting; 'multilingual' buys
--    prosody at the cost of time-to-first-byte.

CREATE TYPE public.voice_turn_outcome AS ENUM (
  'answered',
  'abstained',
  'blocked_phi',
  'blocked_scope',
  'confirm_requested',
  'cancelled',
  'error'
);

CREATE TABLE public.voice_turns (
  id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id           uuid NOT NULL REFERENCES public.organizations (id) ON DELETE CASCADE,
  user_id          uuid REFERENCES auth.users (id) ON DELETE SET NULL,
  voice_session_id uuid NOT NULL,                    -- one WebSocket session (resumable)
  query_session_id uuid REFERENCES public.query_sessions (id) ON DELETE CASCADE,
  query_id         uuid REFERENCES public.queries (id) ON DELETE SET NULL,
  turn_index       int NOT NULL CHECK (turn_index >= 0),
  backend          text NOT NULL,                    -- offline | cloud
  stt_model        text NOT NULL,
  tts_model        text NOT NULL,
  transcript_raw   text NOT NULL DEFAULT '',
  transcript_final text NOT NULL DEFAULT '',
  corrections      jsonb NOT NULL DEFAULT '[]'::jsonb,
  confirmation     jsonb NOT NULL DEFAULT '{}'::jsonb,
  outcome          public.voice_turn_outcome NOT NULL,
  blocked_by       text,
  latency          jsonb NOT NULL DEFAULT '{}'::jsonb,
  speculation      jsonb NOT NULL DEFAULT '{}'::jsonb,
  barge_in         jsonb NOT NULL DEFAULT '{}'::jsonb,
  waste            jsonb NOT NULL DEFAULT '{}'::jsonb,
  mask_used        boolean NOT NULL DEFAULT false,
  created_at       timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX voice_turns_org_created_idx ON public.voice_turns (org_id, created_at DESC);
CREATE INDEX voice_turns_query_session_idx ON public.voice_turns (query_session_id, turn_index);
CREATE INDEX voice_turns_voice_session_idx ON public.voice_turns (voice_session_id, turn_index);

GRANT SELECT, INSERT, UPDATE ON public.voice_turns TO authenticated, service_role;

ALTER TABLE public.voice_turns ENABLE ROW LEVEL SECURITY;

CREATE POLICY voice_turns_select_own_org ON public.voice_turns
  FOR SELECT TO authenticated
  USING (org_id = app.user_org_id());

CREATE POLICY voice_turns_insert_own_org ON public.voice_turns
  FOR INSERT TO authenticated
  WITH CHECK (org_id = app.user_org_id());

CREATE POLICY voice_turns_update_own_org ON public.voice_turns
  FOR UPDATE TO authenticated
  USING (org_id = app.user_org_id())
  WITH CHECK (org_id = app.user_org_id());

ALTER TABLE public.organizations
  ADD COLUMN voice_tts_quality text NOT NULL DEFAULT 'flash'
    CHECK (voice_tts_quality IN ('flash', 'multilingual'));
