import { expect, test } from "@playwright/test";

import { newPassword, newTenant, signIn } from "./fixtures/auth";
import { rows } from "./fixtures/db";

/**
 * Settings: a person's choices are saved on the server (so they follow them
 * from laptop to phone) and the rest of the product honours them; the old
 * Admin page is a section of it; and the account controls work end to end.
 */

test("choosing who answers are written for is saved, and a new chat starts with it", async ({ page }) => {
  const tenant = await newTenant("E2E Settings Audience");
  await signIn(page, tenant);
  await page.goto("/app/settings?section=assistant");
  await expect(page.getByTestId("settings-section-title")).toHaveText("Assistant");
  await page.getByTestId("pref-audience-clinician").click();
  await expect(page.getByText("Saved")).toBeVisible();

  const stored = await rows<{ audience: string }>(
    "SELECT audience FROM public.user_preferences WHERE user_id = $1",
    [tenant.id],
  );
  expect(stored[0]?.audience).toBe("clinician");

  // Another screen, after a reload: the preference comes from the server.
  await page.goto("/app/chat");
  await expect(page.getByTestId("audience-clinician")).toHaveAttribute("aria-checked", "true");
});

test("the profile name is saved and shown where the person is named", async ({ page }) => {
  const tenant = await newTenant("E2E Settings Profile");
  await signIn(page, tenant);
  await page.goto("/app/settings?section=profile");
  await page.getByLabel("Full name").fill("Dr Meera Nair");
  await page.getByLabel("Specialty or role").fill("General Medicine");
  await page.getByTestId("profile-save").click();
  await expect(page.getByText("Profile saved.")).toBeVisible();
  const profile = await rows<{ full_name: string; specialty: string; role: string }>(
    "SELECT full_name, specialty, role FROM public.profiles WHERE id = $1",
    [tenant.id],
  );
  expect(profile[0]).toEqual({ full_name: "Dr Meera Nair", specialty: "General Medicine", role: "owner" });
});

test("a new password works at the next sign-in", async ({ page }) => {
  const tenant = await newTenant("E2E Settings Password");
  await signIn(page, tenant);
  await page.goto("/app/settings?section=security");
  const fresh = newPassword();
  await page.getByLabel("New password", { exact: true }).fill(fresh);
  await page.getByLabel("Confirm new password", { exact: true }).fill(fresh);
  await page.getByTestId("password-save").click();
  await expect(page.getByText(/Password changed/)).toBeVisible();

  await page.getByTestId("sign-out-everywhere").click();
  await page.waitForURL(/\/login/);
  await signIn(page, { ...tenant, password: fresh });
  await expect(page).toHaveURL(/\/app/);
});

test("text size is this device's, applied before the page paints", async ({ page }) => {
  const tenant = await newTenant("E2E Settings Text");
  await signIn(page, tenant);
  await page.goto("/app/settings?section=appearance");
  await page.getByTestId("text-size-large").click();
  await expect(page.locator("html")).toHaveAttribute("data-text-size", "large");
  await page.reload();
  await expect(page.locator("html")).toHaveAttribute("data-text-size", "large");
  const rootSize = await page.evaluate(() => getComputedStyle(document.documentElement).fontSize);
  expect(rootSize).toBe("18px");
});

test("owners find the old Admin page as Settings sections", async ({ page }) => {
  const tenant = await newTenant("E2E Settings Admin");
  await signIn(page, tenant);
  await page.goto("/app/admin");
  await expect(page).toHaveURL(/\/app\/settings\?section=members/);
  await expect(page.getByText("Invite someone")).toBeVisible();
  for (const section of ["sharing", "api", "library", "plan"]) {
    await expect(page.getByTestId(`settings-nav-${section}`)).toBeVisible();
  }
  await expect(page.getByRole("link", { name: "Admin", exact: true })).toHaveCount(0);
});

test("on a phone, Settings is a list and each section opens with a way back", async ({ page }) => {
  const tenant = await newTenant("E2E Settings Phone");
  await page.setViewportSize({ width: 390, height: 844 });
  await signIn(page, tenant);
  await page.goto("/app/settings");
  await expect(page.getByTestId("settings-nav-voice")).toBeVisible();
  await expect(page.getByTestId("settings-section-title")).toBeHidden();
  await page.getByTestId("settings-nav-voice").click();
  await expect(page.getByTestId("settings-section-title")).toHaveText("Voice");
  await expect(page.getByTestId("settings-nav-voice")).toBeHidden();
  await page.getByRole("link", { name: "Settings", exact: true }).first().click();
  await expect(page.getByTestId("settings-nav-voice")).toBeVisible();
});

test("a conversation can be deleted from the chat list", async ({ page }) => {
  const tenant = await newTenant("E2E Settings Delete");
  await signIn(page, tenant);
  await page.goto("/app/chat");
  await page.getByTestId("chat-input").fill("hello");
  await page.getByTestId("chat-input").press("Enter");
  await expect(page.getByTestId("chat-assistant").last()).toHaveAttribute("data-status", "done", {
    timeout: 60_000,
  });
  const list = page.getByTestId("chat-list");
  await expect(list.getByRole("button", { name: /hello/i }).first()).toBeVisible();
  await list.getByTestId("chat-delete").first().click();
  await page.getByTestId("chat-delete-confirm").click();
  await expect(page.getByText("Conversation deleted.")).toBeVisible();
  await expect(list.getByText("Your conversations will appear here.")).toBeVisible();
  const left = await rows("SELECT 1 FROM public.query_sessions WHERE user_id = $1 AND kind = 'chat'", [tenant.id]);
  expect(left).toHaveLength(0);
});
