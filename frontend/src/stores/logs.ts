import { create } from "zustand";

import { openSocket, type SocketHandle, type SocketState } from "../services/socket";
import type { LogLine } from "../types/api";

export const MAX_LINES = 5000;

interface Stream {
  lines: LogLine[];
  state: SocketState;
}

interface LogsState {
  streams: Record<string, Stream>;
  subscribe: (deploymentId: string) => () => void;
  clear: (deploymentId: string) => void;
}

type LogMessage =
  { type: "backlog" | "logs"; data: { lines: LogLine[] } } | { type: "ping"; data: Record<string, never> };

const handles = new Map<string, { socket: SocketHandle; refs: number }>();

/** Append lines newer than what we have (ids are monotonic), keeping at most MAX_LINES. */
export function mergeLogLines(existing: LogLine[], incoming: LogLine[]): LogLine[] {
  const lastId = existing.length ? existing[existing.length - 1]!.id : 0;
  const fresh = incoming.filter((line) => line.id > lastId);
  const merged = fresh.length ? existing.concat(fresh) : existing;
  return merged.length > MAX_LINES ? merged.slice(merged.length - MAX_LINES) : merged;
}

export const useLogs = create<LogsState>((set, get) => ({
  streams: {},

  // Reference-counted live subscription to one deployment's logs.
  subscribe: (deploymentId) => {
    const existing = handles.get(deploymentId);
    if (existing) {
      existing.refs += 1;
    } else {
      const update = (patch: Partial<Stream>) =>
        set((state) => ({
          streams: {
            ...state.streams,
            [deploymentId]: {
              ...(state.streams[deploymentId] ?? { lines: [], state: "connecting" }),
              ...patch,
            },
          },
        }));
      const socket = openSocket<LogMessage>({
        path: `/ws/deployments/${deploymentId}/logs`,
        onState: (state) => update({ state }),
        onMessage: (message) => {
          if (message.type === "ping") return;
          const current = get().streams[deploymentId]?.lines ?? [];
          update({ lines: mergeLogLines(current, message.data.lines) });
        },
      });
      handles.set(deploymentId, { socket, refs: 1 });
    }
    return () => {
      const handle = handles.get(deploymentId);
      if (!handle) return;
      handle.refs -= 1;
      if (handle.refs <= 0) {
        handle.socket.close();
        handles.delete(deploymentId);
      }
    };
  },

  clear: (deploymentId) =>
    set((state) => ({
      streams: {
        ...state.streams,
        [deploymentId]: { state: state.streams[deploymentId]?.state ?? "closed", lines: [] },
      },
    })),
}));
