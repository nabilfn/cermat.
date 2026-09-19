"use client";

import { useEffect, useId, useRef, useState } from "react";

/**
 * Native <dialog> confirmation: focus is trapped, Escape cancels.
 * With `confirmText`, the user must type it exactly (e.g. a workspace name).
 */
export function ConfirmDialog({
  open,
  title,
  body,
  confirmLabel,
  confirmText,
  busy = false,
  error,
  onConfirm,
  onCancel,
}: {
  open: boolean;
  title: string;
  body: React.ReactNode;
  confirmLabel: string;
  confirmText?: string;
  busy?: boolean;
  error?: string;
  onConfirm: () => void;
  onCancel: () => void;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  const [typed, setTyped] = useState("");
  const titleId = useId();

  useEffect(() => {
    const dialog = ref.current;
    if (!dialog) return;
    if (open && !dialog.open) dialog.showModal();
    if (!open && dialog.open) dialog.close();
  }, [open]);

  const blocked = busy || (confirmText !== undefined && typed !== confirmText);

  return (
    <dialog
      ref={ref}
      className="confirmDialog"
      aria-labelledby={titleId}
      onCancel={(event) => {
        event.preventDefault();
        if (!busy) onCancel();
      }}
      onClose={() => setTyped("")}
    >
      <form
        method="dialog"
        onSubmit={(event) => {
          event.preventDefault();
          if (!blocked) onConfirm();
        }}
      >
        <div className="panelLabel">Confirm</div>
        <h2 id={titleId}>{title}</h2>
        <div className="confirmBody">{body}</div>
        {confirmText !== undefined && (
          <label className="field">
            <span>Type “{confirmText}” to confirm</span>
            <input className="textInput" value={typed} onChange={(e) => setTyped(e.target.value)} autoComplete="off" />
          </label>
        )}
        {error && <p className="error" role="alert">{error}</p>}
        <div className="confirmActions">
          <button type="button" className="secondaryButton" onClick={onCancel} disabled={busy}>
            Cancel
          </button>
          <button type="submit" className="dangerButton" disabled={blocked}>
            {busy ? "Working…" : confirmLabel}
          </button>
        </div>
      </form>
    </dialog>
  );
}
