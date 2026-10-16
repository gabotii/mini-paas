import { create } from "zustand";

import { api } from "../services/api";
import type { Deployment, DeploymentEvent, DeploymentSummary } from "../types/api";
import { toSummary } from "./projects";

interface History {
  items: DeploymentSummary[];
  total: number;
  loading: boolean;
}

interface DeploymentsState {
  history: Record<string, History>;
  details: Record<string, Deployment>;
  fetchHistory: (projectId: string, limit?: number) => Promise<void>;
  fetchDetail: (deploymentId: string) => Promise<Deployment>;
  track: (deployment: DeploymentSummary) => void;
  applyEvent: (event: DeploymentEvent) => void;
  reset: () => void;
}

const EMPTY: History = { items: [], total: 0, loading: false };

export const useDeployments = create<DeploymentsState>((set, get) => ({
  history: {},
  details: {},

  fetchHistory: async (projectId, limit = 50) => {
    const setHistory = (patch: Partial<History>) =>
      set((state) => ({
        history: { ...state.history, [projectId]: { ...(state.history[projectId] ?? EMPTY), ...patch } },
      }));
    setHistory({ loading: true });
    try {
      const page = await api.deployments(projectId, limit);
      setHistory({ items: page.items, total: page.total, loading: false });
    } catch (error) {
      setHistory({ loading: false });
      throw error;
    }
  },

  fetchDetail: async (deploymentId) => {
    const deployment = await api.deployment(deploymentId);
    set((state) => ({ details: { ...state.details, [deploymentId]: deployment } }));
    return deployment;
  },

  // Insert a deployment we just created so the UI reacts before the first event arrives.
  track: (deployment) => {
    const current = get().history[deployment.project_id] ?? EMPTY;
    if (current.items.some((d) => d.id === deployment.id)) return;
    set((state) => ({
      history: {
        ...state.history,
        [deployment.project_id]: {
          ...current,
          items: [deployment, ...current.items],
          total: current.total + 1,
        },
      },
    }));
  },

  applyEvent: (event) =>
    set((state) => {
      const summary = toSummary(event);
      const history = state.history[event.project_id];
      let nextHistory = state.history;
      if (history) {
        const known = history.items.some((d) => d.id === event.id);
        const items = known
          ? history.items.map((d) => (d.id === event.id ? summary : d))
          : [summary, ...history.items];
        nextHistory = {
          ...state.history,
          [event.project_id]: { ...history, items, total: known ? history.total : history.total + 1 },
        };
      }
      const detail = state.details[event.id];
      const nextDetails = detail
        ? { ...state.details, [event.id]: { ...detail, ...summary, error_message: event.error_message } }
        : state.details;
      return { history: nextHistory, details: nextDetails };
    }),

  reset: () => set({ history: {}, details: {} }),
}));
