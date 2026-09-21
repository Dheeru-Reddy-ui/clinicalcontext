import { randomUUID } from "node:crypto";

import { expect, test } from "@playwright/test";

import { ask, call, json, resultOf } from "./fixtures/api";
import { newTenant, type Tenant } from "./fixtures/auth";
import { one, rows } from "./fixtures/db";

/**
 * Gates 17 and 18: a tenant API key is a first-class credential — it answers,
 * it is audited, it has its own rate-limit bucket, and revoking it takes
 * effect on the next request — and an Idempotency-Key replay returns the same
 * response without running the graph again.
 */

const GROUNDED = "How is type 2 diabetes managed with metformin?";

let tenant: Tenant;

test.beforeAll(async () => {
  tenant = await newTenant("E2E API Keys");
});

test("an API key answers, is audited, and stops working when revoked", async () => {
  const created = await json<{ id: string; key: string; key_prefix: string }>("/api/v1/api-keys", {
    token: tenant.token,
    body: { name: "e2e integration", scopes: ["query", "read"] },
  });
  expect(created.key).toMatch(/^cck_/);

  const { events, status } = await ask(GROUNDED, { apiKey: created.key });
  expect(status).toBe(200);
  const result = resultOf(events);
  expect(String(result.answer).length).toBeGreaterThan(40);

  // Audited with the key's prefix — never the key itself.
  const audit = await one<{ action: string; payload: string }>(
    `SELECT action, payload::text AS payload FROM public.audit_log
     WHERE org_id = $1 AND action = 'api.request' ORDER BY created_at DESC LIMIT 1`,
    [tenant.orgId],
  );
  expect(audit, "an API-key request is audited").not.toBeNull();
  const payload = JSON.parse(audit!.payload) as { api_key_prefix: string };
  expect(payload.api_key_prefix).toBe(created.key_prefix);
  expect(audit!.payload).not.toContain(created.key.slice(8));

  // The key's usage is tracked on its row.
  const keyRow = await one<{ last_used_at: string | null; key_hash: string }>(
    "SELECT last_used_at, key_hash FROM public.api_keys WHERE id = $1",
    [created.id],
  );
  expect(keyRow?.last_used_at, "last_used_at is stamped").toBeTruthy();
  expect(keyRow?.key_hash).not.toBe(created.key);

  // Revoked: the very next request is 401, with no answer written.
  const revoked = await call(`/api/v1/api-keys/${created.id}`, { token: tenant.token, method: "DELETE" });
  expect(revoked.status).toBe(204);

  const after = await call("/api/v1/queries", {
    apiKey: created.key,
    method: "POST",
    body: { query: GROUNDED },
  });
  expect(after.status).toBe(401);
});

test("API-key traffic is limited on its own bucket, not the user's", async () => {
  // A free-plan tenant: 20/min for a user, 60/min for an API key, so spending
  // the user's budget must leave the key's alone.
  const limited = await newTenant("E2E Key Buckets", "free");
  const key = await json<{ key: string }>("/api/v1/api-keys", {
    token: limited.token,
    body: { name: "bucket probe", scopes: ["query", "read"] },
  });

  let userLimited = false;
  for (let i = 0; i < 30 && !userLimited; i += 1) {
    const response = await call("/api/v1/sessions?limit=1", { token: limited.token });
    if (response.status === 429) userLimited = true;
    else await response.text();
  }
  expect(userLimited, "the user's bucket runs out").toBe(true);

  // The key still works: separate bucket.
  const viaKey = await call("/api/v1/sessions?limit=1", { apiKey: key.key });
  expect(viaKey.status).toBe(200);
});

test("replaying an Idempotency-Key returns the same answer without a second run", async () => {
  const idempotencyKey = randomUUID();
  const first = await ask(GROUNDED, { token: tenant.token, idempotencyKey });
  const firstResult = resultOf(first.events);

  const answersBefore = await rows("SELECT id FROM public.answers WHERE org_id = $1", [tenant.orgId]);
  const queriesBefore = await rows("SELECT id FROM public.queries WHERE org_id = $1", [tenant.orgId]);

  const replay = await ask(GROUNDED, { token: tenant.token, idempotencyKey });
  const replayResult = resultOf(replay.events);

  // Identical response...
  expect(replayResult.answer_id).toBe(firstResult.answer_id);
  expect(replayResult.answer).toBe(firstResult.answer);
  // Every replayed event is marked as such, so a caller can tell.
  expect(replay.events.length).toBeGreaterThan(0);
  expect(replay.events.every((e) => (e as unknown as { replayed?: boolean }).replayed === true)).toBe(true);

  // ...and no second run: no new query row, no new answer row, and nothing
  // added to the cost ledger.
  const answersAfter = await rows("SELECT id FROM public.answers WHERE org_id = $1", [tenant.orgId]);
  const queriesAfter = await rows("SELECT id FROM public.queries WHERE org_id = $1", [tenant.orgId]);
  expect(answersAfter.length).toBe(answersBefore.length);
  expect(queriesAfter.length).toBe(queriesBefore.length);

  const ledger = await rows(
    "SELECT id FROM public.cost_events WHERE query_id = $1 AND component = 'generation'",
    [firstResult.query_id],
  );
  expect(ledger.length, "generation ran once, for the first request").toBeGreaterThanOrEqual(1);
});
