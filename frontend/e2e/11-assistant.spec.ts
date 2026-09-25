import { expect, test, type Page } from "@playwright/test";

import { newTenant, signIn, type Tenant } from "./fixtures/auth";
import { rows } from "./fixtures/db";

/**
 * The chat assistant, the Treatment and Learn tabs, and the website's own
 * chatbot — driven through the real UI against the real backend.
 *
 * The backend in this run has no language model configured, which is the
 * state the free deployment starts in: answers are quoted from the sources,
 * personal symptom questions get the symptom check's guidance, and every
 * assertion here holds without a key. Each spec also checks something the
 * UI cannot fake — a stored row, a dose that must match the NHS table.
 */

const EVIDENCE_QUESTION = "What is first-line anticoagulation in non-valvular atrial fibrillation?";

let tenant: Tenant;

test.beforeAll(async () => {
  tenant = await newTenant(`E2E Assistant ${Date.now().toString().slice(-7)}`);
});

async function sendInChat(page: Page, text: string): Promise<void> {
  const box = page.getByTestId("chat-input").last();
  await box.click();
  await box.fill(text);
  await box.press("Enter");
}

test.describe("signed in", () => {
  test.beforeEach(async ({ page }) => {
    await signIn(page, tenant);
  });

  test("a chat answer streams with cited sources, coloured on the timeline, and is saved", async ({ page }) => {
    await page.goto("/app/chat");
    await page.getByTestId("audience-clinician").click();
    await sendInChat(page, EVIDENCE_QUESTION);

    const answer = page.getByTestId("chat-assistant").last();
    await expect(answer).toHaveAttribute("data-status", "done", { timeout: 90_000 });
    await expect(answer.getByRole("button", { name: /Open source \d+/ }).first()).toBeVisible();
    await expect(answer.getByTestId("chat-check")).toContainText(/cited statements match their sources/);

    // The sources, then the timeline: a cited source is drawn as supporting,
    // never the all-grey timeline this replaced.
    await answer.getByTestId("chat-sources-toggle").click();
    const timeline = answer.getByTestId("evidence-timeline");
    await expect(timeline).toBeVisible();
    await expect(timeline.locator('[aria-label*=", supports"]').first()).toBeVisible();

    // Saved as a conversation of this person's, listed on the left.
    await expect(page.getByTestId("chat-list")).toContainText("first-line anticoagulation");
    const stored = await rows<{ kind: string; audience: string; status: string }>(
      `SELECT s.kind, q.audience, q.status::text AS status
         FROM public.queries q JOIN public.query_sessions s ON s.id = q.session_id
        WHERE q.org_id = $1 AND q.raw_query = $2`,
      [tenant.orgId, EVIDENCE_QUESTION],
    );
    expect(stored).toEqual([{ kind: "chat", audience: "clinician", status: "completed" }]);
  });

  test("a follow-up continues the same conversation", async ({ page }) => {
    await page.goto("/app/chat");
    await sendInChat(page, "What is metformin used for?");
    await expect(page.getByTestId("chat-assistant").last()).toHaveAttribute("data-status", "done", {
      timeout: 90_000,
    });
    await sendInChat(page, "What are its side effects?");
    await expect(page.getByTestId("chat-assistant")).toHaveCount(2);
    await expect(page.getByTestId("chat-assistant").last()).toHaveAttribute("data-status", "done", {
      timeout: 90_000,
    });
    const sessions = await rows<{ turns: number }>(
      `SELECT count(*)::int AS turns FROM public.queries q
         JOIN public.query_sessions s ON s.id = q.session_id
        WHERE q.org_id = $1 AND s.title = 'What is metformin used for?'
        GROUP BY s.id`,
      [tenant.orgId],
    );
    expect(sessions).toEqual([{ turns: 2 }]);
  });

  test("patient identifiers are refused in chat and never stored", async ({ page }) => {
    await page.goto("/app/chat");
    await sendInChat(page, "Mr. Ravi Kumar, MRN 99887766, has a fever — what should he take?");
    await expect(page.getByTestId("chat-blocked")).toContainText(/leave out names/i);
    const leaked = await rows(
      "SELECT 1 FROM public.queries WHERE org_id = $1 AND raw_query ILIKE '%Ravi%'",
      [tenant.orgId],
    );
    expect(leaked).toHaveLength(0);
  });

  test("a worried parent gets warning signs first and is offered the fever check", async ({ page }) => {
    await page.goto("/app/chat");
    await sendInChat(page, "My child has a fever of 38.5 — what should I do?");
    const answer = page.getByTestId("chat-assistant").last();
    await expect(answer).toHaveAttribute("data-status", "done", { timeout: 60_000 });
    await expect(answer).toContainText(/Get emergency help now/);
    await expect(answer).toContainText(/112 or 108/);
    await answer.getByTestId("chat-suggest-check").click();
    await expect(page).toHaveURL(/\/app\/treatment\?complaint=fever/);
    await expect(page.getByTestId("profile-next")).toContainText("Continue: Fever");
  });

  test("the fever check gives a four-year-old the NHS paracetamol dose and holds back ibuprofen", async ({
    page,
  }) => {
    await page.goto("/app/treatment");
    await page.getByTestId("profile-age").fill("4");
    await page.getByTestId("profile-next").click();
    await page.getByTestId("complaint-fever").click();

    await expect(page.getByTestId("question-text")).toHaveText("Do any of these apply right now?");
    await page.getByTestId("option-none").check();
    await page.getByTestId("question-next").click();
    await page.getByTestId("option-39").click();
    await page.getByTestId("option-1_2").click();
    await page.getByTestId("option-none").check();
    await page.getByTestId("question-next").click();
    await page.getByTestId("option-none").check();
    await page.getByTestId("question-next").click();

    const assessment = page.getByTestId("assessment");
    await expect(assessment).toHaveAttribute("data-urgency", "self_care");
    const paracetamol = page.getByTestId("medicine-paracetamol");
    await expect(paracetamol).toHaveAttribute("data-suitable", "true");
    await expect(paracetamol.getByTestId("medicine-dose")).toHaveText(/^240 mg \(10 ml of 120 mg\/5 ml/);
    await expect(page.getByTestId("medicine-ibuprofen")).toHaveAttribute("data-suitable", "false");
    await expect(page.getByTestId("medicine-ibuprofen")).toContainText(/dengue/);
  });

  test("a single danger sign ends the check with emergency advice and no home remedies", async ({ page }) => {
    await page.goto("/app/treatment?complaint=headache");
    await page.getByTestId("profile-age").fill("45");
    await page.getByTestId("profile-next").click();
    await page.getByTestId("option-weakness").check();
    await page.getByTestId("question-next").click();
    const assessment = page.getByTestId("assessment");
    await expect(assessment).toHaveAttribute("data-urgency", "emergency");
    await expect(page.getByTestId("assessment-headline")).toHaveText(/Get emergency help now/);
    await expect(page.getByTestId("assessment-medicines")).toHaveCount(0);
  });

  test("prescribing support sends a structured case to the clinician assistant", async ({ page }) => {
    await page.goto("/app/treatment");
    await page.getByTestId("tab-prescribing").click();
    await page.getByTestId("rx-condition").fill("community-acquired pneumonia");
    await page.getByLabel("Age").last().fill("67 years");
    await page.getByTestId("rx-submit").click();
    const answer = page.getByTestId("prescribing-answer").getByTestId("chat-assistant");
    await expect(answer).toHaveAttribute("data-status", "done", { timeout: 90_000 });
    const stored = await rows<{ audience: string; kind: string }>(
      `SELECT q.audience, s.kind FROM public.queries q
         JOIN public.query_sessions s ON s.id = q.session_id
        WHERE q.org_id = $1 AND q.raw_query LIKE 'Prescribing support for community-acquired pneumonia%'`,
      [tenant.orgId],
    );
    expect(stored).toEqual([{ audience: "clinician", kind: "treatment" }]);
  });

  test("Learn lists MBBS and PG subjects and teaches a topic in its specialty", async ({ page }) => {
    await page.goto("/app/learn");
    await expect(page.getByTestId("specialty-anatomy")).toBeVisible();
    await expect(page.getByTestId("specialty-neurosurgery")).toBeVisible();
    await page.getByTestId("learn-filter").fill("cardio");
    await page.getByTestId("specialty-cardiology").click();
    await expect(page.getByTestId("specialty-name")).toHaveText("Cardiology");
    await page.getByTestId("learn-topic").first().click();
    await expect(page.getByTestId("chat-assistant").last()).toHaveAttribute("data-status", "done", {
      timeout: 90_000,
    });
    const stored = await rows<{ kind: string; audience: string }>(
      `SELECT s.kind, q.audience FROM public.queries q
         JOIN public.query_sessions s ON s.id = q.session_id
        WHERE q.org_id = $1 AND s.kind = 'learn'`,
      [tenant.orgId],
    );
    expect(stored).toEqual([{ kind: "learn", audience: "student" }]);
  });

  test("the floating assistant answers on any page", async ({ page }) => {
    await page.goto("/app/library");
    await page.getByTestId("assistant-launcher").click();
    const panel = page.getByTestId("assistant-panel");
    await expect(panel).toBeVisible();
    await panel.getByTestId("chat-input").fill("hello");
    await panel.getByTestId("chat-input").press("Enter");
    await expect(panel.getByTestId("chat-assistant").last()).toContainText("health assistant", {
      timeout: 60_000,
    });
  });
});

test.describe("the website, signed out", () => {
  test("the chatbot answers visitors and keeps nothing", async ({ page }) => {
    // Unique per run, so "not stored" is checked against this message alone
    // (the landing page's demo panel stores its own demo queries).
    const question = `What are the warning signs of dengue? (visitor ${Date.now()})`;
    await page.goto("/");
    await page.getByTestId("assistant-launcher").click();
    const panel = page.getByTestId("assistant-panel");
    await panel.getByTestId("chat-input").fill(question);
    await panel.getByTestId("chat-input").press("Enter");
    const answer = panel.getByTestId("chat-assistant").last();
    await expect(answer).toHaveAttribute("data-status", "done", { timeout: 90_000 });
    await expect(answer.getByRole("button", { name: /Open source \d+/ }).first()).toBeVisible();
    const stored = await rows("SELECT 1 FROM public.queries WHERE raw_query = $1", [question]);
    expect(stored).toHaveLength(0);
  });

  test("the landing page's hero decides how to draw itself, and its button opens the chatbot", async ({ page }) => {
    await page.goto("/");
    await expect(page.getByRole("heading", { level: 1 })).toContainText("traced to the evidence");

    // The 3D layer settles on the scene (a GPU) or the still illustration
    // (software rendering, as in a headless run) — never stays undecided.
    const layer = page.locator("[data-hero-3d]");
    await expect(layer).toHaveAttribute("data-hero-3d", /^(3d|still)$/);
    if ((await layer.getAttribute("data-hero-3d")) === "still") {
      await expect(page.getByTestId("hero-still")).toBeVisible();
    } else {
      await expect(layer.locator("canvas")).toBeVisible();
    }

    await page.getByTestId("home-ask").click();
    const panel = page.getByTestId("assistant-panel");
    await expect(panel).toBeVisible();
    await expect(panel.getByTestId("chat-input")).toBeVisible();
  });

  test("the symptom check works without an account", async ({ page }) => {
    await page.goto("/");
    await page.getByTestId("home-check").click();
    await expect(page).toHaveURL(/\/check$/);
    await page.getByTestId("profile-age").fill("30");
    await page.getByTestId("profile-next").click();
    await page.getByTestId("complaint-urinary").click();
    await page.getByTestId("option-none").check();
    await page.getByTestId("question-next").click();
    await expect(page.getByTestId("assessment")).toBeVisible();
    await expect(page.getByTestId("assessment-doctor-may")).toContainText(/nitrofurantoin/);
  });
});
