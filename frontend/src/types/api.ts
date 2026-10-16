// Mirrors backend/app/schemas. Keep in sync with the OpenAPI document at /api/docs.

export type Runtime = "auto" | "python" | "node" | "static" | "dotnet" | "dockerfile";
export type SourceType = "zip" | "git";
export type ProjectStatus = "NEW" | "RUNNING" | "STOPPED" | "FAILED" | "CRASHED" | "DELETING";
export type DeploymentStatus =
  "QUEUED" | "BUILDING" | "DEPLOYING" | "HEALTH_CHECKING" | "RUNNING" | "STOPPED" | "FAILED" | "CANCELLED";
export type FailureReason =
  "BUILD_FAILED" | "DEPLOY_FAILED" | "HEALTH_CHECK_FAILED" | "RUNTIME_CRASHED" | "TIMEOUT" | "INTERNAL_ERROR";
export type DeploymentTrigger = "deploy" | "upload" | "rollback";
export type LogLevel = "DEBUG" | "INFO" | "WARNING" | "ERROR";
export type LogSource = "build" | "deploy" | "runtime" | "system";

export const ACTIVE_STATUSES: DeploymentStatus[] = ["QUEUED", "BUILDING", "DEPLOYING", "HEALTH_CHECKING"];

export interface User {
  id: string;
  email: string;
  name: string;
  created_at: string;
}

export interface TokenResponse {
  access_token: string;
  token_type: string;
  expires_in: number;
  user: User;
}

export interface DeploymentSummary {
  id: string;
  project_id: string;
  number: number;
  status: DeploymentStatus;
  trigger: DeploymentTrigger;
  runtime: Runtime | null;
  commit_hash: string | null;
  failure_reason: FailureReason | null;
  succeeded: boolean;
  duration_seconds: number | null;
  created_at: string;
  started_at: string | null;
  deployed_at: string | null;
  finished_at: string | null;
  stopped_at: string | null;
}

export interface HealthCheckResult {
  ok: boolean;
  url: string;
  attempts: number;
  duration_ms: number;
  status_code: number | null;
  error: string | null;
}

export interface Deployment extends DeploymentSummary {
  source_type: SourceType;
  repository_url: string | null;
  branch: string | null;
  image_name: string | null;
  container_name: string | null;
  internal_port: number | null;
  rollback_of_id: string | null;
  error_message: string | null;
  health_check: HealthCheckResult | null;
  meta: Record<string, unknown>;
  can_rollback: boolean;
}

export interface MetricSnapshot {
  timestamp: string;
  cpu_usage: number;
  memory_usage: number;
  memory_limit: number;
  network_rx: number;
  network_tx: number;
  uptime_seconds: number;
  restart_count: number;
}

export interface Project {
  id: string;
  name: string;
  slug: string;
  description: string | null;
  runtime: Runtime;
  source_type: SourceType;
  repository_url: string | null;
  repository_branch: string | null;
  has_source: boolean;
  status: ProjectStatus;
  url: string;
  port: number | null;
  start_command: string | null;
  health_check_path: string;
  cpu_limit: number;
  memory_limit_mb: number;
  current_deployment: DeploymentSummary | null;
  active_deployment: DeploymentSummary | null;
  latest_metrics: MetricSnapshot | null;
  created_at: string;
  updated_at: string;
}

export interface PlatformLimits {
  max_projects: number;
  max_upload_mb: number;
  cpu_max: number;
  memory_max_mb: number;
  cpu_default: number;
  memory_default_mb: number;
}

export interface ProjectList {
  items: Project[];
  limits: PlatformLimits;
}

export interface ProjectCreate {
  name: string;
  slug?: string;
  description?: string;
  runtime: Runtime;
  source_type: SourceType;
  repository_url?: string;
  repository_branch?: string;
  port?: number | null;
  start_command?: string | null;
  health_check_path?: string;
  cpu_limit?: number;
  memory_limit_mb?: number;
}

export type ProjectUpdate = Partial<Omit<ProjectCreate, "slug" | "source_type">>;

export interface DeploymentPage {
  items: DeploymentSummary[];
  total: number;
  limit: number;
  offset: number;
}

export interface LogLine {
  id: number;
  deployment_id: string;
  timestamp: string;
  level: LogLevel;
  source: LogSource;
  message: string;
}

export interface LogPage {
  deployment_id: string | null;
  items: LogLine[];
  next_after_id: number | null;
}

export interface MetricsSummary {
  cpu_usage: number;
  memory_usage: number;
  memory_limit: number;
  network_rx: number;
  network_tx: number;
  uptime_seconds: number;
  restart_count: number;
}

export interface MetricsResponse {
  project_id: string;
  window_minutes: number;
  current: MetricsSummary | null;
  samples: MetricSnapshot[];
}

export interface EnvVar {
  key: string;
  value: string;
  is_secret: boolean;
  created_at: string;
  updated_at: string;
}

export interface AuditEntry {
  id: number;
  action: string;
  resource_type: string | null;
  resource_id: string | null;
  ip: string | null;
  details: Record<string, unknown>;
  created_at: string;
}

export interface ApiErrorBody {
  error: { code: string; message: string; details: Record<string, unknown> };
}

// Real-time events (WebSocket /ws/events)
export type DeploymentEvent = DeploymentSummary & {
  project_status: ProjectStatus;
  error_message: string | null;
};
export interface ProjectEvent {
  id: string;
  status: ProjectStatus;
  current_deployment_id: string | null;
}
export interface MetricEvent extends MetricSnapshot {
  project_id: string;
  deployment_id: string | null;
}

export type ServerEvent =
  | { type: "hello"; data: { user_id: string } }
  | { type: "ping"; data: Record<string, never> }
  | { type: "deployment.updated"; data: DeploymentEvent }
  | { type: "project.updated"; data: ProjectEvent }
  | { type: "metrics.sample"; data: MetricEvent };
