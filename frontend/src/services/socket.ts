// Reconnecting WebSocket authenticated with single-use tickets.
// Each (re)connection asks the API for a fresh 30-second ticket; the JWT never goes in a URL.

import { api } from "./api";

export type SocketState = "connecting" | "open" | "closed";

export interface SocketOptions<T> {
  path: string;
  onMessage: (message: T) => void;
  onState?: (state: SocketState) => void;
  /** Close codes after which we should not retry (auth or not-found). */
  fatalCodes?: number[];
}

export interface SocketHandle {
  close: () => void;
}

const MAX_BACKOFF_MS = 15_000;

export function socketUrl(path: string): string {
  const scheme = window.location.protocol === "https:" ? "wss" : "ws";
  return `${scheme}://${window.location.host}${path}`;
}

export function openSocket<T>({
  path,
  onMessage,
  onState,
  fatalCodes = [4401, 4404],
}: SocketOptions<T>): SocketHandle {
  let socket: WebSocket | null = null;
  let stopped = false;
  let attempt = 0;
  let timer: ReturnType<typeof setTimeout> | undefined;

  const schedule = () => {
    if (stopped) return;
    const delay = Math.min(MAX_BACKOFF_MS, 500 * 2 ** attempt) + Math.random() * 250;
    attempt += 1;
    timer = setTimeout(connect, delay);
  };

  async function connect() {
    if (stopped) return;
    onState?.("connecting");
    let ticket: string;
    try {
      ticket = (await api.wsTicket()).ticket;
    } catch {
      onState?.("closed");
      schedule();
      return;
    }
    if (stopped) return;
    const separator = path.includes("?") ? "&" : "?";
    socket = new WebSocket(socketUrl(`${path}${separator}ticket=${encodeURIComponent(ticket)}`));
    socket.onopen = () => {
      attempt = 0;
      onState?.("open");
    };
    socket.onmessage = (event) => {
      try {
        onMessage(JSON.parse(event.data as string) as T);
      } catch {
        // ignore malformed frames
      }
    };
    socket.onclose = (event) => {
      socket = null;
      onState?.("closed");
      if (!stopped && !fatalCodes.includes(event.code)) schedule();
    };
  }

  void connect();

  return {
    close() {
      stopped = true;
      clearTimeout(timer);
      socket?.close();
      socket = null;
    },
  };
}
