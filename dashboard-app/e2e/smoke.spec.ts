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

// Regression coverage for the facelift: same real proxy, isolated ledger.
test("brand, deep links and Back work without a request storm", async ({ page, request }) => {
  let detailRequests = 0;
  const errors: string[] = [];
  page.on("request", (r) => { if (r.url().includes("/api/runs/smoke-loop")) detailRequests++; });
  page.on("pageerror", (e) => errors.push(e.message));
  await page.goto("/app#/runs/smoke-loop");
  await expect.poll(() => page.locator(".brand img").evaluate((i: HTMLImageElement) => i.naturalWidth)).toBeGreaterThan(0);
  const favicon = await page.locator('link[rel="icon"]').getAttribute("href");
  expect((await request.get(favicon!)).ok()).toBeTruthy();
  await expect(page.getByRole("region", { name: "Recorded run timeline" })).toContainText("Stuck loop suspected");
  await page.getByRole("button", { name: /^Iteration 1:/ }).click();
  await expect(page).toHaveURL(/#\/sessions\/smoke-loop-s1$/);
  await page.goBack();
  await expect(page.locator(".run-title")).toContainText("Smoke loop");
  await page.getByRole("button", { name: "Settings", exact: true }).click();
  await page.getByRole("button", { name: "Loop Lens", exact: true }).click();
  await expect(page).toHaveURL(/#\/runs\/smoke-loop$/);
  // Observe a quiet window after route transitions: the previous release
  // fixed a render/fetch feedback loop that exhausted browser resources.
  const settled = detailRequests;
  await page.waitForTimeout(500);
  expect(detailRequests - settled).toBeLessThanOrEqual(4);
  expect(errors).toEqual([]);
});

test("session overview, all inspector tabs, Flow and Trace retain captured data", async ({ page }) => {
  await page.goto("/app#/sessions/smoke-loop-s3");
  await expect(page.locator(".session-metrics")).toContainText("$0.0036");
  await expect(page.locator(".cost-bar")).toHaveCount(3);
  await page.locator(".call-row").first().click();
  const inspector = page.locator(".call-body").first();
  await inspector.getByRole("tab", { name: "Tools", exact: true }).click();
  await expect(inspector.locator("pre").first()).toContainText("Bash");
  await inspector.getByRole("tab", { name: "Prompt", exact: true }).click();
  await expect(inspector).toContainText("You are the smoke-test worker.");
  await inspector.getByRole("tab", { name: "Raw", exact: true }).click();
  await expect(inspector).toContainText('"session_id": "smoke-loop-s3"');
  await inspector.getByRole("tab", { name: "Response", exact: true }).click();
  await expect(inspector).toContainText("No text: the model answered with tool calls.");
  for (const name of ["Flow", "Trace"]) {
    await page.getByRole("tab", { name, exact: true }).click();
    await expect(page.locator(".svg-scroll svg")).toBeVisible();
  }
  await page.getByRole("tab", { name: "Calls", exact: true }).click();
  await expect(page.locator(".call-card")).toHaveCount(3);
  await page.locator(".session-tools > summary").click();
  await expect(page.locator(".whatif")).toBeVisible();
});

test("unknown prices remain explicit in the new metrics and chart", async ({ page }) => {
  await page.route("**/session/smoke-loop-s1", async (route) => {
    const response = await route.fetch();
    const calls = await response.json();
    calls[0].cost_usd = null;
    await route.fulfill({ response, json: calls });
  });
  await page.goto("/app#/sessions/smoke-loop-s1");
  await expect(page.locator(".session-metrics")).toContainText("1 unpriced call excluded");
  await expect(page.locator(".session-metrics")).toContainText("$0.0012");
  await expect(page.locator(".cost-bar.unpriced")).toHaveAttribute("aria-label", /Unknown cost/);
  await expect(page.locator(".call-cost").filter({ hasText: "Unknown" })).toBeVisible();
});

test("ceiling validation, saving, blocking and allowing update the real ledger", async ({ page, request }) => {
  await page.goto("/app#/runs/old-batch");
  try {
    await page.getByRole("button", { name: "Set ceiling", exact: true }).click();
    await page.getByLabel("Run ceiling in US dollars").fill("-1");
    await page.getByRole("button", { name: "Save", exact: true }).click();
    await expect(page.locator(".ceiling-error")).toContainText("above zero");
    await page.getByLabel("Run ceiling in US dollars").fill("20");
    await page.getByRole("button", { name: "Save", exact: true }).click();
    await expect(page.locator(".metric").filter({ hasText: "Run ceiling" })).toContainText("$20.00");
    expect((await (await request.get("/api/runs/old-batch")).json()).budget_usd).toBe(20);
    await page.getByRole("button", { name: "⊘ block future calls", exact: true }).click();
    await page.getByRole("button", { name: "block future calls under this id", exact: true }).click();
    await expect(page.locator(".run-title .badge")).toHaveText("Calls blocked");
    await expect(page.locator(".sidebar .card.selected .badge.stopped")).toHaveText("Calls blocked");
    expect((await (await request.get("/api/runs/old-batch")).json()).status).toBe("stopped");
    await page.getByRole("button", { name: "allow calls again", exact: true }).click();
    await expect(page.locator(".run-title .badge")).toHaveText("Ended");
    await page.getByRole("button", { name: "Remove", exact: true }).click();
    await expect(page.locator(".metric").filter({ hasText: "Run ceiling" })).toContainText("No run ceiling");
  } finally {
    await request.delete("/api/runs/old-batch/stop");
    await request.put("/api/labels/run/old-batch", { data: { budget_usd: 0 } });
  }
});

test("renaming, pinning and filtering preserve raw run identity", async ({ page, request }) => {
  await page.goto("/app");
  const card = page.locator(".sidebar .card").filter({ hasText: "Smoke loop" });
  try {
    await card.locator(".card-edit").click();
    await card.getByPlaceholder("name this run…").fill("Facelift verification");
    await card.getByPlaceholder("project (optional)").fill("dashboard-check");
    await card.getByRole("button", { name: "Save", exact: true }).click();
    const renamed = page.locator(".sidebar .card").filter({ hasText: "Facelift verification" });
    await expect(renamed.locator(".card-id")).toHaveText("smoke-loop");
    await renamed.locator(".card-pin").click();
    await expect(renamed.locator(".card-pin")).toHaveClass(/on/);
    await page.locator(".project-filter").selectOption("dashboard-check");
    await expect(page.locator(".sidebar .card")).toHaveCount(1);
    await renamed.click();
    await expect(page.locator(".run-meta .session-id")).toContainText("smoke-loop");
  } finally {
    await request.put("/api/labels/run/smoke-loop", { data: { name: "Smoke loop", project: "", pinned: false } });
  }
});

test("search, cache, live activity and what-if tools remain usable", async ({ page }) => {
  await page.goto("/app#/sessions");
  await page.getByRole("textbox", { name: "Search captured calls" }).fill("smoke-loop step 1");
  await expect(page.locator(".main .section-title")).toContainText("search results");
  await expect(page.locator(".call-card").first()).toBeVisible();
  await page.goto("/app#/runs/smoke-loop");
  await page.getByRole("tab", { name: "Cache", exact: true }).click();
  await expect(page.locator(".cache-panel")).toBeVisible();
  await page.getByRole("tab", { name: "Activity", exact: true }).click();
  await expect(page.locator(".live-empty")).toBeVisible();
  await page.getByRole("button", { name: /What-if \/ Replay/ }).click();
  await page.getByRole("button", { name: "estimate", exact: true }).click();
  await expect(page.locator(".whatif-result")).toBeVisible();
});

test("light appearance persists across reload and navigation", async ({ page }) => {
  await page.goto("/app#/settings");
  await page.getByRole("radio", { name: "Light", exact: true }).click();
  await expect(page.locator("html")).toHaveAttribute("data-theme", "light");
  await page.reload();
  await expect(page.locator("html")).toHaveAttribute("data-theme", "light");
  await page.getByRole("button", { name: "Loop Lens", exact: true }).click();
  await expect(page.locator("html")).toHaveAttribute("data-theme", "light");
});

for (const width of [320, 390, 768]) {
  test(`mobile navigation, access panel and charts fit at ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height: 844 });
    await page.goto("/app#/runs/smoke-loop");
    await expect(page.locator(".run-title")).toBeVisible();
    await expect(page.getByRole("button", { name: "Dashboard access key" })).toBeInViewport();
    await page.getByRole("button", { name: "Dashboard access key" }).click();
    await expect(page.locator(".key-pop")).toBeInViewport();
    await page.locator(".key-pop").getByRole("button", { name: "Close", exact: true }).click();
    await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    await page.locator(".mobile-back").click();
    await expect(page.getByRole("heading", { name: "Run overview" })).toBeVisible();
    await page.getByRole("button", { name: "Sessions", exact: true }).click();
    await page.locator(".sidebar .card").filter({ hasText: "smoke-loop-s3" }).click();
    await expect(page.locator(".session-header-title")).toContainText("smoke-loop-s3");
    await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    await page.getByRole("tab", { name: "Calls", exact: true }).click();
    await page.locator(".call-row").first().click();
    await expect(page.getByRole("tab", { name: "Raw", exact: true })).toBeVisible();
    await page.locator(".mobile-back").click();
    await expect(page.locator(".sidebar")).toBeVisible();
  });
}
