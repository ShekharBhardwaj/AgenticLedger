import { expect, test } from "@playwright/test";

// A floor, not an empire: each test asserts that a whole surface
// renders real seeded data, nothing about pixels.

test("the app boots with its three tabs", async ({ page }) => {
  await page.goto("/app");
  await expect(page.getByRole("button", { name: "Runs" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Traces" })).toBeVisible();
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

test("Traces lists traces and opens one with call cards", async ({ page }) => {
  await page.goto("/app");
  await page.getByRole("button", { name: "Traces" }).click();
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
  // The anatomy replays both runs side by side, from their recorded calls.
  await expect(page.locator(".anat-lane")).toHaveCount(2);
  await expect(page.locator(".anat-lane .anat-conv").first()).toBeVisible();
  await expect(page.locator(".anat-pos")).toContainText("call 1 of");
  await page.getByRole("button", { name: "step ›" }).click();
  await expect(page.locator(".anat-pos")).toContainText("call 2 of");
  // Every lane steps through its own calls, forwards and back together.
  const counters = page.locator(".anat-lane .anat-counters");
  await expect(counters.nth(0)).toContainText("call 2 /");
  await expect(counters.nth(1)).toContainText("call 2 /");
  await page.getByRole("button", { name: "‹ step" }).click();
  await expect(counters.nth(0)).toContainText("call 1 /");
  await expect(counters.nth(1)).toContainText("call 1 /");
});

test("a run's overview replays its anatomy", async ({ page }) => {
  await page.goto("/app");
  await page.locator(".sidebar .card").filter({ hasText: "Smoke loop" }).click();
  const lane = page.locator(".anat-lane");
  await expect(lane).toHaveCount(1);
  await expect(lane.locator(".anat-shape")).toContainText("3 conversations");
  await expect(lane.locator(".anat-caption .clause.did")).toContainText("calls Bash");
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
  // Every icon the page offers must download: the SVG, the PNG Safari
  // uses instead of it, and the phone's home-screen icon.
  const icons = await page.locator('link[rel="icon"], link[rel="apple-touch-icon"]')
    .evaluateAll((links) => links.map((l) => (l as HTMLLinkElement).getAttribute("href")));
  expect(icons.length).toBeGreaterThanOrEqual(3);
  for (const href of icons) expect((await request.get(href!)).ok()).toBeTruthy();
  await expect(page.getByRole("region", { name: "Recorded run timeline" })).toContainText("Stuck loop suspected");
  await page.getByRole("button", { name: /^Iteration 1:/ }).click();
  await expect(page).toHaveURL(/#\/traces\/smoke-loop-s1$/);
  await page.goBack();
  await expect(page.locator(".run-title")).toContainText("Smoke loop");
  await page.getByRole("button", { name: "Settings", exact: true }).click();
  await page.getByRole("button", { name: "Runs", exact: true }).click();
  await expect(page).toHaveURL(/#\/runs\/smoke-loop$/);
  // Observe a quiet window after route transitions: the previous release
  // fixed a render/fetch feedback loop that exhausted browser resources.
  const settled = detailRequests;
  await page.waitForTimeout(500);
  expect(detailRequests - settled).toBeLessThanOrEqual(4);
  expect(errors).toEqual([]);
});

test("session overview, all inspector tabs, Flow and Trace retain captured data", async ({ page }) => {
  await page.goto("/app#/traces/smoke-loop-s3");
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
  await page.getByRole("tab", { name: "Flow", exact: true }).click();
  await expect(page.locator(".svg-scroll svg")).toBeVisible();
  await page.getByRole("tab", { name: "Calls", exact: true }).click();
  await expect(page.locator(".call-card")).toHaveCount(3);
  await page.locator(".session-tools > summary").click();
  await expect(page.locator(".whatif")).toBeVisible();
});

test("the Timeline joins proxied calls and hook events into one tree", async ({ page }) => {
  await page.goto("/app#/traces/smoke-loop-s3");
  await page.getByRole("tab", { name: /^Timeline/ }).click();
  const rows = page.locator(".timeline-row");
  // the trace's own root row, one turn, three calls, one tool run, one
  // denied tool, one sub-agent
  await expect(rows).toHaveCount(8);
  await expect(page.locator(".timeline-summary")).toContainText("3 model calls, 2 tool runs, 1 sub-agent, 1 turn");
  // the trace anchors the tree and carries its totals; the turn nests
  // under it, and the hook's Bash run hangs off the call that issued
  // tu-5, by id
  await expect(rows.first()).toHaveClass(/kind-root/);
  await expect(rows.first()).toContainText("tok");
  await expect(rows.nth(1)).toHaveClass(/kind-turn/);
  const bash = rows.filter({ hasText: "Bash" }).filter({ has: page.locator(".g-tool") }).first();
  await expect(bash).not.toHaveClass(/by-time/);
  await bash.locator(".timeline-name").click();
  await expect(page.locator(".timeline-body")).toContainText("from hooks");
  await expect(page.locator(".activity-pre").first()).toContainText("date");
  // the denied run shows as denied; the sub-agent folds
  await expect(rows.filter({ hasText: "denied" })).toHaveCount(1);
  await expect(rows.filter({ has: page.locator(".g-agent") })).toHaveCount(1);
  await page.locator(".timeline-row.kind-turn .timeline-caret").click();
  await expect(rows).toHaveCount(2);                  // the root and the folded turn
  await page.locator(".timeline-row.kind-root .timeline-caret").click();
  await expect(rows).toHaveCount(1);                  // the root folds the whole trace
  // a trace the proxy never saw a call for still lists, and opens on its Timeline
  await page.goto("/app#/traces");
  const card = page.locator(".sidebar .card").filter({ hasText: "gateway-only-s1" });
  await expect(card).toContainText("3 events");
  await card.click();
  await expect(page.getByRole("tab", { name: /^Timeline/ })).toHaveAttribute("aria-selected", "true");
  await expect(page.locator(".timeline-row")).toHaveCount(2);   // the root and its one tool run
  // a trace with calls and no hooks still has a Timeline, of its calls
  await page.goto("/app#/traces/smoke-loop-s1");
  await expect(page.getByRole("tab", { name: "Calls", exact: true })).toBeVisible();
  await expect(page.getByRole("tab", { name: /^Timeline \(2\)/ })).toBeVisible();
});

test("unknown prices remain explicit in the new metrics and chart", async ({ page }) => {
  await page.route("**/session/smoke-loop-s1", async (route) => {
    const response = await route.fetch();
    const calls = await response.json();
    calls[0].cost_usd = null;
    await route.fulfill({ response, json: calls });
  });
  await page.goto("/app#/traces/smoke-loop-s1");
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
    await expect(page.locator(".metric").filter({ hasText: "Run ceiling" })).toContainText("none set");
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

test("an icon and a color mark a run on its card, its title and its breadcrumb", async ({ page, request }) => {
  await page.goto("/app");
  const card = page.locator(".sidebar .card").filter({ hasText: "Smoke loop" });
  try {
    await card.locator(".card-edit").click();
    await card.getByRole("button", { name: "Add icon", exact: true }).click();
    const picker = card.getByRole("dialog", { name: "Icon and color" });
    await picker.getByRole("radio", { name: "green", exact: true }).click();
    await expect(picker.getByRole("radio", { name: "green", exact: true })).toBeChecked();
    await picker.getByRole("button", { name: "flask", exact: true }).click();
    await expect(picker.getByRole("button", { name: "flask", exact: true })).toHaveAttribute("aria-pressed", "true");
    await picker.getByRole("button", { name: "Done", exact: true }).click();
    await expect(picker).toHaveCount(0);
    await card.getByRole("button", { name: "Save", exact: true }).click();
    // The mark is decorative (aria-hidden), so the data attributes are the
    // observable: which icon, which color, at each home of the name.
    const mark = '.label-mark[data-icon="flask"][data-color="green"]';
    await expect(card.locator(`.card-name ${mark}`)).toBeVisible();
    const row = await (await request.get("/api/runs/smoke-loop")).json();
    expect([row.icon, row.color]).toEqual(["flask", "green"]);
    await card.click();
    await expect(page.locator(`.run-title ${mark}`)).toBeVisible();
    await expect(page.locator(`.ledger-breadcrumb .breadcrumb-current ${mark}`)).toBeVisible();
  } finally {
    await request.put("/api/labels/run/smoke-loop", { data: { icon: "", color: "" } });
  }
});

test("search, cache, live activity and what-if tools remain usable", async ({ page }) => {
  await page.goto("/app#/traces");
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

test("the audit trail lists actions, filters, and verifies its chain", async ({ page }) => {
  // Viewing a session is an audited read, so the trail has a row to show.
  await page.goto("/app#/traces/smoke-loop-s1");
  await expect(page.locator(".session-header-title")).toContainText("smoke-loop-s1");
  await page.goto("/app#/settings");
  const trail = page.getByRole("region", { name: "Audit trail" });
  await expect(trail.locator("table.rtable tbody tr").first()).toBeVisible();
  await expect(trail).toContainText("view_session");
  await trail.getByRole("button", { name: "Verify chain", exact: true }).click();
  await expect(trail.getByRole("status")).toContainText("Chain intact");
  await trail.getByLabel("Filter by action").fill("view_session");
  await trail.getByRole("button", { name: "Apply filters", exact: true }).click();
  await expect(trail.locator("table.rtable tbody tr").first()).toContainText("view_session");
  await trail.getByLabel("Filter by action").fill("no_such_action");
  await trail.getByRole("button", { name: "Apply filters", exact: true }).click();
  await expect(trail).toContainText("No audit rows match");
});

test("stop all calls engages from Loop Lens, banners every page, and lifts", async ({ page, request }) => {
  await page.goto("/app#/runs");
  try {
    await page.getByRole("button", { name: "Stop all calls", exact: true }).click();
    await page.getByRole("button", { name: "stop every agent's calls now", exact: true }).click();
    await expect(page.getByRole("alert")).toContainText("All calls are stopped");
    expect((await (await request.get("/health")).json()).calls_stopped).toBe(true);
    // The stop is state, not a page: it follows the operator everywhere.
    await page.goto("/app#/traces");
    await expect(page.getByRole("alert")).toContainText("All calls are stopped");
    await expect(page.getByRole("button", { name: "Stop all calls", exact: true })).toHaveCount(0);
    await page.getByRole("alert").getByRole("button", { name: "allow calls again", exact: true }).click();
    await expect(page.getByRole("alert")).toHaveCount(0);
    expect((await (await request.get("/health")).json()).calls_stopped).toBe(false);
  } finally {
    await request.delete("/api/stop");
  }
});

test("the notifications panel says plainly that no webhook is configured", async ({ page }) => {
  await page.goto("/app#/settings");
  const panel = page.getByRole("region", { name: "Notifications" });
  await expect(panel).toContainText("No webhook configured");
  await expect(panel.getByRole("button", { name: "Send a test notification", exact: true })).toBeDisabled();
  await expect(panel).toContainText("Nothing sent yet");
});

test("a call link reopens the session with that call expanded, and the lists know their totals", async ({ page, request }) => {
  const calls = await (await request.get("/session/smoke-loop-s1")).json();
  const target = calls[calls.length - 1].action_id;
  await page.goto(`/app#/traces/smoke-loop-s1/calls/${encodeURIComponent(target)}`);
  await expect(page.locator(".session-header-title")).toContainText("smoke-loop-s1");
  const card = page.locator(`#call-${target}`);
  await expect(card).toHaveClass(/focused/);
  await expect(card.locator(".call-row")).toHaveAttribute("aria-expanded", "true");
  // Collapsing it takes the call out of the address; the session stays.
  await card.locator(".call-row").click();
  await expect(page).toHaveURL(/#\/traces\/smoke-loop-s1$/);
  // Paged lists: the seeded ledger fits in one page, so nothing older is offered.
  const runs = await request.get("/api/runs?limit=1");
  expect(runs.headers()["x-total-count"]).toBe("2");
  expect(runs.headers()["x-next-offset"]).toBe("1");
  await page.goto("/app#/runs");
  await expect(page.locator(".sidebar .sidebar-count")).toHaveText("2");
  await expect(page.getByRole("button", { name: /Load older/ })).toHaveCount(0);
});

test("a setting changed in the dashboard lands in the config file and asks for a restart", async ({ page, request }) => {
  await page.goto("/app#/settings");
  try {
    await page.getByRole("button", { name: "Change circuit breaker", exact: true }).click();
    await page.getByLabel("New value for circuit breaker").selectOption("block");
    await page.getByRole("button", { name: "Save", exact: true }).click();
    await expect(page.locator(".restart-banner")).toContainText("Restart the ledger to apply");
    await expect(page.locator(".setting-pending")).toContainText("block on restart");
    const cfg = await (await request.get("/api/config")).json();
    expect(cfg.values["loops.action"]).toBe("block");
    expect(cfg.restart_required).toBe(true);
    // A secret is written but never shown back.
    await request.put("/api/config", { data: { key: "keys.api_key", value: "smoke-secret" } });
    const after = await (await request.get("/api/config")).json();
    expect(after.values["keys.api_key"]).toBe("set (hidden)");
  } finally {
    await request.put("/api/config", { data: { key: "loops.action", value: null } });
    await request.put("/api/config", { data: { key: "keys.api_key", value: null } });
  }
});

test("light appearance persists across reload and navigation", async ({ page }) => {
  await page.goto("/app#/settings");
  await page.getByRole("radio", { name: "Light", exact: true }).click();
  await expect(page.locator("html")).toHaveAttribute("data-theme", "light");
  await page.reload();
  await expect(page.locator("html")).toHaveAttribute("data-theme", "light");
  await page.getByRole("button", { name: "Runs", exact: true }).click();
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
    await page.getByRole("button", { name: "Traces", exact: true }).click();
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
