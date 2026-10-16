import { type Page, expect, test } from "@playwright/test";

import { exampleZip, register } from "./helpers";

async function expectNoHorizontalOverflow(page: Page) {
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
  expect(overflow).toBeLessThanOrEqual(0);
}

test("pages fit narrow screens and navigation uses a drawer", async ({ page }) => {
  await page.goto("/login");
  await expectNoHorizontalOverflow(page);

  await register(page);
  await expectNoHorizontalOverflow(page);

  // The sidebar is collapsed into a drawer on mobile.
  await page.getByRole("button", { name: "Open navigation" }).click();
  const drawer = page.getByRole("dialog", { name: "Navigation" });
  await expect(drawer).toBeVisible();
  await drawer.getByRole("link", { name: "New project" }).click();
  await expect(drawer).toBeHidden();
  await expectNoHorizontalOverflow(page);

  await page.getByLabel("Project name").fill("Mobile App");
  await page.getByTestId("zip-input").setInputFiles({
    name: "site.zip",
    mimeType: "application/zip",
    buffer: exampleZip("static"),
  });
  await page.getByRole("button", { name: "Create & deploy" }).click();
  await expect(page.getByTestId("log-viewer")).toContainText("$ deployment started");

  for (const tab of ["Overview", "Deployments", "Logs", "Environment", "Metrics", "Settings"]) {
    await page.getByRole("link", { name: tab, exact: true }).click();
    await expectNoHorizontalOverflow(page);
  }
});
