import { Boxes, Plus } from "lucide-react";
import { Link } from "react-router";
import { useShallow } from "zustand/react/shallow";

import { EmptyState, ErrorState } from "../components/ErrorState";
import { LoadingState } from "../components/LoadingState";
import { ProjectCard } from "../components/ProjectCard";
import { useProjects } from "../stores/projects";

export function DashboardPage() {
  const projects = useProjects(
    useShallow((s) => s.order.map((id) => s.byId[id]).filter((p) => p !== undefined)),
  );
  const { loaded, error, fetchAll, limits } = useProjects(
    useShallow((s) => ({ loaded: s.loaded, error: s.error, fetchAll: s.fetchAll, limits: s.limits })),
  );
  const running = projects.filter((p) => p.status === "RUNNING").length;
  const building = projects.filter((p) => p.active_deployment).length;
  const failing = projects.filter((p) => p.status === "FAILED" || p.status === "CRASHED").length;

  return (
    <div className="flex flex-col gap-6">
      <header className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Dashboard</h1>
          <p className="mt-1 text-sm text-muted">
            {projects.length} project{projects.length === 1 ? "" : "s"}
            {limits ? ` of ${limits.max_projects}` : ""} · {running} running
            {building ? ` · ${building} deploying` : ""}
            {failing ? ` · ${failing} need attention` : ""}
          </p>
        </div>
        <Link
          to="/projects/new"
          className="inline-flex h-10 items-center gap-2 rounded-lg bg-accent px-4 text-sm font-medium text-white hover:bg-accent-strong"
        >
          <Plus className="h-4 w-4" aria-hidden /> New Project
        </Link>
      </header>

      {!loaded ? (
        <LoadingState label="Loading projects…" />
      ) : error ? (
        <ErrorState message={error} onRetry={() => void fetchAll()} />
      ) : projects.length === 0 ? (
        <EmptyState
          icon={<Boxes className="h-10 w-10 text-accent" aria-hidden />}
          title="Deploy your first app"
          description="Upload a ZIP or point to a public Git repository. Python, Node.js, static sites and Dockerfiles are detected automatically."
          action={
            <Link
              to="/projects/new"
              className="mt-2 inline-flex h-10 items-center gap-2 rounded-lg bg-accent px-4 text-sm font-medium text-white hover:bg-accent-strong"
            >
              <Plus className="h-4 w-4" aria-hidden /> Create a project
            </Link>
          }
        />
      ) : (
        <div className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-3" data-testid="project-grid">
          {projects.map((project) => (
            <ProjectCard key={project.id} project={project} />
          ))}
        </div>
      )}
    </div>
  );
}
