import { execFileSync } from "node:child_process";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { expect, test } from "@playwright/test";

import { ask, call, json, resultOf } from "./fixtures/api";
import { newTenant, signIn, type Tenant } from "./fixtures/auth";
import { one, rows } from "./fixtures/db";
import { env } from "./fixtures/env";

/**
 * Gate 16: follow an answer, ingest evidence that changes it, run the nightly
 * job, and see the diff and the notification.
 *
 * The question is one the corpus cannot answer, so the first answer abstains.
 * Uploading a document that does answer it is unambiguously new evidence, and
 * the re-run has to notice.
 */

const PDF = resolve(process.cwd(), "e2e/fixtures/riverside-protocol.pdf");
const QUESTION = "What does the RIVERSIDE-BRIDGING-PROTOCOL recommend about perioperative bridging?";
const REPO_ROOT = resolve(process.cwd(), "..");

let tenant: Tenant;
let answerId: string;

test.beforeAll(async () => {
  tenant = await newTenant("E2E Living");
  await rows("DELETE FROM public.documents WHERE title = $1", ["Riverside Perioperative Bridging Protocol"]);
});

test("a followed answer is superseded when new evidence arrives", async ({ page }) => {
  // 1. Ask before the evidence exists. The corpus has nothing, so it abstains.
  const first = await ask(QUESTION, { token: tenant.token });
  const result = resultOf(first.events);
  answerId = String(result.answer_id);
  expect(result.abstained, "nothing in the corpus answers this yet").toBe(true);

  // 2. Follow it.
  const follow = await call(`/api/v1/answers/${answerId}/follow`, { token: tenant.token, method: "POST" });
  expect(follow.status).toBe(201);

  // 3. The evidence arrives — a real upload through the real endpoint.
  const form = new FormData();
  form.append("file", new Blob([readFileSync(PDF)], { type: "application/pdf" }), "riverside-protocol.pdf");
  form.append("title", "Riverside Perioperative Bridging Protocol");
  const upload = await fetch(`${env.apiUrl}/api/v1/documents/upload`, {
    method: "POST",
    headers: { Authorization: `Bearer ${tenant.token}` },
    body: form,
  });
  expect(upload.status, await upload.text().catch(() => "")).toBe(201);

  // 4. The nightly job runs — the same command a scheduler would run.
  const output = execFileSync(
    "uv",
    ["--directory", "backend", "run", "python", "-m", "scripts.jobs", "living-answers"],
    { cwd: REPO_ROOT, encoding: "utf8", timeout: 240_000 },
  );
  expect(output).toContain("living-answers");

  // 5. A second version exists, and it says why.
  await expect
    .poll(
      async () =>
        (await rows("SELECT version FROM public.answer_versions WHERE answer_id = $1", [answerId])).length,
      { timeout: 30_000 },
    )
    .toBeGreaterThanOrEqual(2);

  const versions = await json<{
    following: boolean;
    superseded: boolean;
    versions: { version: number; content: string; diff: { reasons: string[] } | null }[];
  }>(`/api/v1/answers/${answerId}/versions`, { token: tenant.token });
  expect(versions.following).toBe(true);
  expect(versions.superseded).toBe(true);
  expect(versions.versions.map((v) => v.version)).toEqual([1, 2]);
  const latest = versions.versions[1];
  expect(latest.diff?.reasons.join(" "), "a supersede has to say why").toMatch(/new source|confidence|rewritten/i);

  // 6. The clinician is told, and the diff view shows the change.
  const notification = await one<{ type: string; payload: string }>(
    "SELECT type, payload::text AS payload FROM public.notifications WHERE org_id = $1 AND user_id = $2 ORDER BY created_at DESC LIMIT 1",
    [tenant.orgId, tenant.id],
  );
  expect(notification?.type).toBe("answer_updated");
  expect(JSON.parse(notification!.payload).answer_id).toBe(answerId);

  await signIn(page, tenant);
  await page.goto("/app/notifications");
  await expect(page.getByText(/new version/i).first()).toBeVisible({ timeout: 30_000 });

  await page.goto(`/app/answers/${answerId}/versions`);
  // Both versions are offered for comparison, and the page says what changed.
  const newer = page.getByLabel("Newer version");
  await expect(newer).toBeVisible({ timeout: 30_000 });
  expect(await newer.locator("option").allInnerTexts()).toEqual(
    expect.arrayContaining([expect.stringContaining("v2")]),
  );
  const changed = page.getByRole("region", { name: "What changed" });
  await expect(changed).toBeVisible();
  await expect(changed).toContainText(/new source|confidence|rewritten/i);
});
