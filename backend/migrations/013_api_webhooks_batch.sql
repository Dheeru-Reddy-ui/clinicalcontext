-- 013 — API power features: webhooks, batch jobs. (api_keys already exists in 004.)

-- Webhooks -------------------------------------------------------------------------
CREATE TABLE public.webhooks (
  id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id      uuid NOT NULL REFERENCES public.organizations (id) ON DELETE CASCADE,
  url         text NOT NULL CHECK (url ~ '^https://'),  -- HTTPS only
  secret      text NOT NULL,                            -- HMAC signing secret
  events      text[] NOT NULL DEFAULT '{}',             -- subscribed event names
  enabled     boolean NOT NULL DEFAULT true,
  created_by  uuid REFERENCES auth.users (id) ON DELETE SET NULL,
  created_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX webhooks_org_id_idx ON public.webhooks (org_id);

CREATE TABLE public.webhook_deliveries (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  webhook_id    uuid NOT NULL REFERENCES public.webhooks (id) ON DELETE CASCADE,
  org_id        uuid NOT NULL REFERENCES public.organizations (id) ON DELETE CASCADE,
  event         text NOT NULL,
  payload       jsonb NOT NULL DEFAULT '{}'::jsonb,
  status        text NOT NULL DEFAULT 'pending'
                CHECK (status IN ('pending', 'delivered', 'failed')),
  attempts      int NOT NULL DEFAULT 0,
  response_code int,
  last_error    text,
  next_attempt_at timestamptz,
  created_at    timestamptz NOT NULL DEFAULT now(),
  delivered_at  timestamptz
);
CREATE INDEX webhook_deliveries_org_idx ON public.webhook_deliveries (org_id, created_at);
CREATE INDEX webhook_deliveries_pending_idx
  ON public.webhook_deliveries (next_attempt_at)
  WHERE status = 'pending';

-- Batch jobs -----------------------------------------------------------------------
CREATE TABLE public.batch_jobs (
  id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id      uuid NOT NULL REFERENCES public.organizations (id) ON DELETE CASCADE,
  user_id     uuid REFERENCES auth.users (id) ON DELETE SET NULL,
  status      text NOT NULL DEFAULT 'queued'
              CHECK (status IN ('queued', 'running', 'completed', 'failed')),
  total       int NOT NULL,
  completed   int NOT NULL DEFAULT 0,
  failed      int NOT NULL DEFAULT 0,
  created_at  timestamptz NOT NULL DEFAULT now(),
  finished_at timestamptz
);
CREATE INDEX batch_jobs_org_idx ON public.batch_jobs (org_id, created_at);

CREATE TABLE public.batch_items (
  id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  batch_id   uuid NOT NULL REFERENCES public.batch_jobs (id) ON DELETE CASCADE,
  org_id     uuid NOT NULL REFERENCES public.organizations (id) ON DELETE CASCADE,
  position   int NOT NULL,
  query      text NOT NULL,
  status     text NOT NULL DEFAULT 'queued'
             CHECK (status IN ('queued', 'running', 'completed', 'failed', 'blocked')),
  query_id   uuid REFERENCES public.queries (id) ON DELETE SET NULL,
  answer_id  uuid REFERENCES public.answers (id) ON DELETE SET NULL,
  error      text,
  UNIQUE (batch_id, position)
);
CREATE INDEX batch_items_batch_idx ON public.batch_items (batch_id);
CREATE INDEX batch_items_org_idx ON public.batch_items (org_id);

-- RLS: org-scoped for authenticated; workers use the service role -------------------
GRANT SELECT, INSERT, UPDATE, DELETE ON public.webhooks, public.webhook_deliveries,
  public.batch_jobs, public.batch_items TO authenticated, service_role;

ALTER TABLE public.webhooks ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.webhook_deliveries ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.batch_jobs ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.batch_items ENABLE ROW LEVEL SECURITY;

CREATE POLICY webhooks_own_org ON public.webhooks FOR ALL TO authenticated
  USING (org_id = app.user_org_id()) WITH CHECK (org_id = app.user_org_id());
-- Deliveries are written by the delivery worker (service role); tenants read only.
CREATE POLICY webhook_deliveries_select ON public.webhook_deliveries FOR SELECT TO authenticated
  USING (org_id = app.user_org_id());
CREATE POLICY batch_jobs_own_org ON public.batch_jobs FOR ALL TO authenticated
  USING (org_id = app.user_org_id()) WITH CHECK (org_id = app.user_org_id());
CREATE POLICY batch_items_own_org ON public.batch_items FOR ALL TO authenticated
  USING (org_id = app.user_org_id()) WITH CHECK (org_id = app.user_org_id());
