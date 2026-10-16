import { useState } from "react";

import { Button } from "./Button";
import { Modal } from "./Modal";

interface ConfirmDialogProps {
  open: boolean;
  title: string;
  description: string;
  confirmLabel: string;
  danger?: boolean;
  /** When set, the user must type this text to enable the confirm button. */
  confirmText?: string;
  onConfirm: () => Promise<void> | void;
  onClose: () => void;
}

export function ConfirmDialog({
  open,
  title,
  description,
  confirmLabel,
  danger = false,
  confirmText,
  onConfirm,
  onClose,
}: ConfirmDialogProps) {
  const [busy, setBusy] = useState(false);
  const [typed, setTyped] = useState("");
  const blocked = confirmText !== undefined && typed !== confirmText;

  const confirm = async () => {
    setBusy(true);
    try {
      await onConfirm();
      setTyped("");
      onClose();
    } finally {
      setBusy(false);
    }
  };

  return (
    <Modal
      open={open}
      title={title}
      onClose={busy ? () => undefined : onClose}
      footer={
        <>
          <Button onClick={onClose} disabled={busy}>
            Cancel
          </Button>
          <Button variant={danger ? "danger" : "primary"} loading={busy} disabled={blocked} onClick={confirm}>
            {confirmLabel}
          </Button>
        </>
      }
    >
      <p className="text-sm text-muted">{description}</p>
      {confirmText !== undefined && (
        <label className="mt-4 block">
          <span className="label">
            Type <code className="rounded bg-raised px-1.5 py-0.5 font-mono text-xs">{confirmText}</code> to
            confirm
          </span>
          <input
            className="input font-mono"
            value={typed}
            onChange={(e) => setTyped(e.target.value)}
            autoComplete="off"
          />
        </label>
      )}
    </Modal>
  );
}
