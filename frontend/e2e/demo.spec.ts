import { mkdirSync } from "node:fs";
import { resolve } from "node:path";

import { chromium, expect, test } from "@playwright/test";

import { newTenant, signIn } from "./fixtures/auth";

/**
 * The demo reel (Phase 14.6) — real footage, not a mock-up.
 *
 *     pnpm exec playwright test demo --project=demo
 *
 * Playwright records the browser while this drives the product through the
 * four scenes the brief asks for, contradiction first:
 *
 *   1. a question where the sources disagree, and the app says so
 *   2. a question containing PHI, stopped before anything is retrieved
 *   3. a question the corpus cannot answer, and the app abstains
 *   4. the same product by voice, answered out loud from a real microphone
 *
 * The result is a .webm per scene under `e2e/.artifacts/demo/`, about ninety
 * seconds in total, ready to be trimmed and narrated. It is deliberately
 * outside the verification suite: it proves nothing the other specs do not,
 * it is there to be watched.
 */

const OUT = resolve(process.cwd(), "e2e/.artifacts/demo");
const FIXTURES = resolve(process.cwd(), "e2e/fixtures/audio");

const CONTRADICTION = "Should beta-blockers be used after myocardial infarction?";
const PHI = "What is the treatment for AF in John Smith, DOB 03/14/1982?";
const UNANSWERABLE = "What antibiotics are recommended for Lyme disease?";

// Slow enough to read on screen. A demo that moves at machine speed shows
// nothing.
const BEAT = 2_500;

test("record the demo reel", async () => {
  test.setTimeout(600_000);
  mkdirSync(OUT, { recursive: true });

  const tenant = await newTenant("Riverside Cardiology");
  const browser = await chromium.launch({
    args: [
      "--use-fake-ui-for-media-stream",
      "--use-fake-device-for-media-stream",
      `--use-file-for-fake-audio-capture=${resolve(FIXTURES, "golden-01-padded.wav")}`,
      "--autoplay-policy=no-user-gesture-required",
      "--hide-scrollbars",
    ],
  });
  const context = await browser.newContext({
    viewport: { width: 1280, height: 720 },
    permissions: ["microphone"],
    baseURL: process.env.E2E_BASE_URL ?? "http://localhost:3005",
    recordVideo: { dir: OUT, size: { width: 1280, height: 720 } },
    colorScheme: "dark",
  });
  const page = await context.newPage();

  try {
    await signIn(page, tenant);
    await page.waitForTimeout(BEAT);

    // Scene 1 — the contradiction. This is the lead because it is the thing
    // no other tool does: two positions, each with its own sources.
    const box = page.getByRole("textbox", { name: /clinical question/i });
    await box.click();
    await box.pressSequentially(CONTRADICTION, { delay: 35 });
    await page.getByRole("button", { name: "Ask", exact: true }).click();
    const contradiction = page.locator('[aria-labelledby="contradiction-heading"]');
    await expect(contradiction).toBeVisible({ timeout: 90_000 });
    await contradiction.scrollIntoViewIfNeeded();
    await page.waitForTimeout(BEAT * 2);

    // A citation, opened: every claim traces to a passage.
    await page.getByRole("button", { name: "Open source 1" }).first().click();
    await expect(page.getByTestId("cited-passage").first()).toBeVisible();
    await page.waitForTimeout(BEAT * 2);

    // Scene 2 — PHI, stopped at the door.
    await page.goto("/app");
    const phiBox = page.getByRole("textbox", { name: /clinical question/i });
    await phiBox.click();
    await phiBox.pressSequentially(PHI, { delay: 30 });
    await page.waitForTimeout(BEAT / 2);
    await page.getByRole("button", { name: "Ask", exact: true }).click();
    await expect(page.locator('[aria-labelledby="phi-heading"]')).toBeVisible({ timeout: 60_000 });
    await page.waitForTimeout(BEAT * 2);

    // Scene 3 — the abstention. Saying "I don't know" is the feature.
    await page.goto("/app");
    const abstainBox = page.getByRole("textbox", { name: /clinical question/i });
    await abstainBox.click();
    await abstainBox.pressSequentially(UNANSWERABLE, { delay: 30 });
    await page.getByRole("button", { name: "Ask", exact: true }).click();
    await expect(page.locator('[aria-labelledby="abstain-heading"]')).toBeVisible({ timeout: 90_000 });
    await page.waitForTimeout(BEAT * 2);

    // Scene 4 — the same product, spoken. The fixture audio plays as the
    // microphone; the answer comes back cited and out loud.
    await page.goto("/app/voice");
    await page.getByTestId("voice-start").click();
    await expect(page.getByText(/listening/i).first()).toBeVisible({ timeout: 30_000 });
    await expect(page.getByText(/metformin/i).first()).toBeVisible({ timeout: 60_000 });
    await page.waitForTimeout(BEAT * 3);

    // And the numbers behind all of it, in public.
    await page.goto("/methodology");
    await expect(page.getByRole("heading", { name: "Methodology" })).toBeVisible({ timeout: 30_000 });
    await page.mouse.wheel(0, 900);
    await page.waitForTimeout(BEAT);
    await page.mouse.wheel(0, 1200);
    await page.waitForTimeout(BEAT);
  } finally {
    // The video is only written when the context closes.
    await context.close();
    await browser.close();
  }
  console.log(`demo footage written to ${OUT}`);
});
