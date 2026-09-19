"use client";

import { useRouter } from "next/navigation";
import { FormEvent, useEffect, useState } from "react";
import Workspace from "../components/shell/Workspace";
import { api, errorMessage } from "../lib/api";
import { SessionProvider, useSession } from "../lib/session";
import type { WorkspaceSummary } from "../lib/types";

function Gate() {
  const session = useSession();
  const router = useRouter();

  useEffect(() => {
    if (session.state.status === "anonymous") router.replace("/signin?next=/workspace");
  }, [session.state.status, router]);

  if (session.state.status !== "ready") {
    return (
      <main className="shell">
        <p className="gateMessage" role="status">Checking session…</p>
      </main>
    );
  }
  if (!session.state.workspace) return <NoWorkspace />;
  // Keyed by workspace: switching workspaces resets every view and cache.
  return <Workspace key={session.state.workspace.id} workspace={session.state.workspace} />;
}

function NoWorkspace() {
  const session = useSession();
  const [name, setName] = useState("");
  const [error, setError] = useState("");

  async function create(event: FormEvent) {
    event.preventDefault();
    try {
      const created = await api<WorkspaceSummary>("/api/v1/workspaces", { method: "POST", json: { name } });
      await session.refresh();
      session.selectWorkspace(created.id);
    } catch (err) {
      setError(errorMessage(err));
    }
  }

  return (
    <main className="shell authShell" id="main">
      <form className="panel authPanel" onSubmit={create}>
        <div className="panelLabel">No workspace</div>
        <h1>Create a workspace to continue.</h1>
        <label className="field">
          <span>Workspace name</span>
          <input className="textInput" value={name} required maxLength={120} onChange={(e) => setName(e.target.value)} />
        </label>
        {error && <p className="error" role="alert">{error}</p>}
        <button type="submit">Create workspace</button>
      </form>
    </main>
  );
}

export default function WorkspacePage() {
  return (
    <SessionProvider>
      <Gate />
    </SessionProvider>
  );
}
