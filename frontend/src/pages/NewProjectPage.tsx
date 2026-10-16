import { ChevronDown, GitBranch, Package } from "lucide-react";
import { type FormEvent, useState } from "react";
import { useNavigate } from "react-router";

import { Button } from "../components/Button";
import { UploadDropzone } from "../components/UploadDropzone";
import { RUNTIMES, RUNTIME_LABELS } from "../lib/status";
import { SLUG_RE, validateArchive } from "../lib/validation";
import { ApiError, api } from "../services/api";
import { useDeployments } from "../stores/deployments";
import { useProjects } from "../stores/projects";
import { toast } from "../stores/toast";
import type { Runtime, SourceType } from "../types/api";

export function NewProjectPage() {
  const navigate = useNavigate();
  const create = useProjects((s) => s.create);
  const limits = useProjects((s) => s.limits);
  const track = useDeployments((s) => s.track);

  const [name, setName] = useState("");
  const [slug, setSlug] = useState("");
  const [description, setDescription] = useState("");
  const [runtime, setRuntime] = useState<Runtime>("auto");
  const [source, setSource] = useState<SourceType>("zip");
  const [repo, setRepo] = useState("");
  const [branch, setBranch] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [advanced, setAdvanced] = useState(false);
  const [port, setPort] = useState("");
  const [startCommand, setStartCommand] = useState("");
  const [healthPath, setHealthPath] = useState("/");
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);
  const maxMb = limits?.max_upload_mb ?? 100;

  const validate = () => {
    const found: Record<string, string> = {};
    if (!name.trim()) found.name = "Name is required";
    if (slug && !SLUG_RE.test(slug)) found.slug = "3–40 lowercase letters, digits or single hyphens";
    if (source === "git" && !/^https:\/\/\S+$/.test(repo))
      found.repository_url = "Enter a public https:// repository URL";
    if (source === "zip") {
      const problem = file ? validateArchive(file, maxMb) : "Choose a .zip archive to deploy";
      if (problem) found.file = problem;
    }
    if (port && !(Number(port) >= 1 && Number(port) <= 65535)) found.port = "Port must be 1–65535";
    if (healthPath && !healthPath.startsWith("/")) found.health_check_path = "Must start with /";
    return found;
  };

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    const found = validate();
    setErrors(found);
    if (Object.keys(found).length) return;
    setBusy(true);
    try {
      const project = await create({
        name: name.trim(),
        ...(slug ? { slug } : {}),
        ...(description.trim() ? { description: description.trim() } : {}),
        runtime,
        source_type: source,
        ...(source === "git"
          ? { repository_url: repo.trim(), repository_branch: branch.trim() || undefined }
          : {}),
        port: port ? Number(port) : null,
        start_command: startCommand.trim() || null,
        health_check_path: healthPath || "/",
      });
      const deployment =
        source === "zip" && file ? await api.upload(project.id, file) : await api.deploy(project.id);
      track(deployment);
      toast.success(`${project.name} created`, `Deployment #${deployment.number} queued`);
      navigate(`/projects/${project.id}/logs`);
    } catch (err) {
      if (err instanceof ApiError) {
        const fields = err.fieldErrors();
        setErrors(Object.keys(fields).length ? fields : { form: err.message });
      } else {
        setErrors({ form: "Could not create the project" });
      }
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="mx-auto flex max-w-2xl flex-col gap-6">
      <header>
        <h1 className="text-2xl font-semibold tracking-tight">New project</h1>
        <p className="mt-1 text-sm text-muted">
          Every project gets its own subdomain and an isolated container.
        </p>
      </header>

      <form onSubmit={submit} className="card flex flex-col gap-5 p-5 sm:p-6" noValidate>
        <div className="grid gap-4 sm:grid-cols-2">
          <label className="sm:col-span-1">
            <span className="label">Project name</span>
            <input
              className="input"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="my-api"
            />
            {errors.name && <p className="field-error">{errors.name}</p>}
          </label>
          <label>
            <span className="label">Subdomain (optional)</span>
            <input
              className="input font-mono"
              value={slug}
              onChange={(e) => setSlug(e.target.value.toLowerCase())}
              placeholder="derived from the name"
            />
            {errors.slug && <p className="field-error">{errors.slug}</p>}
          </label>
        </div>

        <label>
          <span className="label">Description</span>
          <textarea
            className="input min-h-20"
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            maxLength={500}
          />
        </label>

        <fieldset>
          <legend className="label">Runtime</legend>
          <div className="grid grid-cols-2 gap-2 sm:grid-cols-3">
            {RUNTIMES.map((r) => (
              <label
                key={r}
                className={`flex cursor-pointer items-center justify-center rounded-lg border px-3 py-2.5 text-sm transition-colors ${
                  runtime === r
                    ? "border-accent bg-accent/10 text-fg"
                    : "border-line text-muted hover:border-line-strong"
                }`}
              >
                <input
                  type="radio"
                  name="runtime"
                  value={r}
                  checked={runtime === r}
                  onChange={() => setRuntime(r)}
                  className="sr-only"
                />
                {RUNTIME_LABELS[r]}
              </label>
            ))}
          </div>
          <p className="hint">
            Auto-detect looks for a Dockerfile, a .csproj, package.json, requirements.txt/pyproject.toml or
            index.html.
          </p>
        </fieldset>

        <fieldset>
          <legend className="label">Source</legend>
          <div className="grid grid-cols-2 gap-2">
            {(
              [
                ["zip", "Upload ZIP", Package],
                ["git", "Git repository", GitBranch],
              ] as const
            ).map(([value, label, Icon]) => (
              <label
                key={value}
                className={`flex cursor-pointer items-center justify-center gap-2 rounded-lg border px-3 py-2.5 text-sm transition-colors ${
                  source === value
                    ? "border-accent bg-accent/10 text-fg"
                    : "border-line text-muted hover:border-line-strong"
                }`}
              >
                <input
                  type="radio"
                  name="source"
                  value={value}
                  checked={source === value}
                  onChange={() => setSource(value)}
                  className="sr-only"
                />
                <Icon className="h-4 w-4" aria-hidden /> {label}
              </label>
            ))}
          </div>
        </fieldset>

        {source === "zip" ? (
          <UploadDropzone file={file} onFile={setFile} maxMb={maxMb} error={errors.file} />
        ) : (
          <div className="grid gap-4 sm:grid-cols-[1fr_12rem]">
            <label>
              <span className="label">Repository URL</span>
              <input
                className="input font-mono"
                value={repo}
                onChange={(e) => setRepo(e.target.value)}
                placeholder="https://github.com/user/repo"
              />
              {errors.repository_url && <p className="field-error">{errors.repository_url}</p>}
            </label>
            <label>
              <span className="label">Branch</span>
              <input
                className="input font-mono"
                value={branch}
                onChange={(e) => setBranch(e.target.value)}
                placeholder="default"
              />
              {errors.repository_branch && <p className="field-error">{errors.repository_branch}</p>}
            </label>
          </div>
        )}

        <button
          type="button"
          onClick={() => setAdvanced((v) => !v)}
          className="inline-flex w-fit items-center gap-1 text-sm text-muted hover:text-fg"
          aria-expanded={advanced}
        >
          <ChevronDown
            className={`h-4 w-4 transition-transform ${advanced ? "rotate-180" : ""}`}
            aria-hidden
          />{" "}
          Advanced settings
        </button>
        {advanced && (
          <div className="grid gap-4 sm:grid-cols-3">
            <label>
              <span className="label">Port</span>
              <input
                className="input"
                inputMode="numeric"
                value={port}
                onChange={(e) => setPort(e.target.value.replace(/\D/g, ""))}
                placeholder="auto"
              />
              {errors.port && <p className="field-error">{errors.port}</p>}
            </label>
            <label className="sm:col-span-2">
              <span className="label">Start command</span>
              <input
                className="input font-mono"
                value={startCommand}
                onChange={(e) => setStartCommand(e.target.value)}
                placeholder="auto-detected"
              />
              {errors.start_command && <p className="field-error">{errors.start_command}</p>}
            </label>
            <label className="sm:col-span-3">
              <span className="label">Health check path</span>
              <input
                className="input font-mono"
                value={healthPath}
                onChange={(e) => setHealthPath(e.target.value)}
              />
              {errors.health_check_path && <p className="field-error">{errors.health_check_path}</p>}
            </label>
          </div>
        )}

        {errors.form && (
          <p role="alert" className="rounded-lg border border-bad/30 bg-bad/10 px-3 py-2 text-sm text-bad">
            {errors.form}
          </p>
        )}
        <div className="flex flex-col-reverse gap-2 sm:flex-row sm:justify-end">
          <Button onClick={() => navigate(-1)}>Cancel</Button>
          <Button variant="primary" type="submit" loading={busy}>
            Create &amp; deploy
          </Button>
        </div>
      </form>
    </div>
  );
}
