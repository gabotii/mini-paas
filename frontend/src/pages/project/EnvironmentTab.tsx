import { useCallback, useEffect, useState } from "react";

import { EnvironmentTable } from "../../components/EnvironmentTable";
import { ErrorState } from "../../components/ErrorState";
import { LoadingState } from "../../components/LoadingState";
import { api } from "../../services/api";
import type { EnvVar } from "../../types/api";
import { useProject } from "./context";

export function EnvironmentTab() {
  const project = useProject();
  const [variables, setVariables] = useState<EnvVar[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(
    () =>
      api.env(project.id).then(
        (rows) => {
          setVariables(rows);
          setError(null);
        },
        (err: unknown) => setError((err as Error).message),
      ),
    [project.id],
  );

  useEffect(() => {
    void load();
  }, [load]);

  if (error) return <ErrorState message={error} onRetry={() => void load()} />;
  if (!variables) return <LoadingState label="Loading variables…" />;
  return <EnvironmentTable projectId={project.id} variables={variables} onChange={load} />;
}
