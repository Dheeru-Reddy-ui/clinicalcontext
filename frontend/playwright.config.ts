import { defineConfig, devices } from "@playwright/test";

/**
 * End-to-end verification (Phase 14.2).
 *
 * These run against the **real** stack — the Next.js app, the FastAPI
 * backend, Postgres, Redis and the local Supabase auth — because the point of
 * the suite is to re-prove the phase gates on the system as deployed, not on
 * a set of mocks. Start both dev servers first:
 *
 *     uv --directory backend run uvicorn app.main:app --port 8010
 *     pnpm -C frontend dev --port 3005
 *
 * One worker, always: the specs share one database, one Redis (rate-limit
 * buckets and the semantic cache are per-tenant but the anon/demo buckets are
 * global), and several assert on row counts. Parallelism here would buy
 * seconds and cost determinism.
 */

const BASE_URL = process.env.E2E_BASE_URL ?? "http://localhost:3005";

export default defineConfig({
  testDir: "./e2e",
  outputDir: "./e2e/.artifacts",
  fullyParallel: false,
  workers: 1,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  // A cold query on the offline backend runs the whole graph; voice fixtures
  // play at real-time pace. These are not unit-test timings.
  timeout: 120_000,
  expect: { timeout: 20_000 },
  // The JSON report lives outside `outputDir`, which Playwright wipes at the
  // start of every run — `scripts/verify.py` reads this file to report which
  // phase gates the E2E suite covered.
  reporter: [["list"], ["json", { outputFile: "e2e/.report/results.json" }]],
  use: {
    baseURL: BASE_URL,
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
    video: "off",
    actionTimeout: 20_000,
  },
  projects: [
    {
      name: "app",
      use: { ...devices["Desktop Chrome"] },
      // The voice specs need a microphone, and the demo reel is a recording,
      // not a check.
      testIgnore: [/voice\.spec\.ts/, /demo\.spec\.ts/],
    },
    {
      // Not part of verification: it drives the product slowly and records
      // the result. `pnpm exec playwright test demo --project=demo`.
      name: "demo",
      testMatch: /demo\.spec\.ts/,
      use: { ...devices["Desktop Chrome"] },
    },
    {
      // The voice spec needs a microphone. Chromium can play a WAV file as
      // one, which is how the browser path gets exercised for real.
      name: "voice",
      testMatch: /(?<!demo)voice\.spec\.ts/,
      use: {
        ...devices["Desktop Chrome"],
        permissions: ["microphone"],
        launchOptions: {
          args: [
            "--use-fake-ui-for-media-stream",
            "--use-fake-device-for-media-stream",
            "--autoplay-policy=no-user-gesture-required",
          ],
        },
      },
    },
  ],
});
