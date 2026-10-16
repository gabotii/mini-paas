// One WebSocket per signed-in user carrying status changes and metric samples for all of their
// projects. Stores update themselves from it, so pages never poll for deployment status.

import { create } from "zustand";

import { openSocket, type SocketHandle, type SocketState } from "../services/socket";
import type { ServerEvent } from "../types/api";
import { useDeployments } from "./deployments";
import { useMetrics } from "./metrics";
import { useProjects } from "./projects";
import { toast } from "./toast";

interface EventsState {
  state: SocketState;
  connect: () => void;
  disconnect: () => void;
}

let handle: SocketHandle | null = null;

export function dispatchServerEvent(event: ServerEvent): void {
  switch (event.type) {
    case "deployment.updated": {
      const project = useProjects.getState().byId[event.data.project_id];
      useProjects.getState().applyDeployment(event.data);
      useDeployments.getState().applyEvent(event.data);
      const name = project?.name ?? "Project";
      if (event.data.status === "RUNNING") {
        toast.success(`${name} is live`, `Deployment #${event.data.number} succeeded`);
      } else if (event.data.status === "FAILED") {
        toast.error(
          `${name}: deployment #${event.data.number} failed`,
          event.data.error_message ?? undefined,
        );
      }
      break;
    }
    case "project.updated":
      useProjects.getState().applyProject(event.data);
      break;
    case "metrics.sample":
      useProjects.getState().applyMetric(event.data);
      useMetrics.getState().applySample(event.data);
      break;
    default:
      break;
  }
}

export const useEvents = create<EventsState>((set) => ({
  state: "closed",
  connect: () => {
    if (handle) return;
    handle = openSocket<ServerEvent>({
      path: "/ws/events",
      onMessage: dispatchServerEvent,
      onState: (state) => set({ state }),
    });
  },
  disconnect: () => {
    handle?.close();
    handle = null;
    set({ state: "closed" });
  },
}));
