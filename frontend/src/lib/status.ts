import type { DeploymentStatus, FailureReason, Project, ProjectStatus, Runtime } from "../types/api";

export type Tone = "green" | "yellow" | "blue" | "red" | "gray" | "violet";

export interface StatusMeta {
  label: string;
  tone: Tone;
  pulse: boolean;
}

const DEPLOYMENT: Record<DeploymentStatus, StatusMeta> = {
  QUEUED: { label: "Queued", tone: "gray", pulse: true },
  BUILDING: { label: "Building", tone: "yellow", pulse: true },
  DEPLOYING: { label: "Deploying", tone: "blue", pulse: true },
  HEALTH_CHECKING: { label: "Health check", tone: "blue", pulse: true },
  RUNNING: { label: "Running", tone: "green", pulse: false },
  STOPPED: { label: "Stopped", tone: "gray", pulse: false },
  FAILED: { label: "Failed", tone: "red", pulse: false },
  CANCELLED: { label: "Cancelled", tone: "gray", pulse: false },
};

const PROJECT: Record<ProjectStatus, StatusMeta> = {
  NEW: { label: "Not deployed", tone: "gray", pulse: false },
  RUNNING: { label: "Running", tone: "green", pulse: false },
  STOPPED: { label: "Stopped", tone: "gray", pulse: false },
  FAILED: { label: "Failed", tone: "red", pulse: false },
  CRASHED: { label: "Crashed", tone: "red", pulse: false },
  DELETING: { label: "Deleting", tone: "gray", pulse: true },
};

export const FAILURE_LABELS: Record<FailureReason, string> = {
  BUILD_FAILED: "Build failed",
  DEPLOY_FAILED: "Deploy failed",
  HEALTH_CHECK_FAILED: "Health check failed",
  RUNTIME_CRASHED: "Crashed",
  TIMEOUT: "Timed out",
  INTERNAL_ERROR: "Platform error",
};

export const RUNTIME_LABELS: Record<Runtime, string> = {
  auto: "Auto-detect",
  python: "Python",
  node: "Node.js",
  static: "Static",
  dotnet: ".NET",
  dockerfile: "Dockerfile",
};

/** Order in which runtimes are offered in forms. */
export const RUNTIMES: Runtime[] = ["auto", "python", "node", "dotnet", "static", "dockerfile"];

export function deploymentStatusMeta(status: DeploymentStatus): StatusMeta {
  return DEPLOYMENT[status];
}

export function projectStatusMeta(status: ProjectStatus): StatusMeta {
  return PROJECT[status];
}

/** What a project card should show: an in-progress deployment wins over the steady state. */
export function effectiveProjectStatus(project: Project): StatusMeta {
  if (project.active_deployment) return DEPLOYMENT[project.active_deployment.status];
  return PROJECT[project.status];
}
