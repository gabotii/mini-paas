import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import { describe, expect, it } from "vitest";

import { DeploymentCard } from "../components/DeploymentCard";
import { LogViewer } from "../components/LogViewer";
import { StatusBadge } from "../components/StatusBadge";
import { deploymentStatusMeta } from "../lib/status";
import type { DeploymentSummary, LogLine } from "../types/api";

const lines: LogLine[] = [
  {
    id: 1,
    deployment_id: "d",
    timestamp: "2026-10-05T12:30:01Z",
    level: "INFO",
    source: "system",
    message: "$ deployment started",
  },
  {
    id: 2,
    deployment_id: "d",
    timestamp: "2026-10-05T12:30:02Z",
    level: "INFO",
    source: "build",
    message: "Python detected",
  },
  {
    id: 3,
    deployment_id: "d",
    timestamp: "2026-10-05T12:30:03Z",
    level: "DEBUG",
    source: "deploy",
    message: "Health check attempt 1",
  },
  {
    id: 4,
    deployment_id: "d",
    timestamp: "2026-10-05T12:30:04Z",
    level: "ERROR",
    source: "runtime",
    message: "Traceback: boom",
  },
];

describe("StatusBadge", () => {
  it("renders label and pulse for in-progress states", () => {
    render(<StatusBadge meta={deploymentStatusMeta("BUILDING")} />);
    const badge = screen.getByTestId("status-badge");
    expect(badge).toHaveTextContent("Building");
    expect(badge.querySelector(".animate-pulse-dot")).not.toBeNull();
  });
});

describe("LogViewer", () => {
  it("filters by source and search, hides debug lines by default", async () => {
    const user = userEvent.setup();
    render(<LogViewer lines={lines} connection="open" />);
    const log = screen.getByRole("log");
    expect(log).toHaveTextContent("Python detected");
    expect(log).not.toHaveTextContent("Health check attempt 1");
    expect(screen.getByText("Live")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Show debug lines" }));
    expect(log).toHaveTextContent("Health check attempt 1");

    await user.click(screen.getByRole("button", { name: "runtime" }));
    expect(log).toHaveTextContent("Traceback: boom");
    expect(log).not.toHaveTextContent("Python detected");

    await user.click(screen.getByRole("button", { name: "all" }));
    await user.type(screen.getByLabelText("Search logs"), "python");
    expect(log).toHaveTextContent("Python detected");
    expect(log).not.toHaveTextContent("Traceback");
  });

  it("toggles timestamps and auto-scroll", async () => {
    const user = userEvent.setup();
    render(<LogViewer lines={lines} />);
    expect(screen.getByRole("log").textContent).toMatch(/\[\d\d:\d\d:\d\d\]/);
    await user.click(screen.getByRole("button", { name: "Hide timestamps" }));
    expect(screen.getByRole("log").textContent).not.toMatch(/\[\d\d:\d\d:\d\d\]/);
    await user.click(screen.getByRole("button", { name: "Pause auto-scroll" }));
    expect(screen.getByRole("button", { name: "Resume auto-scroll" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Jump to latest/ })).toBeInTheDocument();
  });

  it("shows an empty state", () => {
    render(<LogViewer lines={[]} emptyText="Waiting for logs…" />);
    expect(screen.getByRole("log")).toHaveTextContent("Waiting for logs…");
  });
});

describe("DeploymentCard", () => {
  it("shows number, status, failure reason and links to the detail", () => {
    const deployment: DeploymentSummary = {
      id: "d11",
      project_id: "p1",
      number: 11,
      status: "FAILED",
      trigger: "deploy",
      runtime: "python",
      commit_hash: "abcdef123456",
      failure_reason: "HEALTH_CHECK_FAILED",
      succeeded: false,
      duration_seconds: 45,
      created_at: new Date().toISOString(),
      started_at: null,
      deployed_at: null,
      finished_at: null,
      stopped_at: null,
    };
    render(
      <MemoryRouter>
        <DeploymentCard deployment={deployment} />
      </MemoryRouter>,
    );
    const row = screen.getByTestId("deployment-row");
    expect(row).toHaveAttribute("href", "/deployments/d11");
    expect(row).toHaveTextContent("#11");
    expect(row).toHaveTextContent("Failed");
    expect(row).toHaveTextContent("Health check failed");
    expect(row).toHaveTextContent("45s");
    expect(row).toHaveTextContent("abcdef1");
  });
});
