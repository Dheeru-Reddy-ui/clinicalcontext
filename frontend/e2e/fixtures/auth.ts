import { randomBytes, randomUUID } from "node:crypto";

import { expect, type Page } from "@playwright/test";

import { rows } from "./db";
import { env } from "./env";

/**
 * Accounts for the E2E run.
 *
 * Users are created through the local Supabase admin API with a throwaway
 * password generated per run — these are fixtures on a developer's machine,
 * never real credentials, and nothing is written to disk. Where a spec is
 * *about* the UI flow (signing up, accepting an invite) it drives the forms;
 * everywhere else it provisions through the API, because a test should set up
 * through the shortest honest path and assert on the thing it is testing.
 */

export interface Account {
  id: string;
  email: string;
  password: string;
  fullName: string;
}

export interface Tenant extends Account {
  orgId: string;
  orgName: string;
  token: string;
}

export function newEmail(prefix = "e2e"): string {
  return `${prefix}-${randomUUID().slice(0, 8)}@cc-e2e.org`;
}

export function newPassword(): string {
  // Throwaway, single-run, local-only. Long enough for Supabase's policy.
  return `Pw-${randomBytes(12).toString("base64url")}`;
}

export async function createAccount(
  email = newEmail(),
  password = newPassword(),
  fullName = `Dr ${email.split("@")[0]}`,
): Promise<Account> {
  const response = await fetch(`${env.supabaseUrl}/auth/v1/admin/users`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      apikey: env.supabaseServiceKey,
      Authorization: `Bearer ${env.supabaseServiceKey}`,
    },
    // full_name rides in user_metadata exactly as the sign-up form puts it
    // there, so the profile carries a name to attribute annotations to.
    body: JSON.stringify({ email, password, email_confirm: true, user_metadata: { full_name: fullName } }),
  });
  if (!response.ok) {
    throw new Error(`createAccount failed (${response.status}): ${await response.text()}`);
  }
  const user = (await response.json()) as { id: string };
  return { id: user.id, email, password, fullName };
}

export async function accessToken(account: Account): Promise<string> {
  const response = await fetch(`${env.supabaseUrl}/auth/v1/token?grant_type=password`, {
    method: "POST",
    headers: { "Content-Type": "application/json", apikey: env.supabaseAnonKey },
    body: JSON.stringify({ email: account.email, password: account.password }),
  });
  if (!response.ok) {
    throw new Error(`accessToken failed (${response.status}): ${await response.text()}`);
  }
  const body = (await response.json()) as { access_token: string };
  return body.access_token;
}

/**
 * A user with a fresh organisation, ready to ask questions.
 *
 * The plan defaults to `pro` because the specs ask many questions in a row and
 * the free tier's 20 requests/minute would make unrelated tests fail as rate
 * limiting — which is its own spec (12), on a free-plan tenant.
 */
export async function newTenant(
  orgName = `E2E ${randomUUID().slice(0, 6)}`,
  plan: "free" | "pro" | "enterprise" = "pro",
): Promise<Tenant> {
  const account = await createAccount();
  const token = await accessToken(account);
  const response = await fetch(`${env.apiUrl}/api/v1/orgs`, {
    method: "POST",
    headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}` },
    body: JSON.stringify({ name: orgName }),
  });
  if (!response.ok) {
    throw new Error(`create org failed (${response.status}): ${await response.text()}`);
  }
  const me = (await response.json()) as { org: { id: string } };
  if (plan !== "free") {
    await rows("UPDATE public.organizations SET plan = $2 WHERE id = $1", [me.org.id, plan]);
  }
  // The org id lands in app_metadata, so the token has to be re-minted.
  const fresh = await accessToken(account);
  return { ...account, orgId: me.org.id, orgName, token: fresh };
}

/** Sign in through the real login form and land in the app. */
export async function signIn(page: Page, account: Account, expectPath = "/app"): Promise<void> {
  await page.goto("/login");
  await page.getByLabel("Email").fill(account.email);
  await page.getByLabel("Password").fill(account.password);
  await page.getByRole("button", { name: /sign in/i }).click();
  await page.waitForURL((url) => url.pathname.startsWith(expectPath), { timeout: 30_000 });
}

/** The access token the signed-in browser is holding, for API-level assertions. */
export async function tokenFromPage(page: Page): Promise<string> {
  const cookies = await page.context().cookies();
  const chunks = cookies
    .filter((c) => /^sb-.*-auth-token(\.\d+)?$/.test(c.name))
    .sort((a, b) => a.name.localeCompare(b.name, undefined, { numeric: true }))
    .map((c) => c.value)
    .join("");
  const raw = chunks.startsWith("base64-")
    ? Buffer.from(chunks.slice("base64-".length), "base64").toString("utf8")
    : decodeURIComponent(chunks);
  const session = JSON.parse(raw) as { access_token: string };
  expect(session.access_token, "the browser is holding a session").toBeTruthy();
  return session.access_token;
}
