import { expect, test } from "@playwright/test";

import { createAccount, newEmail, newPassword } from "./fixtures/auth";

/**
 * Account recovery, found missing on the first real deployment: a duplicate
 * sign-up must say so, a forgotten password must be recoverable through the
 * email that gets sent, and a dead link must explain itself.
 *
 * The reset flow is driven through the actual email: the local Supabase
 * stack delivers to Mailpit, whose API hands back the message. Against a
 * deployment there is no mail catcher, so that test skips itself there.
 */

const MAILPIT = process.env.E2E_MAILPIT_URL ?? "http://127.0.0.1:54324";

async function mailpitReachable(): Promise<boolean> {
  try {
    const response = await fetch(`${MAILPIT}/api/v1/messages?limit=1`);
    return response.ok;
  } catch {
    return false;
  }
}

/** The first link in the newest email to `address`, once it exists. */
async function latestLinkTo(address: string, subject: RegExp): Promise<string> {
  for (let attempt = 0; attempt < 20; attempt += 1) {
    const list = (await (await fetch(`${MAILPIT}/api/v1/messages?limit=10`)).json()) as {
      messages: { ID: string; Subject: string; To: { Address: string }[] }[];
    };
    const hit = list.messages.find((m) => m.To.some((t) => t.Address === address) && subject.test(m.Subject));
    if (hit) {
      const message = (await (await fetch(`${MAILPIT}/api/v1/message/${hit.ID}`)).json()) as {
        Text?: string;
        HTML?: string;
      };
      const body = message.Text || (message.HTML ?? "").replace(/<[^>]+>/g, " ");
      const link = /https?:\/\/[^\s"<>]+/.exec(body)?.[0];
      if (link) return link.replace(/&amp;/g, "&");
    }
    await new Promise((r) => setTimeout(r, 500));
  }
  throw new Error(`no email matching ${subject} reached ${address}`);
}

/** How many messages the catcher holds right now. */
async function mailCount(): Promise<number> {
  const response = await fetch(`${MAILPIT}/api/v1/messages?limit=1`);
  return ((await response.json()) as { total: number }).total;
}

test("signing up sends no email at all, and lands in the app", async ({ page }) => {
  test.skip(!(await mailpitReachable()), "needs the local mail catcher");
  // The deployment's first sign-up died on "Error sending confirmation
  // email": Supabase's built-in mailer refuses every address outside the
  // project team. Sign-up must therefore not need email at all.
  const before = await mailCount();
  const email = newEmail("no-mail");
  const password = newPassword();

  await page.goto("/signup");
  await page.getByLabel("Full name").fill("Dr No Mail");
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password", { exact: true }).fill(password);
  await page.getByRole("button", { name: "Sign up" }).click();

  // Straight to onboarding — no "check your email" step in between.
  await page.waitForURL(/\/onboarding/, { timeout: 60_000 });
  expect(await mailCount(), "sign-up must not send an email").toBe(before);

  // And the account is usable immediately: sign out, sign back in.
  await page.context().clearCookies();
  await page.goto("/login");
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password", { exact: true }).fill(password);
  await page.getByRole("button", { name: /^sign in$/i }).click();
  await page.waitForURL(/\/(onboarding|app)(\/|$)/, { timeout: 30_000 });
});

test("signing up with an existing email says so, and offers the way in", async ({ page }) => {
  const account = await createAccount();

  await page.goto("/signup");
  await page.getByLabel("Full name").fill("Someone Again");
  await page.getByLabel("Email").fill(account.email);
  await page.getByLabel("Password", { exact: true }).fill(newPassword());
  await page.getByRole("button", { name: "Sign up" }).click();

  const alert = page.getByTestId("already-registered");
  await expect(alert).toContainText(/already exists/i);
  await expect(alert.getByRole("link", { name: "Sign in" })).toHaveAttribute(
    "href",
    `/login?email=${encodeURIComponent(account.email)}`,
  );
  await expect(alert.getByRole("link", { name: "Reset password" })).toHaveAttribute(
    "href",
    `/forgot-password?email=${encodeURIComponent(account.email)}`,
  );
});

test("a forgotten password is recovered through the emailed link, and the old one stops working", async ({
  page,
}) => {
  test.skip(!(await mailpitReachable()), "needs the local mail catcher");
  const account = await createAccount();
  const replacement = newPassword();

  await page.goto(`/forgot-password?email=${encodeURIComponent(account.email)}`);
  await expect(page.getByLabel("Email")).toHaveValue(account.email);
  await page.getByRole("button", { name: "Send reset link" }).click();
  await expect(page.getByRole("status")).toContainText(/reset email is on its way/i);
  // The code path is offered alongside the link.
  await expect(page.getByLabel(/6-digit code/i)).toBeVisible();

  // The email, and the link in it — followed in the same browser, as the
  // PKCE flow requires.
  const link = await latestLinkTo(account.email, /reset/i);
  await page.goto(link);
  await page.waitForURL(/\/reset-password/, { timeout: 30_000 });
  await page.getByLabel("New password", { exact: true }).fill(replacement);
  await page.getByLabel("Confirm new password", { exact: true }).fill(replacement);
  await page.getByRole("button", { name: "Save new password" }).click();
  // A reset link's session is a real session: straight into the app.
  await page.waitForURL(/\/app(\/|$)/, { timeout: 30_000 });

  // Out, then back in: the old password is dead, the new one works.
  await page.context().clearCookies();
  await page.goto("/login");
  await page.getByLabel("Email").fill(account.email);
  await page.getByLabel("Password", { exact: true }).fill(account.password);
  await page.getByRole("button", { name: /^sign in$/i }).click();
  await expect(page.getByText(/invalid login credentials/i)).toBeVisible();
  await page.getByLabel("Password", { exact: true }).fill(replacement);
  await page.getByRole("button", { name: /^sign in$/i }).click();
  await page.waitForURL(/\/app(\/|$)/, { timeout: 30_000 });
});

test("a reset page without a reset session, and a dead link, both explain themselves", async ({ page }) => {
  await page.goto("/reset-password");
  await page.waitForURL(/\/login\?error=password_reset_expired/);
  await expect(page.getByText(/expired or was already used/i)).toBeVisible();

  await page.goto("/auth/callback?code=not-a-real-code&next=/app");
  await page.waitForURL(/\/login\?error=/);
  await expect(page.locator(".text-destructive")).not.toBeEmpty();
});
