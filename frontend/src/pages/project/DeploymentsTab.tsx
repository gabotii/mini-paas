import { History } from "lucide-react";
import { useEffect } from "react";

import { DeploymentCard } from "../../components/DeploymentCard";
import { EmptyState } from "../../components/ErrorState";
import { LoadingState } from "../../components/LoadingState";
import { useDeployments } from "../../stores/deployments";
import { toast } from "../../stores/toast";
import { useProject } from "./context";

export function DeploymentsTab() {
  const project = useProject();
  const history = useDeployments((s) => s.history[project.id]);
  const fetchHistory = useDeployments((s) => s.fetchHistory);

  useEffect(() => {
    fetchHistory(project.id).catch((error: unknown) =>
      toast.error("Could not load deployments", (error as Error).message),
    );
  }, [project.id, fetchHistory]);

  if (!history || (history.loading && history.items.length === 0))
    return <LoadingState label="Loading history…" />;
  if (history.items.length === 0)
    return (
      <EmptyState
        icon={<History className="h-8 w-8 text-subtle" aria-hidden />}
        title="No deployments yet"
        description="Deploy the project to see its history here."
      />
    );

  return (
    <section className="flex flex-col gap-3">
      <p className="text-sm text-muted">
        {history.total} deployment{history.total === 1 ? "" : "s"} · open one to see its logs, timings and to
        roll back.
      </p>
      <div className="card divide-y divide-line overflow-hidden">
        {history.items.map((deployment) => (
          <DeploymentCard
            key={deployment.id}
            deployment={deployment}
            isCurrent={deployment.id === project.current_deployment?.id && project.status === "RUNNING"}
          />
        ))}
      </div>
    </section>
  );
}
