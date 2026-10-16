import { useEffect } from "react";
import { Link } from "react-router";

import { EmptyState } from "../../components/ErrorState";
import { LogViewer } from "../../components/LogViewer";
import { StatusBadge } from "../../components/StatusBadge";
import { deploymentStatusMeta } from "../../lib/status";
import { useDeployments } from "../../stores/deployments";
import { useLogs } from "../../stores/logs";
import type { DeploymentSummary } from "../../types/api";
import { useProject } from "./context";

/** Live logs of the in-progress deployment, otherwise of the most recent one (even if it failed). */
export function LogsTab() {
  const project = useProject();
  const latest = useDeployments((s) => s.history[project.id]?.items[0]);
  const fetchHistory = useDeployments((s) => s.fetchHistory);

  useEffect(() => {
    fetchHistory(project.id, 20).catch(() => undefined);
  }, [project.id, fetchHistory]);

  const deployment: DeploymentSummary | null =
    project.active_deployment ?? latest ?? project.current_deployment;

  if (!deployment) {
    return (
      <EmptyState
        title="No logs yet"
        description="Logs appear here as soon as the first deployment starts."
      />
    );
  }
  return (
    <section className="flex flex-col gap-3">
      <div className="flex flex-wrap items-center gap-2 text-sm text-muted">
        Showing
        <Link to={`/deployments/${deployment.id}`} className="font-medium text-fg hover:underline">
          deployment #{deployment.number}
        </Link>
        <StatusBadge meta={deploymentStatusMeta(deployment.status)} size="sm" />
      </div>
      <DeploymentLogs deploymentId={deployment.id} />
    </section>
  );
}

export function DeploymentLogs({ deploymentId, height }: { deploymentId: string; height?: string }) {
  const stream = useLogs((s) => s.streams[deploymentId]);
  const subscribe = useLogs((s) => s.subscribe);
  const clear = useLogs((s) => s.clear);

  useEffect(() => subscribe(deploymentId), [deploymentId, subscribe]);

  return (
    <LogViewer
      lines={stream?.lines ?? []}
      connection={stream?.state ?? "connecting"}
      onClear={() => clear(deploymentId)}
      height={height}
      downloadName={`deployment-${deploymentId}.log`}
    />
  );
}
