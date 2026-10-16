import { ExternalLink, Play, RotateCw, Square } from "lucide-react";
import { useEffect, useState } from "react";
import { Outlet, useParams } from "react-router";

import { Button } from "../../components/Button";
import { ConfirmDialog } from "../../components/ConfirmDialog";
import { DeployButton } from "../../components/DeployButton";
import { ErrorState } from "../../components/ErrorState";
import { LoadingState } from "../../components/LoadingState";
import { StatusBadge } from "../../components/StatusBadge";
import { Tabs } from "../../components/Tabs";
import { useDeployActions } from "../../hooks/useDeployActions";
import { effectiveProjectStatus } from "../../lib/status";
import { ApiError } from "../../services/api";
import { useProjects } from "../../stores/projects";
import type { Project } from "../../types/api";

export function ProjectLayout() {
  const { projectId = "" } = useParams();
  const project = useProjects((s) => s.byId[projectId]);
  const fetchOne = useProjects((s) => s.fetchOne);
  // Errors are keyed by project id, so navigating to another project never shows a stale one.
  const [failure, setFailure] = useState<{ id: string; message: string } | null>(null);

  useEffect(() => {
    fetchOne(projectId).catch((err: unknown) =>
      setFailure({
        id: projectId,
        message:
          err instanceof ApiError && err.status === 404 ? "Project not found." : (err as Error).message,
      }),
    );
  }, [projectId, fetchOne]);

  if (failure?.id === projectId && !project) return <ErrorState message={failure.message} />;
  if (!project) return <LoadingState label="Loading project…" />;

  const base = `/projects/${project.id}`;
  return (
    <div className="flex flex-col gap-6">
      <ProjectHeader project={project} />
      <Tabs
        items={[
          { to: base, label: "Overview", end: true },
          { to: `${base}/deployments`, label: "Deployments" },
          { to: `${base}/logs`, label: "Logs" },
          { to: `${base}/environment`, label: "Environment" },
          { to: `${base}/metrics`, label: "Metrics" },
          { to: `${base}/settings`, label: "Settings" },
        ]}
      />
      <Outlet context={{ project }} />
    </div>
  );
}

function ProjectHeader({ project }: { project: Project }) {
  const actions = useDeployActions(project);
  const [confirmStop, setConfirmStop] = useState(false);
  const running = project.status === "RUNNING";
  const canStart =
    (project.status === "STOPPED" || project.status === "CRASHED") && Boolean(project.current_deployment);
  const canStop = running || project.status === "CRASHED" || Boolean(project.active_deployment);

  return (
    <header className="flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
      <div className="min-w-0">
        <div className="flex flex-wrap items-center gap-3">
          <h1 className="truncate text-2xl font-semibold tracking-tight" data-testid="project-name">
            {project.name}
          </h1>
          <StatusBadge meta={effectiveProjectStatus(project)} />
        </div>
        <a
          href={project.url}
          target="_blank"
          rel="noreferrer"
          className={`mt-1 inline-flex max-w-full items-center gap-1.5 font-mono text-sm ${
            running ? "text-accent hover:underline" : "pointer-events-none text-subtle"
          }`}
          aria-disabled={!running}
          data-testid="project-url"
        >
          <ExternalLink className="h-3.5 w-3.5 shrink-0" aria-hidden />
          <span className="truncate">{project.url}</span>
        </a>
      </div>
      <div className="flex flex-wrap gap-2">
        {canStart && (
          <Button
            icon={<Play className="h-4 w-4" />}
            onClick={() => void actions.start()}
            loading={actions.busy}
          >
            Start
          </Button>
        )}
        {running && (
          <Button
            icon={<RotateCw className="h-4 w-4" />}
            onClick={() => void actions.restart()}
            disabled={actions.busy}
          >
            Restart
          </Button>
        )}
        {canStop && (
          <Button
            icon={<Square className="h-4 w-4" />}
            onClick={() => setConfirmStop(true)}
            disabled={actions.busy}
          >
            Stop
          </Button>
        )}
        <DeployButton project={project} />
      </div>
      <ConfirmDialog
        open={confirmStop}
        title="Stop application"
        description={`${project.name} will stop serving traffic and its container will be removed. You can start it again from the last deployment.`}
        confirmLabel="Stop"
        danger
        onConfirm={async () => {
          await actions.stop();
        }}
        onClose={() => setConfirmStop(false)}
      />
    </header>
  );
}
