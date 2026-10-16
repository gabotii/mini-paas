import { create } from "zustand";

import { api } from "../services/api";
import type { MetricEvent, MetricSnapshot, MetricsSummary } from "../types/api";

const MAX_SAMPLES = 720;

interface ProjectMetrics {
  samples: MetricSnapshot[];
  current: MetricsSummary | null;
  window: number;
  loading: boolean;
}

interface MetricsState {
  byProject: Record<string, ProjectMetrics>;
  fetch: (projectId: string, window?: number) => Promise<void>;
  applySample: (event: MetricEvent) => void;
}

export const useMetrics = create<MetricsState>((set) => ({
  byProject: {},

  fetch: async (projectId, window = 60) => {
    set((state) => ({
      byProject: {
        ...state.byProject,
        [projectId]: {
          ...(state.byProject[projectId] ?? { samples: [], current: null }),
          window,
          loading: true,
        },
      },
    }));
    try {
      const response = await api.metrics(projectId, window);
      set((state) => ({
        byProject: {
          ...state.byProject,
          [projectId]: { samples: response.samples, current: response.current, window, loading: false },
        },
      }));
    } catch (error) {
      set((state) => ({
        byProject: { ...state.byProject, [projectId]: { ...state.byProject[projectId]!, loading: false } },
      }));
      throw error;
    }
  },

  // Live samples pushed by the agent through the user's event stream.
  applySample: (event) =>
    set((state) => {
      const existing = state.byProject[event.project_id];
      if (!existing) return state;
      const { project_id: _p, deployment_id: _d, timestamp, ...current } = event;
      const samples = [...existing.samples, { timestamp, ...current }].slice(-MAX_SAMPLES);
      return { byProject: { ...state.byProject, [event.project_id]: { ...existing, samples, current } } };
    }),
}));
