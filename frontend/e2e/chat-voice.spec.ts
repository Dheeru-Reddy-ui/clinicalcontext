import { resolve } from "node:path";

import { chromium, expect, test, type Browser, type Page } from "@playwright/test";

import { newTenant, signIn } from "./fixtures/auth";
import { rows } from "./fixtures/db";

/**
 * Voice in the chat, end to end, with a real spoken question.
 *
 * Chromium plays a WAV recording as the microphone — a person asking how
 * type 2 diabetes is managed with metformin — so the whole path is real:
 * the browser detects the speech and the pause after it, the server
 * transcribes the clip (faster-whisper locally, Deepgram when deployed),
 * the question runs as an ordinary chat turn, and the answer comes back as
 * speech from /voice/speak.
 */

const SPEECH = resolve(process.cwd(), "e2e/fixtures/audio/golden-01-padded.wav");

async function voicePage(label: string): Promise<{ browser: Browser; page: Page; orgId: string }> {
  const tenant = await newTenant(`E2E Chat Voice ${label}`);
  const browser = await chromium.launch({
    args: [
      "--use-fake-ui-for-media-stream",
      "--use-fake-device-for-media-stream",
      `--use-file-for-fake-audio-capture=${SPEECH}`,
      "--autoplay-policy=no-user-gesture-required",
    ],
  });
  const context = await browser.newContext({
    permissions: ["microphone"],
    baseURL: process.env.E2E_BASE_URL ?? "http://localhost:3005",
  });
  const page = await context.newPage();
  await signIn(page, tenant);
  return { browser, page, orgId: tenant.orgId };
}

test("a spoken question is heard, answered in the chat, and the answer is spoken", async () => {
  const { browser, page, orgId } = await voicePage("turn");
  try {
    await page.goto("/app/chat");
    const speak = page.waitForResponse(
      (r) => r.url().includes("/api/v1/voice/speak") && r.request().method() === "POST",
      { timeout: 150_000 },
    );
    await page.getByTestId("voice-mode").click();
    const bar = page.getByTestId("voice-bar");
    await expect(bar).toHaveAttribute("data-phase", "listening");

    // The recording is transcribed and sent as the person's own message.
    const asked = page.getByTestId("chat-user").first();
    await expect(asked).toContainText(/metformin|diabetes/i, { timeout: 90_000 });

    // The answer arrives in the thread and is read aloud.
    const spoken = await speak;
    expect(spoken.status()).toBe(200);
    expect(spoken.headers()["content-type"]).toContain("audio/wav");
    await expect(page.getByTestId("chat-assistant").first()).toHaveAttribute("data-status", "done", {
      timeout: 90_000,
    });

    await page.getByTestId("voice-end").click();
    await expect(bar).toBeHidden();

    // A spoken turn is an ordinary chat turn: stored like one.
    const stored = await rows<{ kind: string }>(
      `SELECT s.kind FROM public.queries q JOIN public.query_sessions s ON s.id = q.session_id
        WHERE q.org_id = $1 AND q.raw_query ~* '(metformin|diabetes)'`,
      [orgId],
    );
    expect(stored.length).toBeGreaterThan(0);
    expect(stored[0]?.kind).toBe("chat");
  } finally {
    await browser.close();
  }
});

test("the old Voice tab opens the chat with voice ready, and is gone from the menu", async () => {
  const { browser, page } = await voicePage("redirect");
  try {
    await page.goto("/app/voice");
    await expect(page).toHaveURL(/\/app\/chat\?voice=1/);
    await expect(page.getByTestId("voice-bar")).toHaveAttribute("data-phase", "ready");
    await expect(page.getByRole("link", { name: "Voice", exact: true })).toHaveCount(0);
  } finally {
    await browser.close();
  }
});
