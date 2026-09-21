import { expect, test } from "@playwright/test";

import { ask, call, resultOf } from "./fixtures/api";
import { newTenant, signIn } from "./fixtures/auth";
import { one, rows } from "./fixtures/db";

/**
 * Gates 11 and 12: a thumbs-down with a reason is a row, and hitting the rate
 * limit is a 429 the UI explains rather than a spinner that never ends.
 */

const GROUNDED = "How is type 2 diabetes managed with metformin?";

test("a thumbs-down with a reason writes the feedback row and queues it for review", async ({ page }) => {
  const tenant = await newTenant("E2E Feedback");
  await signIn(page, tenant);

  const box = page.getByRole("textbox", { name: /clinical question/i });
  await box.fill(GROUNDED);
  await page.getByRole("button", { name: "Ask", exact: true }).click();
  await expect(page.getByRole("article", { name: "Answer" }).first()).toBeVisible({ timeout: 90_000 });

  await page.getByRole("button", { name: "Not helpful — choose a reason" }).click();
  await page.getByRole("radio", { name: /not supported by the sources|unsupported/i }).first().click();
  await page.getByRole("button", { name: "Send" }).click();

  // The row, with its reason — the thumb is optimistic, the row is not.
  await expect
    .poll(
      async () => {
        const row = await one<{ rating: string; reason: string | null }>(
          `SELECT f.rating::text AS rating, f.reason::text AS reason FROM public.feedback f
           WHERE f.org_id = $1 ORDER BY f.created_at DESC LIMIT 1`,
          [tenant.orgId],
        );
        return row ? `${row.rating}:${row.reason}` : null;
      },
      { timeout: 20_000 },
    )
    .toMatch(/^down:(unsupported|wrong)$/);

  // Phase 12's loop: a thumbs-down marked wrong or unsupported is queued for a
  // reviewer to promote into the golden set.
  const queued = await rows("SELECT status::text AS status FROM public.golden_reviews WHERE org_id = $1", [
    tenant.orgId,
  ]);
  expect(queued, "the case is queued for review").toHaveLength(1);
  expect(queued[0].status).toBe("pending");
});

test("hitting the rate limit returns 429 and the UI says so", async ({ page }) => {
  // A free-plan tenant: 20 user requests a minute, so the limit is reachable.
  const tenant = await newTenant("E2E Limits", "free");

  // Spend the budget through the API, then watch the UI meet the wall.
  let limited: Response | null = null;
  for (let i = 0; i < 30 && !limited; i += 1) {
    const response = await call("/api/v1/queries", {
      token: tenant.token,
      method: "POST",
      body: { query: `rate limit probe ${i} — ${GROUNDED}` },
    });
    if (response.status === 429) limited = response;
    else await response.text();
  }
  expect(limited, "the limiter eventually refuses").not.toBeNull();
  expect(limited!.status).toBe(429);
  const retryAfter = limited!.headers.get("retry-after");
  expect(Number(retryAfter)).toBeGreaterThan(0);
  const body = (await limited!.json()) as { error: { code: string; message: string } };
  expect(body.error.code).toBe("rate_limited");

  await signIn(page, tenant);
  const box = page.getByRole("textbox", { name: /clinical question/i });
  await box.fill(GROUNDED);
  await page.getByRole("button", { name: "Ask", exact: true }).click();

  // Not a dead spinner: an alert, the reason, when to retry, and a way to.
  const alert = page.getByRole("alert").filter({ hasText: /could not be completed/i }).first();
  await expect(alert).toBeVisible({ timeout: 30_000 });
  await expect(alert).toContainText(/try again in \d+s/i);
  await expect(page.getByRole("button", { name: "Try again" })).toBeVisible();
});

test("an abstention is still a real answer row that can be rated", async () => {
  const tenant = await newTenant("E2E Abstain Feedback");
  const { events } = await ask("What is the first-line treatment for head lice?", { token: tenant.token });
  const result = resultOf(events);
  expect(result.abstained).toBe(true);

  const response = await call("/api/v1/feedback", {
    token: tenant.token,
    body: { answer_id: result.answer_id, rating: "up" },
  });
  expect(response.status).toBe(201);
  const row = await one<{ rating: string }>(
    "SELECT rating::text AS rating FROM public.feedback WHERE answer_id = $1",
    [result.answer_id],
  );
  expect(row?.rating).toBe("up");
});
