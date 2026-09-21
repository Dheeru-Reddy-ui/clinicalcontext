-- 017 — Let the request path queue its own webhook deliveries.
--
-- 013 gave webhook_deliveries a SELECT policy only, on the assumption that a
-- worker would create the rows. In practice the event is queued by the request
-- that produced it, inside that request's transaction, so that a subscriber is
-- never told about an answer whose transaction later rolled back. Under RLS
-- that INSERT runs as `authenticated` and needs a policy.
--
-- This was invisible until a tenant actually registered a webhook: with no
-- matching webhooks the INSERT ... SELECT writes zero rows, and a zero-row
-- INSERT never evaluates WITH CHECK.
--
-- Scope: a tenant may only queue deliveries for its own org (and only to its
-- own webhooks, via the FK). Status transitions stay with the delivery worker
-- on the service role — there is still no UPDATE or DELETE policy here, so a
-- tenant cannot mark its own deliveries delivered or erase a failure.

CREATE POLICY webhook_deliveries_insert_own_org ON public.webhook_deliveries
  FOR INSERT TO authenticated
  WITH CHECK (org_id = app.user_org_id());
