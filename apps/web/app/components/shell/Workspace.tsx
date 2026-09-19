"use client";

import Link from "next/link";
import { useState } from "react";
import { useSession } from "../../lib/session";
import type { WorkspaceSummary } from "../../lib/types";
import AskWorkspace, { AskScope } from "../ask/AskWorkspace";
import SingleDocument from "../documents/SingleDocument";
import OverviewWorkspace from "../intelligence/OverviewWorkspace";
import ThreeWayMatch from "../reconciliation/ThreeWayMatch";
import HistoryWorkspace from "../reviews/HistoryWorkspace";
import AttentionIndicator from "./AttentionIndicator";
import DemoBanner from "./DemoBanner";
import WorkspacePanel from "./WorkspacePanel";

type Mode = "overview" | "document" | "transaction" | "history" | "ask";

const MODES: Array<{ id: Mode; label: string; hint: string }> = [
  { id: "overview", label: "Overview", hint: "Rules calculate · AI explains" },
  { id: "document", label: "Single document", hint: "Extract and inspect one source" },
  { id: "transaction", label: "Three-way match", hint: "PO ↔ DO ↔ Invoice" },
  { id: "history", label: "Review history", hint: "Open → Review → Resolved" },
  { id: "ask", label: "Ask cermat.", hint: "Questions → Records → Evidence" },
];

export default function Workspace({ workspace }: { workspace: WorkspaceSummary }) {
  const session = useSession();
  const [mode, setMode] = useState<Mode>("overview");
  const [askScope, setAskScope] = useState<AskScope>(null);
  const [historyFocusId, setHistoryFocusId] = useState<string | null>(null);
  const [supplierKey, setSupplierKey] = useState<string | null>(null);
  const [askRequest, setAskRequest] = useState<{ text: string; nonce: number } | null>(null);
  const [panelOpen, setPanelOpen] = useState(false);

  if (session.state.status !== "ready") return null;
  const { user, workspaces } = session.state;

  function openTransaction(id: string) {
    setHistoryFocusId(id);
    setMode("history");
  }

  function askAbout(question: string) {
    setAskScope(null);
    setAskRequest({ text: question, nonce: Date.now() });
    setMode("ask");
  }

  async function loadDemo() {
    await session.openDemo();
  }

  return (
    <main className="shell" id="main">
      <header className="topbar">
        <Link className="brand" href="/" aria-label="cermat. home">
          cermat.
        </Link>
        <div className="topbarRight">
          <label className="workspaceSwitch">
            <span className="visuallyHidden">Workspace</span>
            <select
              value={workspace.id}
              onChange={(event) => session.selectWorkspace(event.target.value)}
              aria-label="Switch workspace"
            >
              {workspaces.map((w) => (
                <option key={w.id} value={w.id}>
                  {w.is_demo ? `DEMO · ${w.name}` : w.name}
                </option>
              ))}
            </select>
          </label>
          <button type="button" className="topbarButton" onClick={() => setPanelOpen(true)}>
            Workspace
          </button>
          <AttentionIndicator
            onOpenTransaction={openTransaction}
            onSupplier={(key) => {
              setSupplierKey(key);
              setMode("overview");
            }}
            onOverview={() => {
              setSupplierKey(null);
              setMode("overview");
            }}
          />
          <details className="accountMenu">
            <summary aria-label={`Account: ${user.display_name}`}>{user.display_name}</summary>
            <div className="accountMenuBody">
              <span>{user.email}</span>
              <button type="button" className="askTextButton" onClick={() => void session.signOut()}>
                Sign out
              </button>
            </div>
          </details>
        </div>
      </header>

      {workspace.is_demo && <DemoBanner workspace={workspace} />}

      <section className="hero compact">
        <p className="eyebrow">AI operations intelligence</p>
        <h1>Read less. Catch more.</h1>
        <p className="lede">
          Turn purchase documents into structured records, cross-check the numbers, and surface exactly what
          needs human attention.
        </p>
      </section>

      <nav className="modeBar" aria-label="Workspace mode">
        {MODES.map((item) => (
          <button
            type="button"
            key={item.id}
            className={`modeButton ${mode === item.id ? "active" : ""}`}
            aria-current={mode === item.id ? "page" : undefined}
            onClick={() => {
              if (item.id === "overview") setSupplierKey(null);
              setMode(item.id);
            }}
          >
            {item.label}
          </button>
        ))}
        <span className="modeHint">{MODES.find((m) => m.id === mode)?.hint}</span>
      </nav>

      {mode === "overview" ? (
        <OverviewWorkspace
          supplierKey={supplierKey}
          onSupplier={setSupplierKey}
          onOpenTransaction={openTransaction}
          onAsk={askAbout}
          onStart={setMode}
          onLoadDemo={loadDemo}
          canEditSettings={workspace.role === "owner"}
          isDemo={workspace.is_demo}
        />
      ) : mode === "document" ? (
        <SingleDocument />
      ) : mode === "transaction" ? (
        <ThreeWayMatch onOpenTransaction={openTransaction} />
      ) : mode === "history" ? (
        <HistoryWorkspace
          focusId={historyFocusId}
          onStart={() => setMode("transaction")}
          onAsk={(transaction) => {
            setAskScope(transaction);
            setMode("ask");
          }}
        />
      ) : null}

      {/* Kept mounted so the Ask session survives switching modes. */}
      <div hidden={mode !== "ask"}>
        <AskWorkspace
          scope={askScope}
          onScope={setAskScope}
          onClearScope={() => setAskScope(null)}
          onOpenTransaction={openTransaction}
          request={askRequest}
        />
      </div>

      <WorkspacePanel open={panelOpen} workspace={workspace} onClose={() => setPanelOpen(false)} />
    </main>
  );
}
