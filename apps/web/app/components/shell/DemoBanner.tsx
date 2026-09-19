"use client";

import { useState } from "react";
import { api, errorMessage } from "../../lib/api";
import { useSession } from "../../lib/session";
import type { WorkspaceSummary } from "../../lib/types";
import { ConfirmDialog } from "../shared/ConfirmDialog";

/** Always visible in a demo workspace so sample records are never mistaken for real ones. */
export default function DemoBanner({ workspace }: { workspace: WorkspaceSummary }) {
  const session = useSession();
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const real = session.state.status === "ready" ? session.state.workspaces.find((w) => !w.is_demo) : undefined;

  async function reset() {
    setBusy(true);
    setError("");
    try {
      await api(`/api/v1/workspaces/${workspace.id}/demo/reset`, { method: "POST" });
      setConfirming(false);
      window.location.reload();
    } catch (err) {
      setError(errorMessage(err, "Could not reset the demo."));
      setBusy(false);
    }
  }

  return (
    <div className="demoBanner" role="note">
      <strong>Demo data</strong>
      <span>Sample records for exploring cermat. Nothing here is real, and your own workspace is untouched.</span>
      <span className="demoActions">
        {workspace.role === "owner" && (
          <button type="button" className="askTextButton" onClick={() => setConfirming(true)}>
            Reset demo
          </button>
        )}
        {real && (
          <button type="button" className="askTextButton" onClick={() => session.selectWorkspace(real.id)}>
            Back to {real.name}
          </button>
        )}
      </span>
      <ConfirmDialog
        open={confirming}
        title="Reset the demo workspace?"
        body={<p>Every record in this demo workspace is deleted and the sample data is recreated. Other workspaces are not affected.</p>}
        confirmLabel="Reset demo"
        busy={busy}
        error={error}
        onConfirm={() => void reset()}
        onCancel={() => setConfirming(false)}
      />
    </div>
  );
}
