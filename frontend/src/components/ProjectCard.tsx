import { Cpu, ExternalLink, GitBranch, HardDrive, Package } from "lucide-react";
import { Link } from "react-router";

import { formatBytes, formatPercent, relativeTime } from "../lib/format";
import { RUNTIME_LABELS, effectiveProjectStatus } from "../lib/status";
import type { Project } from "../types/api";
import { StatusBadge } from "./StatusBadge";

export function ProjectCard({ project }: { project: Project }) {
  const status = effectiveProjectStatus(project);
  const metrics = project.latest_metrics;
  const runtime = project.current_deployment?.runtime ?? project.runtime;
  return (
    <article className="card group relative flex flex-col gap-4 p-5 transition-colors hover:border-line-strong">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <h3 className="truncate text-base font-semibold">
            <Link to={`/projects/${project.id}`} className="after:absolute after:inset-0 focus:outline-none">
              {project.name}
            </Link>
          </h3>
          <p className="truncate font-mono text-xs text-muted">{project.slug}</p>
        </div>
        <StatusBadge meta={status} size="sm" />
      </div>

      {project.description && <p className="line-clamp-2 text-sm text-muted">{project.description}</p>}

      <div className="mt-auto grid grid-cols-2 gap-2 text-xs text-muted">
        <span className="inline-flex items-center gap-1.5">
          <Cpu className="h-3.5 w-3.5" aria-hidden />
          CPU {metrics ? formatPercent(metrics.cpu_usage) : "—"}
        </span>
        <span className="inline-flex items-center gap-1.5">
          <HardDrive className="h-3.5 w-3.5" aria-hidden />
          RAM {metrics ? formatBytes(metrics.memory_usage, 0) : "—"}
        </span>
        <span className="inline-flex items-center gap-1.5">
          {project.source_type === "git" ? (
            <GitBranch className="h-3.5 w-3.5" aria-hidden />
          ) : (
            <Package className="h-3.5 w-3.5" aria-hidden />
          )}
          {RUNTIME_LABELS[runtime]}
        </span>
        <span className="truncate">
          {project.current_deployment
            ? `#${project.current_deployment.number} · ${relativeTime(project.current_deployment.created_at)}`
            : "never deployed"}
        </span>
      </div>

      {project.status === "RUNNING" && (
        <a
          href={project.url}
          target="_blank"
          rel="noreferrer"
          className="relative z-10 inline-flex w-fit max-w-full items-center gap-1.5 truncate text-xs text-accent hover:underline"
        >
          <ExternalLink className="h-3.5 w-3.5 shrink-0" aria-hidden />
          <span className="truncate">{project.url.replace(/^https?:\/\//, "")}</span>
        </a>
      )}
    </article>
  );
}
