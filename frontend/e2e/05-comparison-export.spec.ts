import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { expect, test } from "@playwright/test";

import { newTenant, signIn, type Tenant } from "./fixtures/auth";
import { one } from "./fixtures/db";
import { isPdf, pdfText } from "./fixtures/pdf";

/**
 * Gate 13: a comparison query renders a table, and the exported PDF is a real
 * file that carries the citations — checked by reading the bytes, not by
 * trusting that a download fired.
 */

let tenant: Tenant;

test.beforeAll(async () => {
  tenant = await newTenant("E2E Comparison");
});

test("a comparison query renders a table and exports a PDF with its citations", async ({ page }) => {
  await signIn(page, tenant);

  await page.getByRole("button", { name: "Compare" }).click();
  const entity = page.getByRole("textbox", { name: "Option to compare" });
  await entity.fill("metformin");
  await entity.press("Enter");
  await entity.fill("empagliflozin");
  await entity.press("Enter");

  const box = page.getByRole("textbox", { name: /clinical question/i });
  await box.fill("Compare these drugs for glycaemic control in type 2 diabetes.");
  await page.getByRole("button", { name: "Ask", exact: true }).click();

  const table = page.getByTestId("comparison-table");
  await expect(table).toBeVisible({ timeout: 120_000 });
  await expect(table).toContainText(/metformin/i);
  await expect(table).toContainText(/empagliflozin/i);

  // The invariant, not a count: every cell either carries citations or says
  // "insufficient evidence". A cell with prose and no source would be the
  // failure — an invented comparison — and how many cells fall each way
  // depends on the corpus, so asserting a count would make an improvement in
  // retrieval look like a regression.
  const cells = table.locator("tbody td:not(:first-child)");
  const cellCount = await cells.count();
  expect(cellCount).toBeGreaterThan(0);
  for (let i = 0; i < cellCount; i += 1) {
    const cell = cells.nth(i);
    const insufficient = (await cell.getAttribute("data-testid")) === "insufficient-cell";
    const citations = await cell.locator(".cite-chip").count();
    expect(
      insufficient || citations > 0,
      `cell ${i} has prose but no citation and is not marked insufficient`,
    ).toBe(true);
  }

  // The row behind it.
  const stored = await one<{ comparison_table: string | null }>(
    "SELECT comparison_table::text AS comparison_table FROM public.answers WHERE org_id = $1 ORDER BY created_at DESC LIMIT 1",
    [tenant.orgId],
  );
  expect(stored?.comparison_table, "the table is persisted, not only rendered").toBeTruthy();

  // Export, and read what came out.
  const downloadPromise = page.waitForEvent("download", { timeout: 60_000 });
  await page.getByRole("button", { name: "Export" }).click();
  const pdfItem = page.getByRole("menuitem", { name: /pdf/i }).first();
  if (await pdfItem.isVisible().catch(() => false)) await pdfItem.click();
  const download = await downloadPromise;
  // Kept as run evidence, and so the export can be opened by hand.
  const saved = resolve(process.cwd(), "e2e/.artifacts/comparison-export.pdf");
  await download.saveAs(saved);
  const bytes = readFileSync(saved);
  expect(isPdf(bytes), "the file really is a PDF").toBe(true);
  expect(bytes.length).toBeGreaterThan(2_000);

  // The export is read back from the bytes: the question, the verdict, the
  // table, and the sources it cited. Line breaking hyphenates words, so the
  // comparison ignores hyphens and spacing.
  const flatten = (value: string) => value.replace(/[\s-]+/g, "").toLowerCase();
  const text = flatten(pdfText(bytes));
  expect(text).toContain("clinicalcontext");
  expect(text).toContain(flatten("Compare these drugs for glycaemic control in type 2 diabetes."));
  expect(text).toContain("comparison");
  expect(text).toContain("confidence:");

  const citations = JSON.parse(
    (await one<{ citations: string }>(
      "SELECT citations::text AS citations FROM public.answers WHERE org_id = $1 ORDER BY created_at DESC LIMIT 1",
      [tenant.orgId],
    ))!.citations,
  ) as { marker: number; title: string | null }[];
  expect(citations.length).toBeGreaterThan(0);
  for (const citation of citations.slice(0, 3)) {
    const fragment = flatten(citation.title ?? "").slice(0, 25);
    if (fragment.length > 12) {
      expect(text, `citation [${citation.marker}] is in the PDF`).toContain(fragment);
    }
  }
});
