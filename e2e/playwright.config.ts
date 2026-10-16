import { defineConfig, devices } from "@playwright/test";

// E2E_BASE_URL   where the platform runs (docker compose: http://localhost; dev: http://localhost:5173)
// E2E_CHANNEL    optional installed browser channel, e.g. "msedge" (CI uses bundled Chromium)
// E2E_DOCKER=1   the stack has a Docker daemon: run the full deploy → live URL → restart → stop flow
const baseURL = process.env.E2E_BASE_URL ?? "http://localhost";
const channel = process.env.E2E_CHANNEL || undefined;

export default defineConfig({
  testDir: "./tests",
  timeout: 60_000,
  expect: { timeout: 15_000 },
  fullyParallel: false,
  workers: 1,
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI ? [["list"], ["html", { open: "never" }]] : "list",
  use: {
    baseURL,
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
  projects: [
    {
      name: "desktop",
      use: { ...devices["Desktop Chrome"], channel, viewport: { width: 1440, height: 900 } },
      testIgnore: /responsive.spec.ts/,
    },
    { name: "mobile", use: { ...devices["Pixel 7"], channel }, testMatch: /responsive\.spec\.ts/ },
  ],
});
