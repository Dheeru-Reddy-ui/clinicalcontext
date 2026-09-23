import { resolve } from "node:path";

import { chromium, expect, test, type Browser, type Page } from "@playwright/test";

import { newTenant, signIn } from "./fixtures/auth";

/**
 * Recording, before any session opens.
 *
 * On the first deployment the voice page accepted a click, the browser
 * connected, and nothing was recorded: the server had no speech engine, its
 * config still reported ready, and the page retried the refusal as though it
 * were a network blip. These pin what replaced that — a microphone check
 * that shows a real voice moving a real meter, and a page that says plainly
 * when the server cannot run voice rather than letting someone talk into it.
 *
 * Chromium plays a WAV file as the microphone; it is the voice harness's
 * spoken fixture, so the meter is moved by actual speech.
 */

const SPEECH = resolve(process.cwd(), "e2e/fixtures/audio/golden-01-padded.wav");

async function voicePage(label: string): Promise<{ browser: Browser; page: Page }> {
  const tenant = await newTenant(`E2E Recording ${label}`);
  const browser = await chromium.launch({
    args: [
      "--use-fake-ui-for-media-stream",
      "--use-fake-device-for-media-stream",
      `--use-file-for-fake-audio-capture=${SPEECH}`,
    ],
  });
  const context = await browser.newContext({
    permissions: ["microphone"],
    baseURL: process.env.E2E_BASE_URL ?? "http://localhost:3005",
  });
  const page = await context.newPage();
  await signIn(page, tenant);
  return { browser, page };
}

test("the microphone check shows a spoken voice moving the level meter", async () => {
  const { browser, page } = await voicePage("mic check");
  try {
    await page.goto("/app/voice");
    const check = page.getByTestId("mic-check");
    await expect(check).toBeVisible();
    await page.getByTestId("mic-test").click();

    const meter = check.getByRole("meter", { name: "Microphone level" });
    await expect(meter).toBeVisible();
    // Speech, not a constant tone: the bar must rise past the "heard" mark.
    await expect(page.getByTestId("mic-heard")).toHaveText(/coming through/i, { timeout: 15_000 });

    // Stopping the test releases the device before a session would open.
    await page.getByTestId("mic-test").click();
    await expect(meter).toBeHidden();
    await expect(page.getByTestId("voice-start")).toBeEnabled();
  } finally {
    await browser.close();
  }
});

test("a server that cannot run voice says why, and Start is disabled", async () => {
  const { browser, page } = await voicePage("unavailable");
  try {
    const reason =
      "This server has no speech-recognition engine installed — the free deployment leaves it out to fit in memory.";
    // The config as a server without an engine or key returns it.
    await page.route("**/api/v1/voice/config", async (route) => {
      const response = await route.fetch();
      const body = { ...(await response.json()), available: false, unavailable_reason: reason };
      await route.fulfill({ response, json: body });
    });
    await page.goto("/app/voice");

    const notice = page.getByTestId("voice-unavailable");
    await expect(notice).toBeVisible();
    await expect(notice).toContainText("no speech-recognition engine");
    await expect(page.getByTestId("voice-start")).toBeDisabled();
    // Nothing offers to record into a server that cannot hear it.
    await expect(page.getByTestId("mic-check")).toHaveCount(0);
  } finally {
    await browser.close();
  }
});
