import { Activity, LayoutGrid, LogOut, Plus } from "lucide-react";
import { NavLink } from "react-router";
import { useShallow } from "zustand/react/shallow";

import { effectiveProjectStatus } from "../lib/status";
import { useAuth } from "../stores/auth";
import { useEvents } from "../stores/events";
import { useProjects } from "../stores/projects";
import { StatusDot } from "./StatusBadge";

export function Logo() {
  return (
    <span className="inline-flex items-center gap-2 font-semibold tracking-tight">
      <svg viewBox="0 0 32 32" className="h-7 w-7" aria-hidden>
        <rect width="32" height="32" rx="8" fill="var(--color-accent)" />
        <path d="M9 21l7-11 7 11z" fill="#fff" />
      </svg>
      Mini PaaS
    </span>
  );
}

const linkClass = ({ isActive }: { isActive: boolean }) =>
  `flex items-center gap-2.5 rounded-lg px-3 py-2 text-sm transition-colors ${
    isActive ? "bg-raised text-fg" : "text-muted hover:bg-raised/60 hover:text-fg"
  }`;

export function ProjectSidebar({ onNavigate }: { onNavigate?: () => void }) {
  // useShallow: the selector builds a new array, compare its items instead of its identity.
  const projects = useProjects(
    useShallow((s) => s.order.map((id) => s.byId[id]).filter((p) => p !== undefined)),
  );
  const user = useAuth((s) => s.user);
  const logout = useAuth((s) => s.logout);
  const live = useEvents((s) => s.state);

  return (
    <div className="flex h-full flex-col gap-6 px-3 py-5">
      <div className="px-2">
        <Logo />
      </div>

      <nav className="flex flex-col gap-1" aria-label="Main">
        <NavLink to="/" end className={linkClass} onClick={onNavigate}>
          <LayoutGrid className="h-4 w-4" aria-hidden /> Dashboard
        </NavLink>
        <NavLink to="/projects/new" className={linkClass} onClick={onNavigate}>
          <Plus className="h-4 w-4" aria-hidden /> New project
        </NavLink>
        <NavLink to="/activity" className={linkClass} onClick={onNavigate}>
          <Activity className="h-4 w-4" aria-hidden /> Activity
        </NavLink>
      </nav>

      <div className="flex min-h-0 flex-1 flex-col">
        <p className="mb-2 px-3 text-[11px] font-semibold tracking-wider text-subtle uppercase">Projects</p>
        <ul className="flex min-h-0 flex-col gap-0.5 overflow-y-auto">
          {projects.length === 0 && <li className="px-3 text-xs text-subtle">No projects yet</li>}
          {projects.map((project) => (
            <li key={project.id}>
              <NavLink to={`/projects/${project.id}`} className={linkClass} onClick={onNavigate}>
                <StatusDot meta={effectiveProjectStatus(project)} />
                <span className="truncate">{project.name}</span>
              </NavLink>
            </li>
          ))}
        </ul>
      </div>

      <div className="flex items-center gap-3 border-t border-line px-2 pt-4">
        <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-accent/20 text-sm font-semibold text-accent">
          {user?.name.slice(0, 1).toUpperCase() ?? "?"}
        </div>
        <div className="min-w-0 flex-1">
          <p className="truncate text-sm font-medium">{user?.name}</p>
          <p className="flex items-center gap-1.5 truncate text-xs text-subtle">
            <span
              className={`h-1.5 w-1.5 rounded-full ${live === "open" ? "bg-ok" : "bg-warn"}`}
              aria-hidden
            />
            {live === "open" ? "Live updates" : "Connecting…"}
          </p>
        </div>
        <button
          onClick={() => void logout()}
          className="rounded-md p-2 text-muted hover:bg-raised hover:text-fg"
          aria-label="Log out"
          title="Log out"
        >
          <LogOut className="h-4 w-4" />
        </button>
      </div>
    </div>
  );
}
