import { expect, test } from "@playwright/test";

import { json } from "./fixtures/api";
import { accessToken, createAccount, newEmail, newPassword, signIn, tokenFromPage } from "./fixtures/auth";
import { one, rows } from "./fixtures/db";

/**
 * Gates 1 and 2 of the Phase 14 E2E list: a person can get from nothing to a
 * working organisation, and an owner can bring a colleague into it with the
 * role they were given.
 */

test("sign up, create an organisation, and land in the app", async ({ page }) => {
  const email = newEmail("signup");
  const password = newPassword();

  await page.goto("/signup");
  await page.getByLabel("Full name").fill("Dr E2E Tester");
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password").fill(password);
  await page.getByRole("button", { name: "Sign up" }).click();

  // A new account belongs to no organisation, so onboarding comes first.
  await page.waitForURL(/\/onboarding/, { timeout: 30_000 });
  const orgName = `E2E Cardiology ${Date.now().toString().slice(-6)}`;
  await page.getByLabel("Organization name").fill(orgName);
  await page.getByRole("button", { name: "Create organization" }).click();

  await page.waitForURL(/\/app(\/|$)/, { timeout: 30_000 });
  await expect(page.getByRole("textbox", { name: /clinical question/i })).toBeVisible();

  // The rows behind the UI: a real org, and the signer-up as its owner.
  const profile = await one<{ org_id: string; role: string; org_name: string }>(
    `SELECT p.org_id, p.role::text AS role, o.name AS org_name
     FROM public.profiles p JOIN public.organizations o ON o.id = p.org_id
     JOIN auth.users u ON u.id = p.id WHERE u.email = $1`,
    [email],
  );
  expect(profile, "the new user has a profile").not.toBeNull();
  expect(profile!.role).toBe("owner");
  expect(profile!.org_name).toBe(orgName);
});

test("an owner invites a clinician who accepts and joins the same org", async ({ page }) => {
  // The owner, provisioned through the API — this spec is about the invite.
  const owner = await createAccount();
  const ownerToken = await accessToken(owner);
  const created = await json<{ org: { id: string; name: string } }>("/api/v1/orgs", {
    token: ownerToken,
    body: { name: `E2E Invites ${Date.now().toString().slice(-6)}` },
  });
  const orgId = created.org.id;

  const colleagueEmail = newEmail("clinician");
  const invite = await json<{ token: string; role: string; email: string }>("/api/v1/orgs/invites", {
    token: await accessToken(owner),
    body: { email: colleagueEmail, role: "clinician" },
  });
  expect(invite.role).toBe("clinician");

  // The colleague signs up and joins with the token, through the UI.
  const colleague = await createAccount(colleagueEmail);
  await signIn(page, colleague, "/onboarding");
  await page.getByLabel("Invite token").fill(invite.token);
  await page.getByRole("button", { name: "Join organization" }).click();
  // The membership row is the thing that matters; the redirect follows it.
  // Under a full-suite load the API can take longer than the default wait,
  // and a redirect that is merely slow is not a failed join.
  await expect
    .poll(
      async () =>
        (
          await rows("SELECT id FROM public.profiles WHERE org_id = $1", [orgId])
        ).length,
      { timeout: 60_000 },
    )
    .toBe(2);
  await page.waitForURL(/\/app(\/|$)/, { timeout: 60_000 });

  // Both users, one org, the roles they were given.
  const members = await rows<{ email: string; role: string }>(
    `SELECT u.email, p.role::text AS role FROM public.profiles p
     JOIN auth.users u ON u.id = p.id WHERE p.org_id = $1 ORDER BY p.created_at`,
    [orgId],
  );
  expect(members.map((m) => [m.email, m.role])).toEqual([
    [owner.email, "owner"],
    [colleagueEmail, "clinician"],
  ]);

  // And the app agrees, from the colleague's own session.
  const colleagueToken = await tokenFromPage(page);
  const me = await json<{ org: { id: string }; role: string }>("/api/v1/orgs/me", { token: colleagueToken });
  expect(me.org.id).toBe(orgId);
  expect(me.role).toBe("clinician");
});
