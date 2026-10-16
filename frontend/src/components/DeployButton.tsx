import { Rocket } from "lucide-react";
import { useState } from "react";

import { useDeployActions } from "../hooks/useDeployActions";
import { useProjects } from "../stores/projects";
import type { Project } from "../types/api";
import { Button } from "./Button";
import { Modal } from "./Modal";
import { validateArchive } from "../lib/validation";
import { UploadDropzone } from "./UploadDropzone";

export function DeployButton({ project, size = "md" }: { project: Project; size?: "sm" | "md" }) {
  const maxMb = useProjects((s) => s.limits?.max_upload_mb ?? 100);
  const { deploy, upload, busy } = useDeployActions(project);
  const [open, setOpen] = useState(false);
  const [file, setFile] = useState<File | null>(null);
  const [error, setError] = useState<string | null>(null);
  const inProgress = Boolean(project.active_deployment);

  const onClick = () => {
    if (project.source_type === "git") void deploy();
    else setOpen(true);
  };

  const submitUpload = async () => {
    if (!file) return setError("Choose a .zip archive");
    const problem = validateArchive(file, maxMb);
    if (problem) return setError(problem);
    if (await upload(file)) close();
  };

  const redeploy = async () => {
    if (await deploy()) close();
  };

  const close = () => {
    setOpen(false);
    setFile(null);
    setError(null);
  };

  return (
    <>
      <Button
        variant="primary"
        size={size}
        icon={<Rocket className="h-4 w-4" />}
        onClick={onClick}
        loading={busy && !open}
        disabled={inProgress}
        title={inProgress ? "A deployment is already in progress" : undefined}
      >
        {inProgress ? "Deploying…" : "Deploy"}
      </Button>
      <Modal
        open={open}
        onClose={close}
        title={`Deploy ${project.name}`}
        footer={
          <>
            {project.has_source && (
              <Button onClick={redeploy} loading={busy && !file} disabled={busy}>
                Redeploy last upload
              </Button>
            )}
            <Button
              variant="primary"
              onClick={submitUpload}
              loading={busy && Boolean(file)}
              disabled={!file || busy}
            >
              Upload &amp; deploy
            </Button>
          </>
        }
      >
        <UploadDropzone
          file={file}
          onFile={(f) => {
            setFile(f);
            setError(f ? validateArchive(f, maxMb) : null);
          }}
          maxMb={maxMb}
          error={error}
        />
      </Modal>
    </>
  );
}
