import { useEffect, useState } from "react";

import { EmptyState, ErrorState } from "../components/ErrorState";
import { LoadingState } from "../components/LoadingState";
import { formatDateTime } from "../lib/format";
import { api } from "../services/api";
import type { AuditEntry } from "../types/api";

const TONE: Record<string, string> = {
  DEPLOYMENT_FAILED: "text-bad",
  USER_LOGIN_FAILED: "text-bad",
  REFRESH_TOKEN_REUSE: "text-bad",
  PROJECT_DELETED: "text-warn",
  ENV_REVEALED: "text-warn",
  DEPLOYMENT_SUCCEEDED: "text-ok",
};

/** The signed-in user's audit trail (logins, deployments, env changes, deletions). */
export function ActivityPage() {
  const [entries, setEntries] = useState<AuditEntry[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = () => {
    api
      .activity()
      .then((rows) => {
        setEntries(rows);
        setError(null);
      })
      .catch((err: unknown) => setError((err as Error).message));
  };
  useEffect(load, []);

  return (
    <div className="flex flex-col gap-6">
      <header>
        <h1 className="text-2xl font-semibold tracking-tight">Activity</h1>
        <p className="mt-1 text-sm text-muted">Security-relevant actions on your account, newest first.</p>
      </header>
      {error ? (
        <ErrorState message={error} onRetry={load} />
      ) : !entries ? (
        <LoadingState />
      ) : entries.length === 0 ? (
        <EmptyState title="No activity yet" />
      ) : (
        <ol className="card divide-y divide-line">
          {entries.map((entry) => (
            <li
              key={entry.id}
              className="flex flex-col gap-1 px-4 py-3 text-sm sm:flex-row sm:items-center sm:gap-4"
            >
              <span className={`font-mono text-xs sm:w-56 ${TONE[entry.action] ?? "text-fg"}`}>
                {entry.action}
              </span>
              <span className="min-w-0 flex-1 truncate text-muted">
                {Object.entries(entry.details)
                  .filter(([, v]) => typeof v !== "object")
                  .map(([k, v]) => `${k}: ${String(v)}`)
                  .join(" · ")}
              </span>
              <span className="text-xs text-subtle">
                {formatDateTime(entry.created_at)}
                {entry.ip ? ` · ${entry.ip}` : ""}
              </span>
            </li>
          ))}
        </ol>
      )}
    </div>
  );
}
