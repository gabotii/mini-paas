import { Clock, Cpu, GitCommit, Globe, HardDrive, Layers, Network } from "lucide-react";
import type { ReactNode } from "react";
import { Link } from "react-router";

import { MetricCard } from "../../components/MetricCard";
import { StatusBadge } from "../../components/StatusBadge";
import { useNow } from "../../hooks/useNow";
import {
  formatBytes,
  formatDateTime,
  formatDuration,
  formatPercent,
  secondsSince,
  shortHash,
} from "../../lib/format";
import { FAILURE_LABELS, RUNTIME_LABELS, deploymentStatusMeta } from "../../lib/status";
import { useProject } from "./context";

export function OverviewTab() {
  const project = useProject();
  const now = useNow(1000);
  const current = project.current_deployment;
  const active = project.active_deployment;
  const metrics = project.latest_metrics;
  const uptime = project.status === "RUNNING" ? secondsSince(current?.deployed_at, now) : null;

  return (
    <div className="flex flex-col gap-6">
      {active && (
        <Link
          to={`/projects/${project.id}/logs`}
          className="card flex flex-wrap items-center justify-between gap-3 border-info/30 bg-info/5 px-4 py-3 hover:border-info/50"
        >
          <span className="flex items-center gap-3 text-sm">
            <StatusBadge meta={deploymentStatusMeta(active.status)} size="sm" />
            Deployment #{active.number} in progress
          </span>
          <span className="text-sm text-info">View live logs →</span>
        </Link>
      )}

      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <MetricCard
          label="CPU"
          value={formatPercent(metrics?.cpu_usage)}
          icon={<Cpu className="h-4 w-4" />}
          hint={`limit ${project.cpu_limit} CPU`}
        />
        <MetricCard
          label="Memory"
          value={formatBytes(metrics?.memory_usage)}
          icon={<HardDrive className="h-4 w-4" />}
          ratio={metrics ? metrics.memory_usage / metrics.memory_limit : null}
          hint={`limit ${project.memory_limit_mb} MB`}
        />
        <MetricCard
          label="Network"
          value={metrics ? formatBytes(metrics.network_rx + metrics.network_tx) : "—"}
          icon={<Network className="h-4 w-4" />}
          hint={
            metrics
              ? `↓ ${formatBytes(metrics.network_rx)} · ↑ ${formatBytes(metrics.network_tx)}`
              : undefined
          }
        />
        <MetricCard
          label="Uptime"
          value={formatDuration(uptime)}
          icon={<Clock className="h-4 w-4" />}
          hint={metrics ? `${metrics.restart_count} restarts` : undefined}
        />
      </div>

      <section className="card divide-y divide-line">
        <Row icon={<Globe className="h-4 w-4" />} label="URL">
          <a
            href={project.url}
            target="_blank"
            rel="noreferrer"
            className="break-all text-accent hover:underline"
          >
            {project.url}
          </a>
        </Row>
        <Row icon={<Layers className="h-4 w-4" />} label="Runtime">
          {RUNTIME_LABELS[current?.runtime ?? project.runtime]}
          {project.runtime === "auto" && current?.runtime ? " (detected)" : ""}
        </Row>
        <Row icon={<GitCommit className="h-4 w-4" />} label="Source">
          {project.source_type === "git" ? (
            <span className="break-all">
              {project.repository_url}{" "}
              {project.repository_branch && <span className="text-muted">@ {project.repository_branch}</span>}
            </span>
          ) : (
            "ZIP upload"
          )}
        </Row>
        <Row label="Current deployment">
          {current ? (
            <Link
              to={`/deployments/${current.id}`}
              className="inline-flex flex-wrap items-center gap-2 hover:underline"
            >
              #{current.number}
              <StatusBadge meta={deploymentStatusMeta(current.status)} size="sm" />
              {current.failure_reason && (
                <span className="text-xs text-bad">{FAILURE_LABELS[current.failure_reason]}</span>
              )}
              {current.commit_hash && (
                <span className="font-mono text-xs text-muted">{shortHash(current.commit_hash)}</span>
              )}
            </Link>
          ) : (
            <span className="text-muted">Not deployed yet</span>
          )}
        </Row>
        <Row label="Deployed at">{formatDateTime(current?.deployed_at)}</Row>
        <Row label="Resources">
          {project.cpu_limit} CPU · {project.memory_limit_mb} MB RAM
        </Row>
      </section>
    </div>
  );
}

function Row({ label, icon, children }: { label: string; icon?: ReactNode; children: ReactNode }) {
  return (
    <div className="flex flex-col gap-1 px-4 py-3 text-sm sm:flex-row sm:items-center sm:gap-4">
      <span className="flex w-44 shrink-0 items-center gap-2 text-muted">
        {icon}
        {label}
      </span>
      <span className="min-w-0">{children}</span>
    </div>
  );
}
