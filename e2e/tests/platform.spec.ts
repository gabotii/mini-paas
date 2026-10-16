import { expect, test } from "@playwright/test";

import { DOCKER, PASSWORD, exampleZip, register, uniqueEmail } from "./helpers";

test.describe("authentication", () => {
  test("register, logout, protected routes, login", async ({ page }) => {
    const email = await register(page);
    await expect(page.getByText("Deploy your first app")).toBeVisible();

    await page.getByRole("button", { name: "Log out" }).first().click();
    await expect(page).toHaveURL(/\/login$/);

    await page.goto("/projects/new");
    await expect(page).toHaveURL(/\/login$/);

    await page.getByLabel("Email").fill(email);
    await page.getByLabel("Password").fill("wrong-password");
    await page.getByRole("button", { name: "Log in" }).click();
    await expect(page.getByRole("alert")).toContainText("Invalid email or password");

    await page.getByLabel("Password").fill(PASSWORD);
    await page.getByRole("button", { name: "Log in" }).click();
    await expect(page).toHaveURL(/\/projects\/new$/); // back where we were going

    // The session survives a reload (access token restored from the refresh cookie).
    await page.reload();
    await expect(page.getByRole("heading", { name: "New project" })).toBeVisible();
  });

  test("weak passwords are rejected client side", async ({ page }) => {
    await page.goto("/register");
    await page.getByLabel("Name").fill("Weak");
    await page.getByLabel("Email").fill(uniqueEmail());
    await page.getByLabel("Password").fill("short");
    await page.getByRole("button", { name: "Create account" }).click();
    await expect(page.getByText("At least 10 characters")).toBeVisible();
  });
});

test("create project, deploy, logs, history, environment, lifecycle", async ({ page, context }) => {
  test.setTimeout(DOCKER ? 600_000 : 120_000);
  await register(page);
  const slug = `e2e-${Date.now().toString(36)}`;
  const appUrl = `http://${slug}.localhost/`;

  // --- create + upload -----------------------------------------------------------------
  await page.getByRole("link", { name: "New Project", exact: true }).click();
  await page.getByLabel("Project name").fill("E2E Static Site");
  await page.getByLabel("Subdomain (optional)").fill(slug);
  await page.getByTestId("zip-input").setInputFiles({
    name: "site.zip",
    mimeType: "application/zip",
    buffer: exampleZip("static"),
  });
  await page.getByRole("button", { name: "Create & deploy" }).click();

  // --- live logs -------------------------------------------------------------------------
  await expect(page).toHaveURL(/\/logs$/);
  const logs = page.getByTestId("log-viewer");
  await expect(logs).toContainText("$ deployment started");
  await expect(logs).toContainText("Runtime: static (index.html found)");
  await expect(page.getByTestId("project-name")).toHaveText("E2E Static Site");

  if (!DOCKER) {
    // Without a Docker daemon the pipeline must still end cleanly with a clear reason.
    await expect(logs).toContainText("Docker", { timeout: 60_000 });
    await expect(page.getByTestId("status-badge").first()).toContainText(/Failed/);
  } else {
    await expect(logs).toContainText("Deployment successful.", { timeout: 480_000 });
    await expect(page.getByTestId("status-badge").first()).toHaveText("Running");

    // The app answers on its own hostname through Traefik (routes are polled every 2s).
    await expect.poll(async () => (await context.request.get(appUrl)).status(), { timeout: 20_000 }).toBe(200);
    expect(await (await context.request.get(appUrl)).text()).toContain("Hello from a static site");
  }

  // --- history + detail ------------------------------------------------------------------
  await page.getByRole("link", { name: "Deployments" }).click();
  const row = page.getByTestId("deployment-row").first();
  await expect(row).toContainText("#1");
  await row.click();
  await expect(page.getByRole("heading", { name: "Deployment #1" })).toBeVisible();
  await expect(page.getByTestId("log-viewer")).toContainText("Detecting runtime...");
  await page.getByRole("main").getByRole("link", { name: "E2E Static Site" }).click();

  // --- environment variables -------------------------------------------------------------
  await page.getByRole("link", { name: "Environment" }).click();
  await page.getByRole("button", { name: "Add variable" }).click();
  await page.getByRole("textbox", { name: "Key" }).fill("api_key");
  await page.getByRole("textbox", { name: "Value" }).fill("s3cr3t-value");
  await page.getByRole("button", { name: "Save" }).click();
  const value = page.getByTestId("env-value-API_KEY");
  await expect(value).toHaveText("••••••••");
  await page.getByRole("button", { name: "Show API_KEY" }).click();
  await expect(value).toHaveText("s3cr3t-value");
  await page.getByRole("button", { name: "Hide API_KEY" }).click();
  await expect(value).toHaveText("••••••••");
  await page.getByRole("button", { name: "Delete API_KEY" }).click();
  await page.getByRole("dialog").getByRole("button", { name: "Delete" }).click();
  await expect(page.getByText("No environment variables")).toBeVisible();

  if (DOCKER) {
    // --- metrics, restart, stop, start ---------------------------------------------------
    await page.getByRole("link", { name: "Metrics" }).click();
    await expect(page.getByTestId("metric-cpu")).not.toHaveText("—", { timeout: 60_000 });

    await page.getByRole("button", { name: "Restart" }).click();
    await expect(page.getByText("Restarting application")).toBeVisible();

    await page.getByRole("button", { name: "Stop" }).click();
    await page.getByRole("dialog").getByRole("button", { name: "Stop" }).click();
    await expect(page.getByTestId("status-badge").first()).toHaveText("Stopped", { timeout: 60_000 });
    await expect.poll(async () => (await context.request.get(appUrl)).status(), { timeout: 20_000 }).toBe(503);

    await page.getByRole("button", { name: "Start" }).click();
    await expect(page.getByTestId("status-badge").first()).toHaveText("Running", { timeout: 180_000 });
    await expect.poll(async () => (await context.request.get(appUrl)).status(), { timeout: 20_000 }).toBe(200);
  }

  // --- settings + delete -----------------------------------------------------------------
  await page.getByRole("link", { name: "Settings" }).click();
  await page.getByRole("button", { name: "Delete project" }).click();
  const dialog = page.getByRole("dialog");
  await dialog.getByRole("textbox").fill(slug);
  await dialog.getByRole("button", { name: "Delete forever" }).click();
  await expect(page.getByRole("heading", { name: "Dashboard" })).toBeVisible();
  await expect(page.getByText("E2E Static Site")).toHaveCount(0);
});
