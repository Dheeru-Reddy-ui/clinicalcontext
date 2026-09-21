import { expect, test } from "@playwright/test";

import { newTenant, signIn, type Tenant } from "./fixtures/auth";

/**
 * Gate 19: ⌘K to the Library and into a document, on the keyboard alone.
 *
 * "Keyboard only" is asserted, not assumed: the page counts every trusted
 * mouse event from the moment the journey starts, and the count has to be
 * zero at the end. A clinician on a ward keyboard, or anyone using a screen
 * reader, has to be able to get anywhere without a pointer.
 */

let tenant: Tenant;

test.beforeAll(async () => {
  tenant = await newTenant("E2E Keyboard");
});

test("⌘K → Library → open a document, without a single mouse event", async ({ page }) => {
  await signIn(page, tenant);

  // Count real pointer input from here on. Note that activating a link or
  // button with Enter dispatches a *trusted* click with `detail === 0`, so a
  // click alone does not mean a mouse was used — pointer events do.
  await page.evaluate(() => {
    const counter = { pointer: 0, mouseClicks: 0 };
    (window as unknown as { __mouse: typeof counter }).__mouse = counter;
    for (const type of ["mousedown", "mouseup", "mousemove", "pointerdown", "pointerup"]) {
      document.addEventListener(type, (event) => {
        if (event.isTrusted) counter.pointer += 1;
      }, true);
    }
    document.addEventListener("click", (event) => {
      if (event.isTrusted && (event as MouseEvent).detail > 0) counter.mouseClicks += 1;
    }, true);
  });

  // The shortcut is registered by an effect, so wait until the page is
  // interactive (the composer takes focus) before reaching for it, and allow
  // a retry: a keystroke sent during hydration lands nowhere.
  await expect(page.getByRole("textbox", { name: /clinical question/i })).toBeFocused();
  const palette = page.getByRole("dialog", { name: /command palette/i });
  await expect
    .poll(
      async () => {
        if (await palette.isVisible().catch(() => false)) return true;
        await page.keyboard.press("ControlOrMeta+k");
        return palette.isVisible().catch(() => false);
      },
      { timeout: 30_000, intervals: [500, 1_000, 2_000] },
    )
    .toBe(true);

  // Type, arrow to the Library entry, and enter.
  await page.keyboard.type("library");
  const libraryOption = page.getByRole("option", { name: /library/i }).first();
  await expect(libraryOption).toBeVisible();
  await page.keyboard.press("Enter");

  await page.waitForURL(/\/app\/library/, { timeout: 30_000 });
  await expect(page.getByRole("heading", { name: /library/i }).first()).toBeVisible();

  // Tab to the first document link and open it with the keyboard.
  const firstDocument = page.locator('a[href^="/app/library/"]').first();
  await expect(firstDocument).toBeVisible({ timeout: 30_000 });
  const href = await firstDocument.getAttribute("href");
  await firstDocument.focus();
  await expect(firstDocument).toBeFocused();
  await page.keyboard.press("Enter");

  await page.waitForURL(new RegExp(href!.replace(/[/]/g, "\\/")), { timeout: 30_000 });
  // The document opened: its passages are on screen.
  await expect(page.getByRole("heading").first()).toBeVisible();

  const mouse = await page.evaluate(
    () => (window as unknown as { __mouse: { pointer: number; mouseClicks: number } }).__mouse,
  );
  expect(mouse.pointer, "no pointer input during the journey").toBe(0);
  expect(mouse.mouseClicks, "no mouse-driven clicks either").toBe(0);
});

test("the composer is ready for typing, and a skip link leads to the content", async ({ page }) => {
  await signIn(page, tenant);

  // Landing on Ask puts the caret in the question box: a clinician can type
  // the moment the page appears.
  await expect(page.getByRole("textbox", { name: /clinical question/i })).toBeFocused();

  // And a skip link is reachable near the top of the tab order, pointing at
  // the main landmark. (In development Next.js injects its own dev-tools
  // button ahead of the page, so allow a few stops rather than exactly one.)
  // Blurring alone is not enough: the browser keeps its sequential-focus
  // starting point, so Tab would continue from the composer. Focusing the
  // document body moves that starting point back to the top.
  await page.evaluate(() => {
    (document.activeElement as HTMLElement | null)?.blur();
    document.body.setAttribute("tabindex", "-1");
    document.body.focus();
  });
  const skip = page.getByRole("link", { name: /skip to content/i });
  let focused = false;
  for (let i = 0; i < 5 && !focused; i += 1) {
    await page.keyboard.press("Tab");
    focused = await skip.evaluate((el) => el === document.activeElement);
  }
  expect(focused, "the skip link is reachable by tabbing from the top").toBe(true);
  expect(await skip.getAttribute("href")).toBe("#main");
  await page.keyboard.press("Enter");
  await expect(page.locator("#main")).toBeVisible();
});
