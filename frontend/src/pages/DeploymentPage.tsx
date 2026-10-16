import { ArrowLeft, Ban, RotateCcw } from "lucide-react";
import { type ReactNode, useEffect, useState } from "react";
import { Link, useParams } from "react-router";

import { Button } from "../components/Button";
import { ConfirmDialog } from "../components/ConfirmDialog";
import { ErrorState } from "../components/ErrorState";
import { LoadingState } from "../components/LoadingState";
import { StatusBadge } from "../components/StatusBadge";
import { useDeployActions } from "../hooks/useDeployActions";
import { useNow } from "../hooks/useNow";
import { formatDateTime, formatDuration, secondsSince, shortHash } from "../lib/format";
import { FAILURE_LABELS, RUNTIME_LABELS, deploymentStatusMeta } from "../lib/status";
import { ApiError } from "../services/api";
import { useDeployments } from "../stores/deployments";
import { useProjects } from "../stores/projects";
import { ACTIVE_STATUSES, type Deployment, type Project } from "../types/api";
import { DeploymentLogs } from "./project/LogsTab";

export function DeploymentPage() {
  const { deploymentId = "" } = useParams();
  const deployment = useDeployments((s) => s.details[deploymentId]);
  const fetchDetail = useDeployments((s) => s.fetchDetail);
  const project = useProjects((s) => (deployment ? s.byId[deployment.project_id] : undefined));
  const fetchProject = useProjects((s) => s.fetchOne);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    fetchDetail(deploymentId)
      .then((d) => fetchProject(d.project_id))
      .catch((err: unknown) =>
        setError(
          err instanceof ApiError && err.status === 404 ? "Deployment not found." : (err as Error).message,
        ),
      );
  }, [deploymentId, fetchDetail, fetchProject]);

  // Refresh details (image, health check, error) when the status changes via events.
  const status = deployment?.status;
  useEffect(() => {
    if (status && !ACTIVE_STATUSES.includes(status)) void fetchDetail(deploymentId).catch(() => undefined);
  }, [status, deploymentId, fetchDetail]);

  if (error) return <ErrorState message={error} />;
  if (!deployment || !project) return <LoadingState label="Loading deployment…" />;
  return <DeploymentDetail deployment={deployment} project={project} />;
}

function DeploymentDetail({ deployment, project }: { deployment: Deployment; project: Project }) {
  const actions = useDeployActions(project);
  const now = useNow(1000);
  const [confirmRollback, setConfirmRollback] = useState(false);
  const active = ACTIVE_STATUSES.includes(deployment.status);
  const elapsed = active
    ? secondsSince(deployment.started_at ?? deployment.created_at, now)
    : deployment.duration_seconds;
  const health = deployment.health_check;

  return (
    <div className="flex flex-col gap-6">
      <Link
        to={`/projects/${project.id}/deployments`}
        className="inline-flex w-fit items-center gap-1.5 text-sm text-muted hover:text-fg"
      >
        <ArrowLeft className="h-4 w-4" aria-hidden /> {project.name}
      </Link>

      <header className="flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
        <div>
          <div className="flex flex-wrap items-center gap-3">
            <h1 className="text-2xl font-semibold tracking-tight">Deployment #{deployment.number}</h1>
            <StatusBadge meta={deploymentStatusMeta(deployment.status)} />
          </div>
          <p className="mt-1 text-sm text-muted">
            {deployment.trigger === "rollback"
              ? "Rollback"
              : deployment.trigger === "upload"
                ? "ZIP upload"
                : "Deploy"}{" "}
            · {formatDateTime(deployment.created_at)}
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          {active && (
            <Button
              icon={<Ban className="h-4 w-4" />}
              onClick={() => void actions.cancel(deployment.id)}
              loading={actions.busy}
            >
              Cancel
            </Button>
          )}
          {deployment.can_rollback && (
            <Button
              variant="primary"
              icon={<RotateCcw className="h-4 w-4" />}
              onClick={() => setConfirmRollback(true)}
              disabled={Boolean(project.active_deployment)}
            >
              Roll back to #{deployment.number}
            </Button>
          )}
        </div>
      </header>

      {deployment.status === "FAILED" && (
        <div role="alert" className="rounded-xl border border-bad/30 bg-bad/10 px-4 py-3 text-sm">
          <p className="font-medium text-bad">
            {deployment.failure_reason ? FAILURE_LABELS[deployment.failure_reason] : "Failed"}
          </p>
          {deployment.error_message && (
            <p className="mt-1 break-words text-fg/80">{deployment.error_message}</p>
          )}
        </div>
      )}

      <dl className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <Stat label="Duration">{formatDuration(elapsed)}</Stat>
        <Stat label="Runtime">{deployment.runtime ? RUNTIME_LABELS[deployment.runtime] : "—"}</Stat>
        <Stat label="Port">{deployment.internal_port ?? "—"}</Stat>
        <Stat label="Health check">
          {health ? (health.ok ? `HTTP ${health.status_code} · ${health.duration_ms} ms` : "failed") : "—"}
        </Stat>
      </dl>

      <DeploymentLogs deploymentId={deployment.id} height="h-[50vh] min-h-64" />

      <section className="card divide-y divide-line text-sm">
        <Row label="Image">
          <code className="break-all font-mono text-xs">{deployment.image_name ?? "—"}</code>
          {deployment.meta.image_pruned === true && (
            <span className="ml-2 text-xs text-subtle">(removed by cleanup)</span>
          )}
        </Row>
        <Row label="Container">
          <code className="break-all font-mono text-xs">{deployment.container_name ?? "—"}</code>
        </Row>
        {deployment.commit_hash && (
          <Row label="Commit">
            <code className="font-mono text-xs">{shortHash(deployment.commit_hash)}</code>
            {deployment.branch && <span className="ml-2 text-muted">{deployment.branch}</span>}
          </Row>
        )}
        {typeof deployment.meta.start_command === "string" && (
          <Row label="Start command">
            <code className="break-all font-mono text-xs">{deployment.meta.start_command}</code>
          </Row>
        )}
        {typeof deployment.meta.runtime_detection === "string" && (
          <Row label="Detection">{deployment.meta.runtime_detection}</Row>
        )}
        {typeof deployment.meta.build_seconds === "number" && (
          <Row label="Build time">{formatDuration(deployment.meta.build_seconds)}</Row>
        )}
        {health && !health.ok && (
          <Row label="Health check">
            {health.attempts} attempts · {health.error}
          </Row>
        )}
        <Row label="Queued">{formatDateTime(deployment.created_at)}</Row>
        <Row label="Started">{formatDateTime(deployment.started_at)}</Row>
        <Row label="Live since">{formatDateTime(deployment.deployed_at)}</Row>
        <Row label="Finished">{formatDateTime(deployment.finished_at)}</Row>
        <Row label="Stopped">{formatDateTime(deployment.stopped_at)}</Row>
      </section>

      <ConfirmDialog
        open={confirmRollback}
        title={`Roll back to #${deployment.number}`}
        description="A new deployment will start from this deployment's image (no rebuild) with the project's current environment variables."
        confirmLabel="Roll back"
        onConfirm={async () => {
          await actions.rollback(deployment.id);
        }}
        onClose={() => setConfirmRollback(false)}
      />
    </div>
  );
}

function Stat({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="card px-4 py-3">
      <dt className="text-xs text-muted">{label}</dt>
      <dd className="mt-1 truncate text-sm font-medium tabular-nums">{children}</dd>
    </div>
  );
}

function Row({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="flex flex-col gap-1 px-4 py-3 sm:flex-row sm:items-center sm:gap-4">
      <span className="w-36 shrink-0 text-muted">{label}</span>
      <span className="min-w-0">{children}</span>
    </div>
  );
}
