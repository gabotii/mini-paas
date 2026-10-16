import { type FormEvent, useState } from "react";
import { useNavigate } from "react-router";

import { Button } from "../../components/Button";
import { ConfirmDialog } from "../../components/ConfirmDialog";
import { RUNTIMES, RUNTIME_LABELS } from "../../lib/status";
import { ApiError } from "../../services/api";
import { useProjects } from "../../stores/projects";
import { toast } from "../../stores/toast";
import { useProject } from "./context";

export function SettingsTab() {
  const project = useProject();
  const update = useProjects((s) => s.update);
  const remove = useProjects((s) => s.remove);
  const limits = useProjects((s) => s.limits);
  const navigate = useNavigate();

  const [form, setForm] = useState({
    name: project.name,
    description: project.description ?? "",
    runtime: project.runtime,
    repository_url: project.repository_url ?? "",
    repository_branch: project.repository_branch ?? "",
    port: project.port ? String(project.port) : "",
    start_command: project.start_command ?? "",
    health_check_path: project.health_check_path,
    cpu_limit: String(project.cpu_limit),
    memory_limit_mb: String(project.memory_limit_mb),
  });
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const set = (key: keyof typeof form) => (event: { target: { value: string } }) =>
    setForm((f) => ({ ...f, [key]: event.target.value }));

  const save = async (event: FormEvent) => {
    event.preventDefault();
    setBusy(true);
    setErrors({});
    try {
      await update(project.id, {
        name: form.name,
        description: form.description || undefined,
        runtime: form.runtime,
        ...(project.source_type === "git"
          ? { repository_url: form.repository_url, repository_branch: form.repository_branch || undefined }
          : {}),
        port: form.port ? Number(form.port) : null,
        start_command: form.start_command || null,
        health_check_path: form.health_check_path || "/",
        cpu_limit: Number(form.cpu_limit),
        memory_limit_mb: Number(form.memory_limit_mb),
      });
      toast.success("Settings saved", "They apply to the next deployment");
    } catch (err) {
      if (err instanceof ApiError) {
        const fields = err.fieldErrors();
        setErrors(Object.keys(fields).length ? fields : { form: err.message });
      }
    } finally {
      setBusy(false);
    }
  };

  const field = (key: keyof typeof form, label: string, props: Record<string, unknown> = {}) => (
    <label>
      <span className="label">{label}</span>
      <input className="input" value={form[key]} onChange={set(key)} {...props} />
      {errors[key] && <p className="field-error">{errors[key]}</p>}
    </label>
  );

  return (
    <div className="flex flex-col gap-6">
      <form onSubmit={save} className="card flex flex-col gap-5 p-5 sm:p-6">
        <h2 className="text-base font-semibold">General</h2>
        <div className="grid gap-4 sm:grid-cols-2">
          {field("name", "Name")}
          <label>
            <span className="label">Runtime</span>
            <select className="input" value={form.runtime} onChange={set("runtime")}>
              {RUNTIMES.map((r) => (
                <option key={r} value={r}>
                  {RUNTIME_LABELS[r]}
                </option>
              ))}
            </select>
          </label>
          <label className="sm:col-span-2">
            <span className="label">Description</span>
            <textarea
              className="input min-h-20"
              value={form.description}
              onChange={set("description")}
              maxLength={500}
            />
          </label>
          {project.source_type === "git" && (
            <>
              {field("repository_url", "Repository URL", { className: "input font-mono" })}
              {field("repository_branch", "Branch", { className: "input font-mono", placeholder: "default" })}
            </>
          )}
        </div>

        <h2 className="pt-2 text-base font-semibold">Runtime</h2>
        <div className="grid gap-4 sm:grid-cols-3">
          {field("port", "Port", { inputMode: "numeric", placeholder: "auto" })}
          <div className="sm:col-span-2">
            {field("start_command", "Start command", {
              className: "input font-mono",
              placeholder: "auto-detected",
            })}
          </div>
          {field("health_check_path", "Health check path", { className: "input font-mono" })}
          {field("cpu_limit", `CPU (max ${limits?.cpu_max ?? 1})`, { inputMode: "decimal" })}
          {field("memory_limit_mb", `Memory MB (max ${limits?.memory_max_mb ?? 512})`, {
            inputMode: "numeric",
          })}
        </div>

        {errors.form && <p className="field-error">{errors.form}</p>}
        <div className="flex justify-end">
          <Button variant="primary" type="submit" loading={busy}>
            Save changes
          </Button>
        </div>
      </form>

      <section className="card flex flex-col gap-3 border-bad/30 p-5 sm:flex-row sm:items-center sm:justify-between sm:p-6">
        <div>
          <h2 className="text-base font-semibold text-bad">Delete project</h2>
          <p className="mt-1 text-sm text-muted">
            Stops the app and permanently removes its containers, images, logs, metrics and variables.
          </p>
        </div>
        <Button variant="danger" onClick={() => setConfirmDelete(true)}>
          Delete project
        </Button>
      </section>

      <ConfirmDialog
        open={confirmDelete}
        title="Delete project"
        description="This cannot be undone."
        confirmLabel="Delete forever"
        confirmText={project.slug}
        danger
        onConfirm={async () => {
          await remove(project.id);
          toast.success(`${project.name} is being deleted`);
          navigate("/", { replace: true });
        }}
        onClose={() => setConfirmDelete(false)}
      />
    </div>
  );
}
