import { useCallback, useState } from "react";
import { useNavigate } from "react-router";

import { ApiError, api } from "../services/api";
import { useDeployments } from "../stores/deployments";
import { useProjects } from "../stores/projects";
import { toast } from "../stores/toast";
import type { Deployment, Project } from "../types/api";

/** Deployment and lifecycle actions for one project, with toasts and optimistic tracking. */
export function useDeployActions(project: Project) {
  const [busy, setBusy] = useState(false);
  const navigate = useNavigate();
  const track = useDeployments((s) => s.track);
  const fetchOne = useProjects((s) => s.fetchOne);

  const perform = useCallback(
    async (action: () => Promise<unknown>, success: string | null): Promise<boolean> => {
      setBusy(true);
      try {
        await action();
        if (success) toast.success(success);
        await fetchOne(project.id).catch(() => undefined);
        return true;
      } catch (error) {
        toast.error("Action failed", error instanceof ApiError ? error.message : "Something went wrong");
        return false;
      } finally {
        setBusy(false);
      }
    },
    [fetchOne, project.id],
  );

  /** Actions that create a deployment: track it and jump to the live logs. */
  const launch = useCallback(
    (action: () => Promise<Deployment>, describe: (d: Deployment) => string) =>
      perform(async () => {
        const deployment = await action();
        track(deployment);
        toast.success(describe(deployment));
        navigate(`/projects/${project.id}/logs`);
      }, null),
    [navigate, perform, project.id, track],
  );

  return {
    busy,
    deploy: (branch?: string) =>
      launch(
        () => api.deploy(project.id, branch),
        (d) => `Deployment #${d.number} queued`,
      ),
    upload: (file: File) =>
      launch(
        () => api.upload(project.id, file),
        (d) => `Uploaded — deployment #${d.number} queued`,
      ),
    start: () =>
      launch(
        () => api.start(project.id),
        (d) => `Starting from deployment #${d.number}`,
      ),
    rollback: (deploymentId: string) =>
      launch(
        () => api.rollback(deploymentId),
        (d) => `Rollback queued as deployment #${d.number}`,
      ),
    stop: () => perform(() => api.stop(project.id), "Stopping application"),
    restart: () => perform(() => api.restart(project.id), "Restarting application"),
    cancel: (deploymentId: string) => perform(() => api.cancel(deploymentId), "Cancellation requested"),
  };
}
