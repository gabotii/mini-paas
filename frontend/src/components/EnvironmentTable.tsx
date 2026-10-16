import { Eye, EyeOff, KeyRound, Lock, Pencil, Plus, Trash2 } from "lucide-react";
import { type FormEvent, useState } from "react";

import { relativeTime } from "../lib/format";
import { ENV_KEY_RE } from "../lib/validation";
import { ApiError, api } from "../services/api";
import { toast } from "../stores/toast";
import type { EnvVar } from "../types/api";
import { Button } from "./Button";
import { ConfirmDialog } from "./ConfirmDialog";
import { EmptyState } from "./ErrorState";
import { Modal } from "./Modal";

interface EnvironmentTableProps {
  projectId: string;
  variables: EnvVar[];
  onChange: () => Promise<void>;
}

type Editing = { mode: "create" } | { mode: "edit"; variable: EnvVar };

export function EnvironmentTable({ projectId, variables, onChange }: EnvironmentTableProps) {
  const [revealed, setRevealed] = useState<Record<string, string>>({});
  const [editing, setEditing] = useState<Editing | null>(null);
  const [deleting, setDeleting] = useState<EnvVar | null>(null);

  const toggle = async (variable: EnvVar) => {
    if (revealed[variable.key] !== undefined) {
      setRevealed(({ [variable.key]: _, ...rest }) => rest);
      return;
    }
    if (!variable.is_secret) {
      setRevealed((r) => ({ ...r, [variable.key]: variable.value }));
      return;
    }
    try {
      const { value } = await api.revealEnv(projectId, variable.key);
      setRevealed((r) => ({ ...r, [variable.key]: value }));
    } catch (error) {
      toast.error("Could not reveal value", (error as Error).message);
    }
  };

  const remove = async () => {
    if (!deleting) return;
    await api.deleteEnv(projectId, deleting.key);
    toast.success(`${deleting.key} deleted`, "Redeploy to apply the change");
    await onChange();
  };

  return (
    <section className="flex flex-col gap-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <p className="text-sm text-muted">
          Values are encrypted at rest. Changes take effect on the next deploy or restart from a new
          deployment.
        </p>
        <Button
          variant="primary"
          size="sm"
          icon={<Plus className="h-4 w-4" />}
          onClick={() => setEditing({ mode: "create" })}
        >
          Add variable
        </Button>
      </div>

      {variables.length === 0 ? (
        <EmptyState
          icon={<KeyRound className="h-8 w-8 text-subtle" aria-hidden />}
          title="No environment variables"
          description="Add DATABASE_URL, SECRET_KEY, API keys… Set PORT to tell the platform which port your app listens on."
        />
      ) : (
        <div className="card divide-y divide-line overflow-hidden" data-testid="env-table">
          {variables.map((variable) => {
            const shown = revealed[variable.key];
            return (
              <div
                key={variable.key}
                className="flex flex-col gap-2 px-4 py-3 sm:flex-row sm:items-center sm:gap-4"
              >
                <div className="flex min-w-0 items-center gap-2 sm:w-64 sm:shrink-0">
                  {variable.is_secret && (
                    <Lock className="h-3.5 w-3.5 shrink-0 text-subtle" aria-label="secret" />
                  )}
                  <code className="truncate font-mono text-sm">{variable.key}</code>
                </div>
                <code
                  className="min-w-0 flex-1 truncate rounded-md bg-canvas px-2 py-1 font-mono text-xs text-muted"
                  data-testid={`env-value-${variable.key}`}
                >
                  {shown ?? variable.value}
                </code>
                <div className="flex items-center justify-between gap-1 sm:justify-end">
                  <span className="mr-2 text-xs text-subtle">{relativeTime(variable.updated_at)}</span>
                  <IconButton
                    label={shown !== undefined ? `Hide ${variable.key}` : `Show ${variable.key}`}
                    onClick={() => toggle(variable)}
                  >
                    {shown !== undefined ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
                  </IconButton>
                  <IconButton
                    label={`Edit ${variable.key}`}
                    onClick={() => setEditing({ mode: "edit", variable })}
                  >
                    <Pencil className="h-4 w-4" />
                  </IconButton>
                  <IconButton label={`Delete ${variable.key}`} onClick={() => setDeleting(variable)}>
                    <Trash2 className="h-4 w-4" />
                  </IconButton>
                </div>
              </div>
            );
          })}
        </div>
      )}

      <EnvForm
        projectId={projectId}
        editing={editing}
        onClose={() => setEditing(null)}
        onSaved={async (key) => {
          setRevealed(({ [key]: _, ...rest }) => rest);
          await onChange();
        }}
      />
      <ConfirmDialog
        open={deleting !== null}
        title="Delete variable"
        description={`Delete ${deleting?.key ?? ""}? Running deployments keep their current value until the next deploy.`}
        confirmLabel="Delete"
        danger
        onConfirm={remove}
        onClose={() => setDeleting(null)}
      />
    </section>
  );
}

function IconButton({
  label,
  onClick,
  children,
}: {
  label: string;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      onClick={onClick}
      aria-label={label}
      title={label}
      className="inline-flex h-9 w-9 items-center justify-center rounded-md text-muted hover:bg-raised hover:text-fg"
    >
      {children}
    </button>
  );
}

function EnvForm({
  projectId,
  editing,
  onClose,
  onSaved,
}: {
  projectId: string;
  editing: Editing | null;
  onClose: () => void;
  onSaved: (key: string) => Promise<void>;
}) {
  const isEdit = editing?.mode === "edit";
  const initial = isEdit ? editing.variable : null;
  const [key, setKey] = useState("");
  const [value, setValue] = useState("");
  const [secret, setSecret] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [lastEditing, setLastEditing] = useState<Editing | null>(null);

  // Reset the form whenever a different variable (or "create") is opened.
  if (editing !== lastEditing) {
    setLastEditing(editing);
    setKey(initial?.key ?? "");
    setValue("");
    setSecret(initial?.is_secret ?? true);
    setError(null);
  }

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    const trimmed = key.trim();
    if (!isEdit && !ENV_KEY_RE.test(trimmed)) {
      setError("Use letters, digits and underscores; do not start with a digit");
      return;
    }
    if (!isEdit && !value) {
      setError("Value is required");
      return;
    }
    setBusy(true);
    try {
      if (isEdit) {
        await api.updateEnv(projectId, trimmed, { ...(value ? { value } : {}), is_secret: secret });
      } else {
        await api.createEnv(projectId, { key: trimmed, value, is_secret: secret });
      }
      toast.success(`${trimmed} saved`, "Redeploy to apply the change");
      await onSaved(trimmed);
      onClose();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not save the variable");
    } finally {
      setBusy(false);
    }
  };

  return (
    <Modal
      open={editing !== null}
      onClose={onClose}
      title={isEdit ? `Edit ${initial?.key}` : "Add environment variable"}
      footer={
        <>
          <Button onClick={onClose}>Cancel</Button>
          <Button variant="primary" type="submit" form="env-form" loading={busy}>
            Save
          </Button>
        </>
      }
    >
      <form id="env-form" onSubmit={submit} className="flex flex-col gap-4">
        <label>
          <span className="label">Key</span>
          <input
            className="input font-mono uppercase"
            value={key}
            onChange={(e) => setKey(e.target.value.toUpperCase())}
            placeholder="DATABASE_URL"
            disabled={isEdit}
            autoComplete="off"
            spellCheck={false}
          />
        </label>
        <label>
          <span className="label">Value</span>
          <textarea
            className="input min-h-24 font-mono"
            value={value}
            onChange={(e) => setValue(e.target.value)}
            placeholder={isEdit ? "Leave empty to keep the current value" : "value"}
            autoComplete="off"
            spellCheck={false}
          />
        </label>
        <label className="flex items-center gap-2 text-sm">
          <input
            type="checkbox"
            checked={secret}
            onChange={(e) => setSecret(e.target.checked)}
            className="h-4 w-4 accent-[var(--color-accent)]"
          />
          Secret — mask the value in the dashboard and API
        </label>
        {error && <p className="field-error">{error}</p>}
      </form>
    </Modal>
  );
}
