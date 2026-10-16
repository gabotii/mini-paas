import { beforeEach, describe, expect, it } from "vitest";

import { dispatchServerEvent } from "../stores/events";
import { useDeployments } from "../stores/deployments";
import { useProjects } from "../stores/projects";
import type { DeploymentEvent, Project } from "../types/api";

const project: Project = {
  id: "p1",
  name: "My API",
  slug: "my-api",
  description: null,
  runtime: "auto",
  source_type: "zip",
  repository_url: null,
  repository_branch: null,
  has_source: true,
  status: "RUNNING",
  url: "http://my-api.localhost",
  port: null,
  start_command: null,
  health_check_path: "/",
  cpu_limit: 0.5,
  memory_limit_mb: 256,
  current_deployment: null,
  active_deployment: null,
  latest_metrics: null,
  created_at: "2026-01-01T00:00:00Z",
  updated_at: "2026-01-01T00:00:00Z",
};

function event(
  status: DeploymentEvent["status"],
  projectStatus: Project["status"] = "RUNNING",
): DeploymentEvent {
  return {
    id: "d2",
    project_id: "p1",
    number: 2,
    status,
    trigger: "deploy",
    runtime: "python",
    commit_hash: null,
    failure_reason: status === "FAILED" ? "BUILD_FAILED" : null,
    succeeded: status === "RUNNING",
    duration_seconds: null,
    created_at: "2026-01-01T00:00:00Z",
    started_at: null,
    deployed_at: null,
    finished_at: null,
    stopped_at: null,
    project_status: projectStatus,
    error_message: status === "FAILED" ? "pip failed" : null,
  };
}

describe("real-time events", () => {
  beforeEach(() => {
    useProjects.getState().reset();
    useDeployments.getState().reset();
    useProjects.getState().upsert(project);
  });

  it("tracks an in-progress deployment and clears it when it goes live", () => {
    dispatchServerEvent({ type: "deployment.updated", data: event("BUILDING") });
    expect(useProjects.getState().byId.p1!.active_deployment?.status).toBe("BUILDING");

    dispatchServerEvent({ type: "deployment.updated", data: event("RUNNING") });
    const updated = useProjects.getState().byId.p1!;
    expect(updated.active_deployment).toBeNull();
    expect(updated.current_deployment?.id).toBe("d2");
  });

  it("keeps the serving deployment when a new one fails", () => {
    useProjects
      .getState()
      .upsert({ ...project, current_deployment: { ...event("RUNNING"), id: "d1", number: 1 } });
    dispatchServerEvent({ type: "deployment.updated", data: event("BUILDING") });
    dispatchServerEvent({ type: "deployment.updated", data: event("FAILED", "RUNNING") });
    const updated = useProjects.getState().byId.p1!;
    expect(updated.status).toBe("RUNNING");
    expect(updated.current_deployment?.id).toBe("d1");
    expect(updated.active_deployment).toBeNull();
  });

  it("updates history entries in place and adds unknown ones", async () => {
    useDeployments.setState({ history: { p1: { items: [], total: 0, loading: false } } });
    dispatchServerEvent({ type: "deployment.updated", data: event("QUEUED") });
    dispatchServerEvent({ type: "deployment.updated", data: event("BUILDING") });
    const history = useDeployments.getState().history.p1!;
    expect(history.total).toBe(1);
    expect(history.items[0]!.status).toBe("BUILDING");
  });

  it("applies metric samples and project status changes", () => {
    dispatchServerEvent({
      type: "metrics.sample",
      data: {
        project_id: "p1",
        deployment_id: "d1",
        timestamp: "2026-01-01T00:00:10Z",
        cpu_usage: 12,
        memory_usage: 100,
        memory_limit: 200,
        network_rx: 1,
        network_tx: 2,
        uptime_seconds: 10,
        restart_count: 0,
      },
    });
    expect(useProjects.getState().byId.p1!.latest_metrics?.cpu_usage).toBe(12);
    dispatchServerEvent({
      type: "project.updated",
      data: { id: "p1", status: "STOPPED", current_deployment_id: null },
    });
    expect(useProjects.getState().byId.p1!.status).toBe("STOPPED");
    expect(useProjects.getState().byId.p1!.latest_metrics).toBeNull();
  });
});
