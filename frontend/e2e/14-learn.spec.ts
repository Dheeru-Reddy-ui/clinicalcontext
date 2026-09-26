import path from "node:path";

import { expect, test } from "@playwright/test";

import { newTenant, signIn } from "./fixtures/auth";
import { rows, scalar } from "./fixtures/db";

/**
 * Learn's tools end to end, against the offline backend: the AI tutor (a quiz
 * written from the library and answered; a case that says it needs the AI
 * writer; the progress it leaves), the note summarizer (identifiers removed,
 * every point tied to its line, nothing stored), and Ask-this-Paper (upload,
 * a question answered from the paper with its page, delete). Then two fixes
 * that came with them: conditions typed in the symptom check, and a Midnight
 * look that no longer duplicates another.
 */

// Kept between runs (Playwright empties .artifacts at the start of each).
const SHOTS = path.join(__dirname, ".shots", "learn");

test("Learn's tabs reach every tool, and the subjects page offers them", async ({ page }) => {
  const tenant = await newTenant("E2E Learn Tabs");
  await signIn(page, tenant);
  await page.goto("/app/learn");
  await expect(page.getByTestId("learn-tool-tutor")).toBeVisible();
  await page.getByTestId("learn-tab-tutor").click();
  await expect(page).toHaveURL(/\/app\/learn\/tutor/);
  await expect(page.getByTestId("tutor")).toBeVisible();
  await page.getByTestId("learn-tab-notes").click();
  await expect(page.getByTestId("note-summarizer")).toBeVisible();
  await page.getByTestId("learn-tab-papers").click();
  await expect(page.getByTestId("paper-library")).toBeVisible();
  await page.getByTestId("learn-tab-learn").click();
  await expect(page.getByTestId("learn-filter")).toBeVisible();
});

test("a quiz is written from the library, answered, and counted in progress", async ({ page }) => {
  const tenant = await newTenant("E2E Tutor Quiz");
  await signIn(page, tenant);
  await page.goto("/app/learn/tutor?mode=quiz");
  await page.getByTestId("tutor-topic").fill("apixaban warfarin stroke prevention in atrial fibrillation");
  await page.getByTestId("tutor-count-3").click();
  await page.getByTestId("tutor-start").click();

  const runner = page.getByTestId("quiz-runner");
  await expect(runner).toBeVisible({ timeout: 90_000 });
  await page.screenshot({ path: path.join(SHOTS, "quiz.png"), fullPage: true });
  const total = Number((await page.getByTestId("quiz-position").textContent())?.match(/of (\d+)/)?.[1] ?? 0);
  expect(total).toBeGreaterThanOrEqual(2);

  for (let i = 0; i < total; i += 1) {
    await page.getByTestId("quiz-option-0").click();
    await expect(page.getByTestId("quiz-feedback")).toBeVisible();
    // The right answer is marked whichever option was chosen.
    await expect(page.locator('[data-correct="true"]')).toHaveCount(1);
    await page.getByTestId("quiz-next").click();
  }
  await expect(page.getByTestId("quiz-results")).toBeVisible();
  await expect(page.getByTestId("quiz-score")).toContainText(`of ${total} right`);

  const recorded = await scalar<string>("SELECT count(*) FROM public.tutor_attempts WHERE user_id = $1", [tenant.id]);
  expect(Number(recorded)).toBe(total);

  await page.getByTestId("tutor-mode-progress").click();
  await expect(page.getByTestId("progress-view")).toBeVisible();
  await expect(page.getByTestId("progress-topics")).toContainText("apixaban");
  await page.screenshot({ path: path.join(SHOTS, "progress.png"), fullPage: true });
});

test("a case says it needs the AI writer, and offers a quiz instead", async ({ page }) => {
  const tenant = await newTenant("E2E Tutor Case");
  await signIn(page, tenant);
  await page.goto("/app/learn/tutor?mode=case");
  await page.getByTestId("tutor-topic").fill("atrial fibrillation");
  await page.getByTestId("tutor-start").click();
  await expect(page.getByTestId("tutor-unavailable")).toContainText("AI writer", { timeout: 90_000 });
  await page.getByRole("button", { name: "Take a quiz instead" }).click();
  await expect(page.getByTestId("tutor-mode-quiz")).toHaveAttribute("aria-selected", "true");
});

test("a note is summarized with its identifiers removed and nothing stored", async ({ page }) => {
  const tenant = await newTenant("E2E Note Summary");
  await signIn(page, tenant);
  const before = await scalar<string>("SELECT count(*) FROM public.queries WHERE org_id = $1", [tenant.orgId]);

  await page.goto("/app/learn/notes");
  await page.getByTestId("note-sample").click();
  await expect(page.getByTestId("note-text")).toHaveValue(/DISCHARGE SUMMARY/);
  await page.getByTestId("note-summarize").click();

  const summary = page.getByTestId("note-summary");
  await expect(summary).toBeVisible({ timeout: 60_000 });
  await expect(page.getByTestId("note-redactions")).toContainText("name");
  await expect(summary).toContainText("Medications");
  await expect(summary).toContainText("Cefixime 200 mg BD");
  const read = page.getByTestId("note-lines");
  await expect(read).not.toContainText("Priya");
  await expect(read).not.toContainText("KOC-2026-118204");
  await expect(read).toContainText("[NAME]");
  // A line number in the summary finds its line in the note.
  await summary.getByRole("button", { name: /Line \d+ of the note/ }).first().click();
  await expect(read.locator("li.bg-highlight\\/40")).toHaveCount(1);
  await page.screenshot({ path: path.join(SHOTS, "note-summary.png"), fullPage: true });

  const after = await scalar<string>("SELECT count(*) FROM public.queries WHERE org_id = $1", [tenant.orgId]);
  expect(after).toBe(before);
});

test("a paper is uploaded, answers from its own pages, and can be deleted", async ({ page }) => {
  const tenant = await newTenant("E2E Ask A Paper");
  await signIn(page, tenant);
  await page.goto("/app/learn/papers");
  await page.getByTestId("paper-file").setInputFiles(path.join(__dirname, "fixtures", "riverside-protocol.pdf"));
  await expect(page).toHaveURL(/\/app\/learn\/papers\/[0-9a-f-]{36}/, { timeout: 60_000 });
  await expect(page.getByTestId("paper-title")).not.toHaveText("Loading…");
  await expect(page.getByTestId("paper-outline")).toContainText("p. 1");

  await page.getByTestId("paper-quick").first().click();
  const answer = page.getByTestId("chat-assistant").last();
  await expect(answer).toHaveAttribute("data-status", "done", { timeout: 60_000 });
  await expect(answer).toContainText("What the paper says");
  await page.screenshot({ path: path.join(SHOTS, "paper.png"), fullPage: true });

  const sessions = await rows<{ kind: string; document_id: string | null }>(
    "SELECT kind, document_id::text FROM public.query_sessions WHERE user_id = $1",
    [tenant.id],
  );
  expect(sessions).toHaveLength(1);
  expect(sessions[0]?.kind).toBe("paper");
  expect(sessions[0]?.document_id).toMatch(/[0-9a-f-]{36}/);

  await page.goto("/app/learn/papers");
  await expect(page.getByTestId("paper-card")).toHaveCount(1);
  await page.getByTestId("paper-delete").click();
  await page.getByTestId("paper-delete-confirm").click();
  await expect(page.getByTestId("paper-card")).toHaveCount(0);
});

test("conditions typed in the symptom check are read, and unknown ones flagged", async ({ page }) => {
  const tenant = await newTenant("E2E Typed Conditions");
  await signIn(page, tenant);
  await page.goto("/app/treatment");
  await page.getByTestId("profile-age").fill("45");
  const typed = page.getByTestId("profile-other-conditions");
  await typed.fill("CKD");
  await typed.press("Enter");
  await typed.fill("typhoid");
  await typed.press("Enter");
  await expect(page.getByTestId("other-condition")).toHaveCount(2);
  await page.getByRole("button", { name: /^Continue/ }).click();
  await page.getByTestId("complaint-fever").click();

  const read = page.getByTestId("conditions-read");
  await expect(read).toContainText("read as kidney disease");
  await expect(read).toContainText("typhoid");
  await expect(read).toContainText("not covered by the medicine checks");
  await page.screenshot({ path: path.join(SHOTS, "conditions.png"), fullPage: true });
});

test("Midnight is its own look, and matching the device is a switch", async ({ page }) => {
  const tenant = await newTenant("E2E Midnight");
  await signIn(page, tenant);
  await page.goto("/app/settings?section=appearance");
  await page.getByTestId("theme-midnight").click();
  await expect(page.locator("html")).toHaveClass(/midnight/);
  await expect(page.getByTestId("theme-match-device")).toHaveAttribute("aria-checked", "false");
  const background = await page.evaluate(() => getComputedStyle(document.body).backgroundColor);
  // Pure black, however the browser spells it.
  expect(background).toMatch(/^(?:rgb\(0, 0, 0\)|(?:ok)?lab\(0 0 0\)|oklch\(0 0 0\))$/);
  await page.screenshot({ path: path.join(SHOTS, "midnight.png"), fullPage: true });

  await page.getByTestId("theme-dark").click();
  await expect(page.locator("html")).toHaveClass(/dark/);
  const dark = await page.evaluate(() => getComputedStyle(document.body).backgroundColor);
  expect(dark).not.toBe(background);

  await page.getByTestId("theme-match-device").click();
  await expect(page.getByTestId("theme-status")).toContainText("Following your device");
});

test("the Learn pages and the fixes around them log no errors", async ({ page }) => {
  const errors: string[] = [];
  page.on("console", (message) => {
    if (message.type() === "error") errors.push(message.text().slice(0, 300));
  });
  page.on("pageerror", (error) => errors.push(error.message.slice(0, 300)));
  const tenant = await newTenant("E2E Learn Console");
  await signIn(page, tenant);
  for (const route of [
    "/app/learn",
    "/app/learn/tutor",
    "/app/learn/tutor?mode=quiz",
    "/app/learn/tutor?mode=progress",
    "/app/learn/notes",
    "/app/learn/papers",
    "/app/treatment",
    "/app/settings?section=appearance",
  ]) {
    await page.goto(route);
    await page.waitForLoadState("networkidle");
  }
  expect(errors).toEqual([]);
});
