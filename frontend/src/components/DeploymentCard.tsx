import { ChevronRight, GitCommit, RotateCcw } from "lucide-react";
import { Link } from "react-router";

import { formatDate, formatDuration, relativeTime, shortHash } from "../lib/format";
import { FAILURE_LABELS, RUNTIME_LABELS, deploymentStatusMeta } from "../lib/status";
import type { DeploymentSummary } from "../types/api";
import { StatusBadge } from "./StatusBadge";

interface DeploymentCardProps {
  deployment: DeploymentSummary;
  isCurrent?: boolean;
}

/** One row of the deployment history; the whole row links to the deployment detail. */
export function DeploymentCard({ deployment, isCurrent = false }: DeploymentCardProps) {
  const meta = deploymentStatusMeta(deployment.status);
  const outcome = deployment.failure_reason
    ? FAILURE_LABELS[deployment.failure_reason]
    : deployment.succeeded
      ? "Succeeded"
      : null;
  return (
    <Link
      to={`/deployments/${deployment.id}`}
      data-testid="deployment-row"
      className="flex items-center gap-3 px-4 py-3.5 transition-colors hover:bg-raised sm:gap-4"
    >
      <span className="w-10 shrink-0 font-mono text-sm text-muted">#{deployment.number}</span>
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-center gap-2">
          <StatusBadge meta={meta} size="sm" />
          {isCurrent && (
            <span className="rounded-full bg-accent/15 px-2 py-0.5 text-[11px] font-medium text-accent">
              Current
            </span>
          )}
          {deployment.trigger === "rollback" && (
            <span className="inline-flex items-center gap-1 text-[11px] text-muted">
              <RotateCcw className="h-3 w-3" aria-hidden /> rollback
            </span>
          )}
          {outcome && deployment.status !== "RUNNING" && (
            <span className="text-xs text-muted">{outcome}</span>
          )}
        </div>
        <div className="mt-1 flex flex-wrap gap-x-3 gap-y-0.5 text-xs text-subtle">
          <span title={formatDate(deployment.created_at)}>{relativeTime(deployment.created_at)}</span>
          {deployment.duration_seconds !== null && <span>{formatDuration(deployment.duration_seconds)}</span>}
          {deployment.runtime && <span>{RUNTIME_LABELS[deployment.runtime]}</span>}
          {deployment.commit_hash && (
            <span className="inline-flex items-center gap-1 font-mono">
              <GitCommit className="h-3 w-3" aria-hidden />
              {shortHash(deployment.commit_hash)}
            </span>
          )}
        </div>
      </div>
      <ChevronRight className="h-4 w-4 shrink-0 text-subtle" aria-hidden />
    </Link>
  );
}
