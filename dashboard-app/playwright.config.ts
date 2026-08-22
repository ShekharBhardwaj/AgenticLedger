import { defineConfig } from "@playwright/test";

// Dashboard smoke floor (#98): boots the REAL proxy on a seeded scratch
// ledger and drives the built SPA headlessly. Minimal by design: the
// app opens, the tabs render, a run and a session open, compare mounts.
const PY = process.env.SMOKE_PYTHON ?? "python3";
const DB = process.env.SMOKE_DB ?? "/tmp/agenticledger-smoke.db";
const PORT = 8099;

export default defineConfig({
  testDir: "./e2e",
  timeout: 30_000,
  retries: process.env.CI ? 1 : 0,
  use: { baseURL: `http://127.0.0.1:${PORT}`, headless: true },
  webServer: {
    command:
      `cd .. && ${PY} scripts/seed_smoke_ledger.py ${DB} && ` +
      `AGENTICLEDGER_PORT=${PORT} AGENTICLEDGER_DSN=sqlite:///${DB} ` +
      `AGENTICLEDGER_LOOP_ACTION=warn ${PY} -m agenticledger.proxy`,
    url: `http://127.0.0.1:${PORT}/health`,
    reuseExistingServer: false,
    timeout: 60_000,
  },
});
