import { expect, test } from "@playwright/test";

// A floor, not an empire: each test asserts that a whole surface
// renders real seeded data, nothing about pixels.

test("the app boots with its three tabs", async ({ page }) => {
  await page.goto("/app");
  await expect(page.getByRole("button", { name: "Loop Lens" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Sessions" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Reports" })).toBeVisible();
});

test("Loop Lens lists runs and opens one with its iterations", async ({ page }) => {
  await page.goto("/app");
  const tiles = page.locator(".sidebar .card");
  await expect(tiles.filter({ hasText: "Smoke loop" })).toBeVisible();
  await expect(tiles.filter({ hasText: "old-batch" })).toBeVisible();

  await tiles.filter({ hasText: "Smoke loop" }).click();
  await expect(page.locator(".main h2.page-title")).toContainText("Smoke loop");
  // #76: the raw id stays visible under the custom name.
  await expect(page.locator(".main .session-id")).toContainText("smoke-loop");
  await expect(page.locator(".main table.grid tbody tr")).toHaveCount(3);
  // The flagged iteration shows its flag, and a flag card explains it.
  await expect(page.locator(".main .badge.flagged").first()).toBeVisible();
  await expect(page.locator(".flag-card").first()).toBeVisible();
});

test("a blocked refusal renders amber in the iterations table", async ({ page }) => {
  await page.goto("/app");
  await page.locator(".sidebar .card").filter({ hasText: "old-batch" }).click();
  await expect(page.locator(".main table.grid .badge.blocked")).toContainText("1 blocked");
});

test("Sessions lists sessions and opens one with call cards", async ({ page }) => {
  await page.goto("/app");
  await page.getByRole("button", { name: "Sessions" }).click();
  const cards = page.locator(".sidebar .card");
  await expect(cards.first()).toBeVisible();
  await cards.filter({ hasText: "smoke-loop-s1" }).first().click();
  await expect(page.locator(".call-card").first()).toBeVisible();
  await expect(page.locator(".session-header-title")).toContainText("smoke-loop-s1");
});

test("Compare runs mounts with named A and B columns", async ({ page }) => {
  await page.goto("/app");
  const cmp = page.locator(".sidebar .card .card-cmp");
  await cmp.nth(0).click();
  await cmp.nth(1).click();
  await expect(page.getByRole("heading", { name: /Compare runs/ })).toBeVisible();
  await expect(page.locator("table.rtable th").nth(1)).toContainText("A ·");
  await expect(page.locator("table.rtable th").nth(2)).toContainText("B ·");
});

test("Reports renders its tiles", async ({ page }) => {
  await page.goto("/app");
  await page.getByRole("button", { name: "Reports" }).click();
  await expect(page.locator(".stats-row .stat").first()).toBeVisible();
});
