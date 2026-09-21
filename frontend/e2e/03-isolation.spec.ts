import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { expect, test } from "@playwright/test";

import { ask, call, json, resultOf } from "./fixtures/api";
import { newTenant, type Tenant } from "./fixtures/auth";
import { asTenant, one, rows, scalar } from "./fixtures/db";
import { env } from "./fixtures/env";

/**
 * Gate 9 and the adversarial half of 14.3.
 *
 * One org uploads a private protocol and can answer from it. A second org
 * must not be able to reach that chunk — not through the API, not by quoting
 * its id, not by forging claims, and not at the database with a tampered
 * context. Every assertion here is at the API or the row, because tenant
 * isolation that is only enforced in the UI is not enforced.
 */

const PDF = resolve(process.cwd(), "e2e/fixtures/riverside-protocol.pdf");
const PRIVATE_QUESTION = "What does the RIVERSIDE-BRIDGING-PROTOCOL say about perioperative bridging?";

let orgA: Tenant;
let orgB: Tenant;
let documentId: string;
let chunkId: string;

test.beforeAll(async () => {
  orgA = await newTenant("E2E Org A");
  orgB = await newTenant("E2E Org B");
  // A previous run's copy would de-duplicate against this one (per tenant
  // since migration 023), so the fixture starts from nothing.
  await rows("DELETE FROM public.documents WHERE title = $1", [
    "Riverside Perioperative Bridging Protocol",
  ]);

  // Upload as org A through the real endpoint, with a real PDF.
  const form = new FormData();
  form.append("file", new Blob([readFileSync(PDF)], { type: "application/pdf" }), "riverside-protocol.pdf");
  form.append("title", "Riverside Perioperative Bridging Protocol");
  const response = await fetch(`${env.apiUrl}/api/v1/documents/upload`, {
    method: "POST",
    headers: { Authorization: `Bearer ${orgA.token}` },
    body: form,
  });
  const body = await response.text();
  expect(response.status, body).toBe(201);
  const uploaded = JSON.parse(body) as { document_id: string; chunk_count: number; embedded_count: number };
  documentId = uploaded.document_id;
  expect(uploaded.chunk_count).toBeGreaterThan(0);
  expect(uploaded.embedded_count, "the upload is searchable immediately on the offline embedder").toBe(
    uploaded.chunk_count,
  );

  const chunk = await one<{ id: string }>(
    "SELECT id FROM public.chunks WHERE document_id = $1 ORDER BY chunk_index LIMIT 1",
    [documentId],
  );
  chunkId = chunk!.id;
});

test("org A can answer from its own private upload", async () => {
  const { events } = await ask(PRIVATE_QUESTION, { token: orgA.token });
  const result = resultOf(events);
  const citations = result.citations as { document_id: string }[];
  expect(citations.some((c) => c.document_id === documentId), "the private document was cited").toBe(true);

  // The document is org A's, in the row.
  const owner = await scalar<string>("SELECT org_id FROM public.documents WHERE id = $1", [documentId]);
  expect(owner).toBe(orgA.orgId);
});

test("org B cannot reach the document, its chunks, or its content", async () => {
  // The API refuses it by id — not found, not "forbidden", so the id is not
  // even confirmed to exist.
  const doc = await call(`/api/v1/documents/${documentId}`, { token: orgB.token });
  expect(doc.status).toBe(404);
  const chunks = await call(`/api/v1/documents/${documentId}/chunks`, { token: orgB.token });
  expect(chunks.status).toBe(404);

  // It is not in org B's library listing either.
  const listing = await json<{ documents: { id: string }[] }>("/api/v1/documents?limit=100", {
    token: orgB.token,
  });
  expect(listing.documents.map((d) => d.id)).not.toContain(documentId);

  // And asking the same question as org B cannot surface the passage.
  const { events } = await ask(PRIVATE_QUESTION, { token: orgB.token });
  const result = resultOf(events);
  const citations = result.citations as { document_id: string }[];
  expect(citations.every((c) => c.document_id !== documentId)).toBe(true);
  expect(String(result.answer)).not.toContain("RIVERSIDE-BRIDGING-PROTOCOL");
});

test("RLS refuses org B at the database, with org B's own claims", async () => {
  // The exact context a Supabase request runs under, for org B.
  const visibleDocs = await asTenant<{ id: string }>(
    orgB.orgId,
    orgB.id,
    "SELECT id FROM public.documents WHERE id = $1",
    [documentId],
  );
  expect(visibleDocs, "org B cannot select org A's document row").toHaveLength(0);

  const visibleChunks = await asTenant<{ id: string }>(
    orgB.orgId,
    orgB.id,
    "SELECT id FROM public.chunks WHERE id = $1",
    [chunkId],
  );
  expect(visibleChunks, "org B cannot select the chunk either").toHaveLength(0);

  // Nor can it take the document by updating its owner.
  await expect(
    asTenant(orgB.orgId, orgB.id, "UPDATE public.documents SET org_id = $2 WHERE id = $1", [
      documentId,
      orgB.orgId,
    ]),
  ).resolves.toHaveLength(0);
  const stillOrgA = await scalar<string>("SELECT org_id FROM public.documents WHERE id = $1", [documentId]);
  expect(stillOrgA).toBe(orgA.orgId);

  // Or deleting it.
  await asTenant(orgB.orgId, orgB.id, "DELETE FROM public.documents WHERE id = $1", [documentId]);
  expect(await scalar<string>("SELECT count(*)::int FROM public.documents WHERE id = $1", [documentId])).toBe(1);
});

test("forging a tenant in the request body does not change the tenant", async () => {
  // The org is taken from the verified token, never from the payload.
  const response = await call("/api/v1/queries", {
    token: orgB.token,
    method: "POST",
    body: { query: PRIVATE_QUESTION, org_id: orgA.orgId, tenant_id: orgA.orgId },
  });
  expect(response.status).toBe(200);
  const events = (await response.text())
    .split(/\r?\n/)
    .filter((l) => l.startsWith("data: "))
    .map((l) => JSON.parse(l.slice(6)) as { stage: string; data: Record<string, unknown> });
  const accepted = events.find((e) => e.stage === "accepted")!;
  const queryId = String(accepted.data.query_id);
  const owner = await scalar<string>("SELECT org_id FROM public.queries WHERE id = $1", [queryId]);
  expect(owner, "the query belongs to the token's org, not the body's").toBe(orgB.orgId);
});

test("a tampered token is rejected outright", async () => {
  const [header, payload, signature] = orgB.token.split(".");
  const decoded = JSON.parse(Buffer.from(payload, "base64url").toString("utf8")) as Record<string, unknown>;
  decoded.app_metadata = { org_id: orgA.orgId };
  const forgedPayload = Buffer.from(JSON.stringify(decoded)).toString("base64url");

  // Same signature, different claims — the signature no longer verifies.
  const tampered = `${header}.${forgedPayload}.${signature}`;
  const response = await call("/api/v1/orgs/me", { token: tampered });
  expect(response.status).toBe(401);

  // And an unsigned "alg: none" token is not accepted either.
  const noneHeader = Buffer.from(JSON.stringify({ alg: "none", typ: "JWT" })).toString("base64url");
  const unsigned = `${noneHeader}.${forgedPayload}.`;
  expect((await call("/api/v1/orgs/me", { token: unsigned })).status).toBe(401);

  // Nothing was leaked: org A's document is still invisible to org B's real token.
  expect((await call(`/api/v1/documents/${documentId}`, { token: orgB.token })).status).toBe(404);
});

test("org B cannot read org A's answers, queries, or feedback by id", async () => {
  const { events } = await ask("How is type 2 diabetes managed with metformin?", { token: orgA.token });
  const answerId = String(resultOf(events).answer_id);

  expect((await call(`/api/v1/answers/${answerId}/versions`, { token: orgB.token })).status).toBe(404);
  const follow = await call(`/api/v1/answers/${answerId}/follow`, { token: orgB.token, method: "POST" });
  expect([403, 404]).toContain(follow.status);

  const feedback = await call("/api/v1/feedback", {
    token: orgB.token,
    body: { answer_id: answerId, rating: "down", reason: "wrong" },
  });
  expect([403, 404]).toContain(feedback.status);

  const written = await rows("SELECT id FROM public.feedback WHERE answer_id = $1 AND org_id = $2", [
    answerId,
    orgB.orgId,
  ]);
  expect(written, "no cross-tenant feedback row was written").toHaveLength(0);
});
