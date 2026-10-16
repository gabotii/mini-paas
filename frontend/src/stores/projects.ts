import { create } from "zustand";

import { api } from "../services/api";
import type {
  DeploymentEvent,
  DeploymentSummary,
  MetricEvent,
  PlatformLimits,
  Project,
  ProjectCreate,
  ProjectEvent,
  ProjectUpdate,
} from "../types/api";
import { ACTIVE_STATUSES } from "../types/api";

interface ProjectsState {
  byId: Record<string, Project>;
  order: string[];
  limits: PlatformLimits | null;
  loaded: boolean;
  error: string | null;
  fetchAll: () => Promise<void>;
  fetchOne: (id: string) => Promise<Project>;
  create: (data: ProjectCreate) => Promise<Project>;
  update: (id: string, data: ProjectUpdate) => Promise<Project>;
  remove: (id: string) => Promise<void>;
  upsert: (project: Project) => void;
  applyDeployment: (event: DeploymentEvent) => void;
  applyProject: (event: ProjectEvent) => void;
  applyMetric: (event: MetricEvent) => void;
  reset: () => void;
}

export function toSummary(event: DeploymentEvent): DeploymentSummary {
  const { project_status: _status, error_message: _error, ...rest } = event;
  return rest;
}

export const useProjects = create<ProjectsState>((set, get) => ({
  byId: {},
  order: [],
  limits: null,
  loaded: false,
  error: null,

  fetchAll: async () => {
    try {
      const list = await api.projects();
      set({
        byId: Object.fromEntries(list.items.map((p) => [p.id, p])),
        order: list.items.map((p) => p.id),
        limits: list.limits,
        loaded: true,
        error: null,
      });
    } catch (error) {
      set({ loaded: true, error: (error as Error).message });
    }
  },

  fetchOne: async (id) => {
    const project = await api.project(id);
    get().upsert(project);
    return project;
  },

  create: async (data) => {
    const project = await api.createProject(data);
    get().upsert(project);
    return project;
  },

  update: async (id, data) => {
    const project = await api.updateProject(id, data);
    get().upsert(project);
    return project;
  },

  remove: async (id) => {
    await api.deleteProject(id);
    set((state) => {
      const byId = { ...state.byId };
      delete byId[id];
      return { byId, order: state.order.filter((x) => x !== id) };
    });
  },

  upsert: (project) =>
    set((state) => ({
      byId: { ...state.byId, [project.id]: project },
      order: state.order.includes(project.id) ? state.order : [project.id, ...state.order],
    })),

  applyDeployment: (event) =>
    set((state) => {
      const project = state.byId[event.project_id];
      if (!project) return state;
      const deployment = toSummary(event);
      const isActive = ACTIVE_STATUSES.includes(event.status);
      let active = project.active_deployment;
      if (isActive) active = deployment;
      else if (active?.id === event.id) active = null;
      const current =
        event.status === "RUNNING" || project.current_deployment?.id === event.id
          ? deployment
          : project.current_deployment;
      const next: Project = {
        ...project,
        status: event.project_status,
        active_deployment: active,
        current_deployment: current,
      };
      return { byId: { ...state.byId, [project.id]: next } };
    }),

  applyProject: (event) =>
    set((state) => {
      const project = state.byId[event.id];
      if (!project) return state;
      const latest = event.status === "RUNNING" ? project.latest_metrics : null;
      return {
        byId: { ...state.byId, [event.id]: { ...project, status: event.status, latest_metrics: latest } },
      };
    }),

  applyMetric: (event) =>
    set((state) => {
      const project = state.byId[event.project_id];
      if (!project) return state;
      return { byId: { ...state.byId, [project.id]: { ...project, latest_metrics: event } } };
    }),

  reset: () => set({ byId: {}, order: [], limits: null, loaded: false, error: null }),
}));
