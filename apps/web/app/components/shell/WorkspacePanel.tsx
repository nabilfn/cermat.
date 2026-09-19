"use client";

import { FormEvent, useEffect, useRef, useState } from "react";
import { api, errorMessage } from "../../lib/api";
import { dateTimeLabel } from "../../lib/format";
import { useSession } from "../../lib/session";
import type { AuditPage, MemberRecord, WorkspaceSummary } from "../../lib/types";
import { useResource } from "../../lib/useResource";
import { ConfirmDialog } from "../shared/ConfirmDialog";

const ACTION_LABEL: Record<string, string> = {
  workspace_created: "Workspace created",
  workspace_renamed: "Workspace renamed",
  document_uploaded: "Document uploaded",
  document_extracted: "Document extracted",
  document_extraction_failed: "Extraction failed",
  document_deleted: "Document deleted",
  transaction_created: "Transaction created",
  document_attached: "Document linked",
  reconciliation_run: "Reconciliation run",
  transaction_deleted: "Transaction deleted",
  issue_resolved: "Issue resolved",
  issue_reopened: "Issue reopened",
  settings_changed: "Thresholds changed",
  attention_dismissed: "Attention dismissed",
  member_invited: "Member added",
  member_removed: "Member removed",
  demo_seeded: "Demo data loaded",
  demo_reset: "Demo reset",
  export_downloaded: "CSV exported",
};

export default function WorkspacePanel({
  open,
  workspace,
  onClose,
}: {
  open: boolean;
  workspace: WorkspaceSummary;
  onClose: () => void;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  const session = useSession();
  const owner = workspace.role === "owner";

  useEffect(() => {
    const dialog = ref.current;
    if (!dialog) return;
    if (open && !dialog.open) dialog.showModal();
    if (!open && dialog.open) dialog.close();
  }, [open]);

  const members = useResource<MemberRecord[]>(open ? `members:${workspace.id}` : null, (signal) =>
    api<MemberRecord[]>(`/api/v1/workspaces/${workspace.id}/members`, { signal })
  );
  const audit = useResource<AuditPage>(open ? `audit:${workspace.id}` : null, (signal) =>
    api<AuditPage>("/api/v1/audit?limit=15", { signal })
  );

  const [name, setName] = useState(workspace.name);
  const [invite, setInvite] = useState("");
  const [newWorkspace, setNewWorkspace] = useState("");
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [deleteError, setDeleteError] = useState("");

  async function act(action: () => Promise<unknown>, done: string) {
    setError("");
    setMessage("");
    try {
      await action();
      setMessage(done);
    } catch (err) {
      setError(errorMessage(err));
    }
  }

  async function rename(event: FormEvent) {
    event.preventDefault();
    await act(async () => {
      await api(`/api/v1/workspaces/${workspace.id}`, { method: "PATCH", json: { name } });
      await session.refresh();
    }, "Workspace renamed.");
  }

  async function addMember(event: FormEvent) {
    event.preventDefault();
    await act(async () => {
      await api(`/api/v1/workspaces/${workspace.id}/members`, { method: "POST", json: { email: invite, role: "member" } });
      setInvite("");
      members.reload();
      audit.reload();
    }, "Member added.");
  }

  async function removeMember(member: MemberRecord) {
    await act(async () => {
      await api(`/api/v1/workspaces/${workspace.id}/members/${member.user_id}`, { method: "DELETE" });
      members.reload();
      audit.reload();
    }, `${member.display_name} removed.`);
  }

  async function createWorkspace(event: FormEvent) {
    event.preventDefault();
    await act(async () => {
      const created = await api<WorkspaceSummary>("/api/v1/workspaces", { method: "POST", json: { name: newWorkspace } });
      await session.refresh();
      session.selectWorkspace(created.id);
      onClose();
    }, "Workspace created.");
  }

  async function deleteWorkspace() {
    setDeleting(true);
    setDeleteError("");
    try {
      await api(`/api/v1/workspaces/${workspace.id}`, { method: "DELETE", json: { confirm_name: workspace.name } });
      setConfirmDelete(false);
      onClose();
      await session.refresh();
    } catch (err) {
      setDeleteError(errorMessage(err, "Could not delete the workspace."));
    } finally {
      setDeleting(false);
    }
  }

  return (
    <dialog
      ref={ref}
      className="workspacePanel"
      aria-labelledby="workspace-panel-title"
      onCancel={(event) => {
        event.preventDefault();
        onClose();
      }}
    >
      <div className="workspacePanelHead">
        <div>
          <div className="panelLabel">Workspace · {workspace.role}</div>
          <h2 id="workspace-panel-title" className="breakWord">{workspace.name}</h2>
        </div>
        <button type="button" className="askTextButton" onClick={onClose}>
          Close
        </button>
      </div>

      {(message || error) && (
        <p className={error ? "error" : "panelMessage"} role={error ? "alert" : "status"}>
          {error || message}
        </p>
      )}

      {owner && !workspace.is_demo && (
        <form className="panelSection inlineForm" onSubmit={rename}>
          <label className="field">
            <span>Name</span>
            <input className="textInput" value={name} maxLength={120} required onChange={(e) => setName(e.target.value)} />
          </label>
          <button type="submit" className="secondaryButton" disabled={!name.trim() || name.trim() === workspace.name}>
            Rename
          </button>
        </form>
      )}

      <section className="panelSection" aria-labelledby="members-title">
        <div className="sectionTitleRow">
          <span id="members-title">Members</span>
          <span>{members.data?.length ?? "—"}</span>
        </div>
        {members.error ? (
          <p className="error">{members.error}</p>
        ) : (
          <ul className="memberList">
            {(members.data ?? []).map((member) => (
              <li key={member.user_id}>
                <span className="minWidth0">
                  <strong className="breakWord">{member.display_name}</strong>
                  <small className="breakWord">{member.email}</small>
                </span>
                <span className="memberRole">{member.role}</span>
                {owner && member.role !== "owner" && (
                  <button type="button" className="askTextButton" onClick={() => void removeMember(member)}>
                    Remove
                  </button>
                )}
              </li>
            ))}
          </ul>
        )}
        {owner && !workspace.is_demo && (
          <form className="inlineForm" onSubmit={addMember}>
            <label className="field">
              <span>Add a member by email · they need a cermat. account</span>
              <input className="textInput" type="email" value={invite} required onChange={(e) => setInvite(e.target.value)} />
            </label>
            <button type="submit" className="secondaryButton">Add member</button>
          </form>
        )}
        {!owner && <p className="overviewFootnote">Only owners can manage members and thresholds.</p>}
      </section>

      <section className="panelSection" aria-labelledby="activity-title">
        <div className="sectionTitleRow">
          <span id="activity-title">Recent activity</span>
          <span>audit trail</span>
        </div>
        {audit.error ? (
          <p className="error">{audit.error}</p>
        ) : (
          <ol className="activityLog compactLog">
            {(audit.data?.items ?? []).map((event) => (
              <li key={event.id}>
                <span>{ACTION_LABEL[event.action] ?? event.action.replaceAll("_", " ")}</span>
                <span>
                  {event.actor_name ? `${event.actor_name} · ` : ""}
                  {dateTimeLabel(event.created_at)}
                </span>
              </li>
            ))}
          </ol>
        )}
      </section>

      <form className="panelSection inlineForm" onSubmit={createWorkspace}>
        <label className="field">
          <span>New workspace</span>
          <input className="textInput" value={newWorkspace} maxLength={120} required onChange={(e) => setNewWorkspace(e.target.value)} />
        </label>
        <button type="submit" className="secondaryButton" disabled={!newWorkspace.trim()}>Create</button>
      </form>

      {owner && (
        <section className="panelSection dangerZone" aria-labelledby="danger-title">
          <div className="sectionTitleRow">
            <span id="danger-title">Danger zone</span>
          </div>
          <p>
            Deleting a workspace permanently removes its documents, source files, transactions, review history, audit
            trail and settings.
          </p>
          <button type="button" className="dangerLink" onClick={() => setConfirmDelete(true)}>
            Delete this workspace
          </button>
        </section>
      )}

      <ConfirmDialog
        open={confirmDelete}
        title="Delete this workspace?"
        body={<p>All of its data is deleted immediately and cannot be recovered.</p>}
        confirmLabel="Delete workspace"
        confirmText={workspace.name}
        busy={deleting}
        error={deleteError}
        onConfirm={() => void deleteWorkspace()}
        onCancel={() => setConfirmDelete(false)}
      />
    </dialog>
  );
}
