import { expect, test } from "@playwright/test";

import { ask, call, json, resultOf } from "./fixtures/api";
import { accessToken, createAccount, newTenant, signIn } from "./fixtures/auth";
import { one, rows } from "./fixtures/db";

/**
 * Gates 14 and 15: a binder is shared workspace — a colleague sees the
 * annotation and who made it — and a permalink is only as public as the org
 * says it is, revocable at any moment.
 */

const GROUNDED = "How is type 2 diabetes managed with metformin?";

test("a saved answer's annotation is visible to another member, with attribution", async ({ page, browser }) => {
  const tenant = await newTenant("E2E Binders");

  // A colleague in the same org, invited the normal way.
  const colleagueEmail = `colleague-${Date.now()}@cc-e2e.org`;
  const invite = await json<{ token: string }>("/api/v1/orgs/invites", {
    token: tenant.token,
    body: { email: colleagueEmail, role: "clinician" },
  });
  const colleague = await createAccount(colleagueEmail);
  const colleagueToken = await accessToken(colleague);
  await json("/api/v1/orgs/invites/accept", { token: colleagueToken, body: { token: invite.token } });

  // A binder shared with the organisation — the default is private, and a
  // private binder is deliberately creator-only.
  await signIn(page, tenant);
  await page.goto("/app/binders");
  await page.getByRole("button", { name: "New binder" }).click();
  await page.getByLabel("Title").fill("Diabetes rounds");
  await page.getByRole("switch", { name: /share with the organisation/i }).click();
  await page.getByRole("button", { name: /^Create/ }).last().click();

  await expect
    .poll(
      async () =>
        (
          await one<{ title: string; visibility: string }>(
            "SELECT title, visibility::text AS visibility FROM public.binders WHERE org_id = $1 ORDER BY created_at DESC LIMIT 1",
            [tenant.orgId],
          )
        )?.visibility ?? null,
      { timeout: 20_000 },
    )
    .toBe("org");
  const binder = (await one<{ id: string; title: string }>(
    "SELECT id, title FROM public.binders WHERE org_id = $1 ORDER BY created_at DESC LIMIT 1",
    [tenant.orgId],
  ))!;
  expect(binder.title).toBe("Diabetes rounds");

  // Ask, keep the answer, and keep the passage behind citation [1].
  await page.goto("/app");
  await page.getByRole("textbox", { name: /clinical question/i }).fill(GROUNDED);
  await page.getByRole("button", { name: "Ask", exact: true }).click();
  await expect(page.getByRole("article", { name: "Answer" }).first()).toBeVisible({ timeout: 90_000 });

  await page.getByRole("button", { name: "Save to binder" }).click();
  await page.getByRole("button", { name: /Diabetes rounds/ }).first().click();

  await page.getByRole("button", { name: "Open source 1" }).first().click();
  await page.getByRole("button", { name: "Save passage" }).click();
  await page.getByRole("button", { name: /Diabetes rounds/ }).first().click();

  await expect
    .poll(
      async () =>
        (await rows("SELECT id FROM public.binder_items WHERE binder_id = $1", [binder.id])).length,
      { timeout: 20_000 },
    )
    .toBe(2);

  // Annotate the passage, the way a reader highlights a sentence.
  await page.goto(`/app/binders/${binder.id}`);
  const passage = page.getByTestId("annotatable-passage").first();
  await expect(passage).toBeVisible({ timeout: 30_000 });
  await passage.evaluate((element) => {
    const walker = document.createTreeWalker(element, NodeFilter.SHOW_TEXT);
    const node = walker.nextNode();
    if (!node || !node.textContent) throw new Error("no passage text to select");
    const range = document.createRange();
    range.setStart(node, 0);
    range.setEnd(node, Math.min(40, node.textContent.length));
    const selection = window.getSelection();
    selection?.removeAllRanges();
    selection?.addRange(range);
    element.dispatchEvent(new MouseEvent("mouseup", { bubbles: true }));
  });
  const note = page.getByRole("dialog", { name: "Annotate selection" });
  await expect(note).toBeVisible();
  await note.getByRole("textbox").fill("Check the renal dosing caveat before rounds.");
  await note.getByRole("button", { name: "Save" }).click();

  await expect
    .poll(async () => (await rows("SELECT id FROM public.annotations WHERE org_id = $1", [tenant.orgId])).length, {
      timeout: 20_000,
    })
    .toBe(1);

  // The colleague opens the same binder in their own session and sees it,
  // attributed — a shared binder is a workspace, not a private scrapbook.
  const colleagueContext = await browser.newContext();
  const colleaguePage = await colleagueContext.newPage();
  await signIn(colleaguePage, colleague);
  await colleaguePage.goto(`/app/binders/${binder.id}`);
  await expect(colleaguePage.getByText("Check the renal dosing caveat before rounds.")).toBeVisible({
    timeout: 30_000,
  });
  // Attribution is the author's name from their profile, not "Colleague".
  await expect(colleaguePage.getByText(tenant.fullName, { exact: false }).first()).toBeVisible();
  await colleagueContext.close();
});

test("a permalink is public until the org turns sharing off", async ({ page, browser }) => {
  const tenant = await newTenant("E2E Sharing");
  const { events } = await ask(GROUNDED, { token: tenant.token });
  const answerId = String(resultOf(events).answer_id);

  const link = await json<{ slug: string; enabled: boolean }>("/api/v1/sharing/links", {
    token: tenant.token,
    body: { answer_id: answerId },
  });
  expect(link.enabled).toBe(true);

  // Logged out, in a clean context: the answer renders.
  const anon = await browser.newContext();
  const anonPage = await anon.newPage();
  await anonPage.goto(`/a/${link.slug}`);
  await expect(anonPage.getByRole("heading", { name: /metformin|diabetes/i }).first()).toBeVisible({
    timeout: 30_000,
  });
  await expect(anonPage.getByText(/sources|citation/i).first()).toBeVisible();

  // The org disables public sharing entirely.
  const policy = await call("/api/v1/sharing/policy", {
    token: tenant.token,
    method: "PATCH",
    body: { public_sharing_enabled: false },
  });
  expect(policy.status).toBe(200);

  // The same URL is now a clean 404 — not an error page, not a redirect that
  // leaks that the answer exists.
  const direct = await call(`/api/public/answers/${link.slug}`);
  expect(direct.status).toBe(404);

  await anonPage.goto(`/a/${link.slug}`);
  await expect(anonPage.getByRole("heading", { name: /isn't available|not available/i })).toBeVisible({
    timeout: 30_000,
  });
  await expect(anonPage.getByText(/turned public sharing off|revoked/i).first()).toBeVisible();
  await anon.close();

  // And the owner can still see it in their own app.
  await signIn(page, tenant);
  await page.goto("/app/history");
  await expect(page.getByText(/metformin/i).first()).toBeVisible({ timeout: 30_000 });
});
