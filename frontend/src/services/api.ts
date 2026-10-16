// Thin typed HTTP client.
//
// * The access token lives in memory only (never localStorage).
// * The refresh token is an httpOnly cookie scoped to /api/auth; on a 401 the client refreshes
//   once (concurrent requests share the same refresh) and retries the original request.

import type {
  ApiErrorBody,
  AuditEntry,
  Deployment,
  DeploymentPage,
  EnvVar,
  LogPage,
  MetricsResponse,
  Project,
  ProjectCreate,
  ProjectList,
  ProjectUpdate,
  TokenResponse,
  User,
} from "../types/api";

export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly details: Record<string, unknown>;

  constructor(status: number, code: string, message: string, details: Record<string, unknown> = {}) {
    super(message);
    this.status = status;
    this.code = code;
    this.details = details;
  }

  /** Field-level validation messages, keyed by field name. */
  fieldErrors(): Record<string, string> {
    const errors = (this.details.errors as { field: string; message: string }[] | undefined) ?? [];
    return Object.fromEntries(errors.map((e) => [e.field, e.message.replace(/^Value error, /, "")]));
  }
}

type TokenListener = (token: string | null, user?: User) => void;

let accessToken: string | null = null;
let refreshing: Promise<boolean> | null = null;
const listeners = new Set<TokenListener>();

export function setAccessToken(token: string | null, user?: User): void {
  accessToken = token;
  listeners.forEach((listener) => listener(token, user));
}

export function onTokenChange(listener: TokenListener): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

async function parseError(response: Response): Promise<ApiError> {
  try {
    const body = (await response.json()) as ApiErrorBody;
    if (body?.error) {
      return new ApiError(response.status, body.error.code, body.error.message, body.error.details ?? {});
    }
  } catch {
    // not JSON
  }
  return new ApiError(response.status, "HTTP_ERROR", response.statusText || "Request failed");
}

/** Try to obtain a new access token from the refresh cookie. Shared by concurrent callers. */
export function refreshSession(): Promise<boolean> {
  refreshing ??= (async () => {
    try {
      const response = await fetch("/api/auth/refresh", { method: "POST", credentials: "same-origin" });
      if (!response.ok) {
        setAccessToken(null);
        return false;
      }
      const body = (await response.json()) as TokenResponse;
      setAccessToken(body.access_token, body.user);
      return true;
    } catch {
      return false;
    } finally {
      refreshing = null;
    }
  })();
  return refreshing;
}

interface RequestOptions {
  method?: string;
  body?: unknown;
  raw?: BodyInit;
  headers?: Record<string, string>;
  query?: Record<string, string | number | boolean | undefined | null | string[]>;
  auth?: boolean;
  signal?: AbortSignal;
}

function buildUrl(path: string, query?: RequestOptions["query"]): string {
  if (!query) return path;
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(query)) {
    if (value === undefined || value === null) continue;
    if (Array.isArray(value)) value.forEach((v) => params.append(key, v));
    else params.set(key, String(value));
  }
  const qs = params.toString();
  return qs ? `${path}?${qs}` : path;
}

export async function request<T>(path: string, options: RequestOptions = {}, retried = false): Promise<T> {
  const headers: Record<string, string> = { Accept: "application/json", ...options.headers };
  let body: BodyInit | undefined = options.raw;
  if (options.body !== undefined) {
    headers["Content-Type"] = "application/json";
    body = JSON.stringify(options.body);
  }
  if (options.auth !== false && accessToken) headers.Authorization = `Bearer ${accessToken}`;

  let response: Response;
  try {
    response = await fetch(buildUrl(`/api${path}`, options.query), {
      method: options.method ?? "GET",
      headers,
      body,
      credentials: "same-origin",
      signal: options.signal,
    });
  } catch (error) {
    if ((error as Error).name === "AbortError") throw error;
    throw new ApiError(0, "NETWORK_ERROR", "Cannot reach the server. Check your connection.");
  }

  if (response.status === 401 && options.auth !== false && !retried) {
    if (await refreshSession()) return request<T>(path, options, true);
  }
  if (!response.ok) throw await parseError(response);
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

// ------------------------------------------------------------------ endpoints
export const api = {
  register: (data: { email: string; password: string; name: string }) =>
    request<TokenResponse>("/auth/register", { method: "POST", body: data, auth: false }),
  login: (data: { email: string; password: string }) =>
    request<TokenResponse>("/auth/login", { method: "POST", body: data, auth: false }),
  logout: () => request<void>("/auth/logout", { method: "POST" }),
  me: () => request<User>("/auth/me"),
  wsTicket: () => request<{ ticket: string; expires_in: number }>("/auth/ws-ticket", { method: "POST" }),
  activity: () => request<AuditEntry[]>("/account/activity"),

  projects: () => request<ProjectList>("/projects"),
  project: (id: string) => request<Project>(`/projects/${id}`),
  createProject: (data: ProjectCreate) => request<Project>("/projects", { method: "POST", body: data }),
  updateProject: (id: string, data: ProjectUpdate) =>
    request<Project>(`/projects/${id}`, { method: "PATCH", body: data }),
  deleteProject: (id: string) => request<{ detail: string }>(`/projects/${id}`, { method: "DELETE" }),

  deploy: (id: string, branch?: string) =>
    request<Deployment>(`/projects/${id}/deploy`, { method: "POST", body: branch ? { branch } : undefined }),
  upload: (id: string, file: File) =>
    request<Deployment>(`/projects/${id}/upload`, {
      method: "POST",
      raw: file,
      headers: { "Content-Type": "application/zip" },
    }),
  start: (id: string) => request<Deployment>(`/projects/${id}/start`, { method: "POST" }),
  stop: (id: string) => request<{ detail: string }>(`/projects/${id}/stop`, { method: "POST" }),
  restart: (id: string) => request<{ detail: string }>(`/projects/${id}/restart`, { method: "POST" }),

  deployments: (id: string, limit = 20, offset = 0) =>
    request<DeploymentPage>(`/projects/${id}/deployments`, { query: { limit, offset } }),
  deployment: (id: string) => request<Deployment>(`/deployments/${id}`),
  cancel: (id: string) => request<Deployment>(`/deployments/${id}/cancel`, { method: "POST" }),
  rollback: (id: string) => request<Deployment>(`/deployments/${id}/rollback`, { method: "POST" }),
  deploymentLogs: (id: string, query: RequestOptions["query"] = {}) =>
    request<LogPage>(`/deployments/${id}/logs`, { query }),

  metrics: (id: string, window = 60) =>
    request<MetricsResponse>(`/projects/${id}/metrics`, { query: { window } }),

  env: (id: string) => request<EnvVar[]>(`/projects/${id}/env`),
  createEnv: (id: string, data: { key: string; value: string; is_secret: boolean }) =>
    request<EnvVar>(`/projects/${id}/env`, { method: "POST", body: data }),
  updateEnv: (id: string, key: string, data: { value?: string; is_secret?: boolean }) =>
    request<EnvVar>(`/projects/${id}/env/${encodeURIComponent(key)}`, { method: "PATCH", body: data }),
  deleteEnv: (id: string, key: string) =>
    request<void>(`/projects/${id}/env/${encodeURIComponent(key)}`, { method: "DELETE" }),
  revealEnv: (id: string, key: string) =>
    request<{ key: string; value: string }>(`/projects/${id}/env/${encodeURIComponent(key)}/reveal`, {
      method: "POST",
    }),
};
