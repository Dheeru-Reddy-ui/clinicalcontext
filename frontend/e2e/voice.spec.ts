import { existsSync } from "node:fs";
import { resolve } from "node:path";

import { chromium, expect, test, type Browser, type Page } from "@playwright/test";

import { newTenant, signIn, type Tenant } from "./fixtures/auth";
import { one, rows } from "./fixtures/db";

/**
 * Gate 10: the voice path, in a real browser, with a real microphone.
 *
 * Chromium can play a WAV file as the microphone
 * (`--use-file-for-fake-audio-capture`), so these drive the whole cascade —
 * AudioWorklet capture → WebSocket → STT → the same guardrails and graph as
 * text → TTS — from the browser, using the same spoken fixtures the voice
 * harness uses. Guardrail parity is proven here rather than assumed: the PHI
 * fixture is *spoken*, and the assertion is that nothing was generated.
 *
 * Each scenario needs its own microphone file, and the file is a launch
 * argument, so each test launches its own browser.
 */

// The harness fixtures with a minute of silence appended: Chromium loops
// the microphone file, and without a pause the endpointer never hears the
// speaker stop, so a turn would never commit — and once one does, the next
// loop barges in mid-answer. Built by `uv run python -m scripts.make_e2e_audio`.
const FIXTURES = resolve(process.cwd(), "e2e/fixtures/audio");
const FIRST_AUDIO_BUDGET_MS = 20_000;

async function speaking(
  fixture: string,
  label: string,
): Promise<{ browser: Browser; page: Page; tenant: Tenant }> {
  const microphone = resolve(FIXTURES, fixture);
  if (!existsSync(microphone)) {
    throw new Error(
      `${fixture} is missing. These are derived from the voice harness fixtures; build them with:
` +
        "  uv --directory backend run python -m scripts.make_e2e_audio",
    );
  }
  // A tenant per scenario: every assertion here reads `voice_turns` by
  // organisation, so a shared tenant would let one scenario see another's
  // turns.
  const tenant = await newTenant(`E2E Voice ${label}`);
  const browser = await chromium.launch({
    args: [
      "--use-fake-ui-for-media-stream",
      "--use-fake-device-for-media-stream",
      `--use-file-for-fake-audio-capture=${microphone}`,
      "--autoplay-policy=no-user-gesture-required",
    ],
  });
  const context = await browser.newContext({
    permissions: ["microphone"],
    baseURL: process.env.E2E_BASE_URL ?? "http://localhost:3005",
  });
  const page = await context.newPage();
  await signIn(page, tenant);
  await page.goto("/app/voice");
  await page.getByTestId("voice-start").click();
  return { browser, page, tenant };
}

/**
 * The recognizer sometimes hears a look-alike drug name in an ordinary
 * question and asks which was meant — that is the LASA gate doing its job,
 * not a failure. When it happens mid-answer, confirm and carry on.
 */
async function answerAnyConfirmation(page: Page): Promise<void> {
  const confirm = page.getByTestId("lasa-confirm");
  if (await confirm.isVisible().catch(() => false)) {
    await confirm.getByRole("button").first().click();
  }
}

test("a spoken question comes back as a cited, spoken answer", async () => {
  const { browser, page, tenant } = await speaking("golden-01-padded.wav", "answer");
  try {
    // The session is live and the recognizer is listening.
    await expect(page.getByText(/LISTENING/i).first()).toBeVisible({ timeout: 30_000 });

    // The question is transcribed...
    await expect(page.getByText(/metformin/i).first()).toBeVisible({ timeout: FIRST_AUDIO_BUDGET_MS });

    // ...and answered out loud, with citations on the spoken sentences.
    await expect(page.getByText(/speaking|listening/i).first()).toBeVisible({ timeout: 60_000 });
    await answerAnyConfirmation(page);
    await expect
      .poll(
        async () =>
          (
            await rows<{ outcome: string }>(
              "SELECT outcome::text AS outcome FROM public.voice_turns WHERE org_id = $1",
              [tenant.orgId],
            )
          ).map((r) => r.outcome),
        { timeout: 90_000 },
      )
      .toContain("answered");

    const turn = await one<{ transcript_final: string; query_id: string; latency: string }>(
      `SELECT transcript_final, query_id, latency::text AS latency FROM public.voice_turns
       WHERE org_id = $1 AND outcome = 'answered' ORDER BY created_at DESC LIMIT 1`,
      [tenant.orgId],
    );
    expect(turn!.transcript_final.toLowerCase()).toContain("metformin");

    // The spoken answer is a real cited answer, from the same graph as text.
    const answer = await one<{ citations: string; abstained: boolean }>(
      "SELECT citations::text AS citations, abstained FROM public.answers WHERE query_id = $1",
      [turn!.query_id],
    );
    expect(JSON.parse(answer!.citations).length).toBeGreaterThan(0);

    // And the browser really heard audio: the waterfall records the moment
    // the client started playing it.
    const latency = JSON.parse(turn!.latency) as { total_first_audio_ms: number | null };
    expect(latency.total_first_audio_ms).toBeGreaterThan(0);
  } finally {
    await browser.close();
  }
});

test("interrupting the agent stops the audio immediately", async () => {
  const { browser, page, tenant } = await speaking("golden-01-padded.wav", "barge-in");
  try {
    // Wait until the agent is actually talking, then cut it off.
    const interrupt = page.getByTestId("voice-interrupt");
    await answerAnyConfirmation(page);
    await expect(interrupt).toBeVisible({ timeout: 120_000 });
    await interrupt.click();

    // The client measures the stop in its own audio thread and reports it;
    // that number is what the budget is about, so that is what is asserted.
    await expect
      .poll(
        async () =>
          (
            await one<{ count: number }>(
              `SELECT (barge_in ->> 'count')::int AS count FROM public.voice_turns
               WHERE org_id = $1 ORDER BY created_at DESC LIMIT 1`,
              [tenant.orgId],
            )
          )?.count ?? 0,
        { timeout: 60_000 },
      )
      .toBeGreaterThan(0);

    const bargeIn = JSON.parse(
      (
        await one<{ barge_in: string }>(
          `SELECT barge_in::text AS barge_in FROM public.voice_turns
           WHERE org_id = $1 AND (barge_in ->> 'count')::int > 0 ORDER BY created_at DESC LIMIT 1`,
          [tenant.orgId],
        )
      )!.barge_in,
    ) as { count: number; stop_ms: number[] };
    expect(bargeIn.stop_ms.length, "the client reported a stop latency").toBeGreaterThan(0);
    for (const latency of bargeIn.stop_ms) {
      expect(latency, "audio stops within the 150 ms budget").toBeLessThanOrEqual(150);
    }

    // And the agent really did stop: it is listening again.
    await expect(page.getByText(/listening/i).first()).toBeVisible({ timeout: 30_000 });
  } finally {
    await browser.close();
  }
});

test("a look-alike drug name is confirmed, never guessed", async () => {
  const { browser, page, tenant } = await speaking("lasa-01-padded.wav", "lasa");
  try {
    // The agent asks which drug was meant instead of picking one.
    await expect(page.getByTestId("lasa-confirm")).toBeVisible({ timeout: 90_000 });
    const prompt = await page.getByTestId("lasa-confirm").innerText();
    expect(prompt.toLowerCase()).toMatch(/hydroxyzine|hydralazine|did you mean/);

    await expect
      .poll(
        async () =>
          (
            await rows<{ outcome: string }>(
              "SELECT outcome::text AS outcome FROM public.voice_turns WHERE org_id = $1",
              [tenant.orgId],
            )
          ).map((r) => r.outcome),
        { timeout: 60_000 },
      )
      .toContain("confirm_requested");
  } finally {
    await browser.close();
  }
});

test("a spoken query containing PHI is blocked with nothing generated", async () => {
  const { browser, page, tenant } = await speaking("adv-01-padded.wav", "phi");
  try {
    await expect
      .poll(
        async () =>
          await one<{ outcome: string; query_id: string }>(
            `SELECT outcome::text AS outcome, query_id FROM public.voice_turns
             WHERE org_id = $1 ORDER BY created_at DESC LIMIT 1`,
            [tenant.orgId],
          ),
        { timeout: 120_000 },
      )
      .toMatchObject({ outcome: "blocked_phi" });

    const turn = (await one<{ query_id: string }>(
      "SELECT query_id FROM public.voice_turns WHERE org_id = $1 ORDER BY created_at DESC LIMIT 1",
      [tenant.orgId],
    ))!;

    // Parity with the text path, proven the same way: no answer row, and
    // nothing reached generation.
    expect(await rows("SELECT id FROM public.answers WHERE query_id = $1", [turn.query_id])).toHaveLength(0);
    expect(
      await rows("SELECT id FROM public.cost_events WHERE query_id = $1 AND component = 'generation'", [
        turn.query_id,
      ]),
      "a blocked spoken turn generates nothing",
    ).toHaveLength(0);

    // The clinician is told, on screen.
    await expect(page.getByText(/patient information|wasn't sent|can't use patient/i).first()).toBeVisible({
      timeout: 30_000,
    });
  } finally {
    await browser.close();
  }
});
