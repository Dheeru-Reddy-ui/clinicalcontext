import { expect, test, type Page } from "@playwright/test";

import { newTenant, signIn } from "./fixtures/auth";

/**
 * Nothing scrolls sideways — on a small phone or a laptop.
 *
 * A page wider than its screen is the most common way a layout breaks on a
 * phone: one long button label that does not wrap (the landing page's demo
 * questions did) and the whole page slides sideways under the reader's
 * thumb. This walks the public pages and the app at a 360-pixel phone and a
 * 1280-pixel laptop and names the element that sticks out when one does.
 */

const SIZES = [
  { name: "phone", width: 360, height: 740 },
  { name: "laptop", width: 1280, height: 720 },
] as const;

async function sticksOut(page: Page): Promise<{ overflow: number; culprits: string[] }> {
  return page.evaluate(() => {
    const width = document.documentElement.clientWidth;
    const clipped = (el: Element) => {
      for (let p = el.parentElement; p && p !== document.body; p = p.parentElement) {
        if (/(auto|scroll|hidden|clip)/.test(getComputedStyle(p).overflowX)) return true;
      }
      return false;
    };
    const culprits: string[] = [];
    for (const el of Array.from(document.body.querySelectorAll("*"))) {
      const box = el.getBoundingClientRect();
      if (!box.width || box.right <= width + 1 || getComputedStyle(el).position === "fixed" || clipped(el)) continue;
      culprits.push(`<${el.tagName.toLowerCase()}> "${(el.textContent ?? "").trim().slice(0, 50)}" right=${Math.round(box.right)}`);
      if (culprits.length >= 5) break;
    }
    return { overflow: document.documentElement.scrollWidth - width, culprits };
  });
}

async function checkRoutes(page: Page, routes: string[]): Promise<void> {
  for (const size of SIZES) {
    await page.setViewportSize({ width: size.width, height: size.height });
    for (const route of routes) {
      await page.goto(route);
      await page.waitForLoadState("networkidle", { timeout: 15_000 }).catch(() => undefined);
      const { overflow, culprits } = await sticksOut(page);
      expect(overflow, `${route} on a ${size.name} scrolls sideways by ${overflow}px: ${culprits.join("; ")}`).toBeLessThanOrEqual(1);
    }
  }
}

test("the public pages fit a phone and a laptop", async ({ page }) => {
  test.setTimeout(240_000);
  await checkRoutes(page, ["/", "/check", "/login", "/signup", "/methodology", "/health"]);
});

test("the app fits a phone and a laptop", async ({ page }) => {
  test.setTimeout(360_000);
  const tenant = await newTenant("E2E Responsive");
  await signIn(page, tenant);
  await checkRoutes(page, [
    "/app",
    "/app/chat",
    "/app/treatment",
    "/app/learn",
    "/app/sessions",
    "/app/history",
    "/app/library",
    "/app/binders",
    "/app/dashboard",
    "/app/notifications",
    "/app/settings",
    "/app/settings?section=profile",
    "/app/settings?section=voice",
    "/app/settings?section=learn",
    "/app/settings?section=members",
    "/app/settings?section=api",
  ]);
});
