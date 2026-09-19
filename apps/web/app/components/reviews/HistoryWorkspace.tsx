"use client";

import { useEffect, useMemo, useState } from "react";
import { api, ApiError, errorMessage, workspaceUrl } from "../../lib/api";
import { dateTimeLabel, documentTypeName, plainMoney, statusClass, statusLabel } from "../../lib/format";
import type {
  AuditEvent,
  ReconciliationResult,
  ReviewIssue,
  TransactionContext,
  TransactionDetail,
  TransactionPage,
} from "../../lib/types";
import { useResource } from "../../lib/useResource";
import { ConfirmDialog } from "../shared/ConfirmDialog";
import { confidenceLookup } from "../shared/Evidence";
import { IssueCard } from "../shared/IssueCard";

const PAGE_SIZE = 20;

const ACTIVITY_LABEL: Record<string, string> = {
  transaction_created: "Created",
  document_uploaded: "Uploaded",
  document_extracted: "Extracted",
  document_extraction_failed: "Extraction failed",
  document_attached: "Linked document",
  reconciliation_run: "Reconciled",
  issue_resolved: "Resolved",
  issue_reopened: "Reopened",
};

type Props = {
  focusId?: string | null;
  onAsk?: (transaction: { id: string; name: string }) => void;
  onStart?: () => void;
};

type DetailBundle = {
  detail: TransactionDetail;
  issues: ReviewIssue[];
  reconciliation: ReconciliationResult | null;
  context: TransactionContext | null;
  activity: AuditEvent[];
};

async function loadDetail(id: string, signal: AbortSignal): Promise<DetailBundle> {
  const [detail, issues, reconciliation, context, activity] = await Promise.all([
    api<TransactionDetail>(`/api/v1/transactions/${id}`, { signal }),
    api<ReviewIssue[]>(`/api/v1/transactions/${id}/issues`, { signal }),
    api<ReconciliationResult>(`/api/v1/transactions/${id}/reconciliation`, { signal }).catch((error) => {
      if (error instanceof ApiError && error.status === 404) return null;
      throw error;
    }),
    api<TransactionContext>(`/api/v1/transactions/${id}/context`, { signal }).catch(() => null),
    api<AuditEvent[]>(`/api/v1/transactions/${id}/activity`, { signal }).catch(() => []),
  ]);
  return { detail, issues, reconciliation, context, activity };
}

export default function HistoryWorkspace({ focusId = null, onAsk, onStart }: Props) {
  const [query, setQuery] = useState("");
  const [search, setSearch] = useState("");
  const [filter, setFilter] = useState("");
  const [offset, setOffset] = useState(0);
  const [selectedId, setSelectedId] = useState<string | null>(focusId);

  // Debounce typing; searching is deterministic and server-side (never the LLM).
  useEffect(() => {
    const timer = window.setTimeout(() => {
      setSearch(query.trim());
      setOffset(0);
    }, 300);
    return () => window.clearTimeout(timer);
  }, [query]);

  const params = new URLSearchParams({ limit: String(PAGE_SIZE), offset: String(offset) });
  if (search) params.set("q", search);
  if (filter) params.set("status", filter);
  const listKey = params.toString();
  const list = useResource<TransactionPage>(listKey, (signal) =>
    api<TransactionPage>(`/api/v1/transactions?${listKey}`, { signal })
  );
  const items = useMemo(() => list.data?.items ?? [], [list.data]);
  const activeId = selectedId ?? items[0]?.id ?? null;
  const detail = useResource<DetailBundle>(activeId, (signal) => loadDetail(activeId as string, signal));

  const [notes, setNotes] = useState<Record<string, string>>({});
  const [savingIssueId, setSavingIssueId] = useState<string | null>(null);
  const [actionError, setActionError] = useState("");
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [deleting, setDeleting] = useState(false);

  async function updateIssue(issue: ReviewIssue, status: "open" | "resolved") {
    if (!detail.data) return;
    setSavingIssueId(issue.id);
    setActionError("");
    try {
      await api(`/api/v1/transactions/${detail.data.detail.id}/issues/${issue.id}`, {
        method: "PATCH",
        json: { status, resolution_note: (notes[issue.id] ?? issue.resolution_note ?? "").trim() || null },
      });
      detail.reload();
      list.reload();
    } catch (err) {
      setActionError(errorMessage(err, "Could not update the issue."));
    } finally {
      setSavingIssueId(null);
    }
  }

  async function deleteTransaction() {
    if (!detail.data) return;
    setDeleting(true);
    setActionError("");
    try {
      await api(`/api/v1/transactions/${detail.data.detail.id}`, { method: "DELETE" });
      setConfirmDelete(false);
      setSelectedId(null);
      list.reload();
    } catch (err) {
      setActionError(errorMessage(err, "Could not delete the transaction."));
    } finally {
      setDeleting(false);
    }
  }

  const total = list.data?.total ?? 0;
  const filtered = search !== "" || filter !== "";
  const bundle = detail.data && detail.data.detail.id === activeId ? detail.data : null;
  const lookup = confidenceLookup(bundle?.context?.sources);

  return (
    <section className="historyWorkspace">
      <aside className="panel historyListPanel" aria-label="Transaction history">
        <div className="historyListTop">
          <div>
            <div className="panelLabel">01 / Transaction history</div>
            <h2>Review queue</h2>
          </div>
          <span className="historyCount" aria-label={`${total} transactions`}>{total}</span>
        </div>

        <div className="historyTools" role="search">
          <input
            className="historySearch"
            type="search"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="Transaction, document no. or supplier"
            aria-label="Search transactions"
            maxLength={120}
          />
          <select
            className="historyFilter"
            value={filter}
            onChange={(event) => {
              setFilter(event.target.value);
              setOffset(0);
            }}
            aria-label="Filter by state"
          >
            <option value="">All states</option>
            <option value="open">Open</option>
            <option value="review_required">Needs review</option>
            <option value="resolved">Resolved</option>
            <option value="matched">Matched</option>
          </select>
        </div>

        <div className="historyRows" aria-busy={list.loading}>
          {list.error ? (
            <p className="historyEmpty" role="alert">{list.error}</p>
          ) : list.loading && !list.data ? (
            <p className="historyEmpty">Loading transactions…</p>
          ) : items.length === 0 ? (
            filtered ? (
              <p className="historyEmpty">No transactions match this search.</p>
            ) : (
              <div className="historyEmpty">
                <strong>No transactions yet.</strong>
                <p>Upload a purchase order, delivery order and invoice to create your first reconciled transaction.</p>
                {onStart && (
                  <button type="button" className="secondaryButton" onClick={onStart}>
                    Start a three-way match
                  </button>
                )}
              </div>
            )
          ) : (
            items.map((item) => (
              <button
                type="button"
                className={`historyRow ${activeId === item.id ? "selected" : ""}`}
                aria-current={activeId === item.id}
                key={item.id}
                onClick={() => setSelectedId(item.id)}
              >
                <span className="historyRowTop">
                  <strong className="breakWord">{item.name}</strong>
                  <span className={`workflowState ${statusClass(item.status)}`}>{statusLabel(item.status)}</span>
                </span>
                <span className="historySupplier breakWord">{item.supplier_name ?? "Supplier pending"}</span>
                <span className="historyRowMeta">
                  <span>{dateTimeLabel(item.updated_at)}</span>
                  <span>{item.document_count}/3 docs</span>
                  {item.open_issue_count > 0 && <span>{item.open_issue_count} open</span>}
                </span>
              </button>
            ))
          )}
        </div>

        {total > PAGE_SIZE && (
          <nav className="pager" aria-label="Pages">
            <button type="button" className="askTextButton" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}>
              ← Newer
            </button>
            <span>
              {offset + 1}–{Math.min(offset + PAGE_SIZE, total)} of {total}
            </span>
            <button type="button" className="askTextButton" disabled={offset + PAGE_SIZE >= total} onClick={() => setOffset(offset + PAGE_SIZE)}>
              Older →
            </button>
          </nav>
        )}
      </aside>

      <section className="panel historyDetailPanel" aria-live="polite">
        <div className="panelLabel">02 / Review workspace</div>

        {!activeId ? (
          <div className="empty historyDetailEmpty">
            <div className="emptyMark" aria-hidden="true">H</div>
            <h2>No transaction selected.</h2>
            <p>Select a transaction to inspect its documents, evidence and review trail.</p>
          </div>
        ) : detail.error && !bundle ? (
          <div className="empty historyDetailEmpty">
            <h2>Could not load this transaction.</h2>
            <p role="alert">{detail.error}</p>
            <button type="button" className="secondaryButton" onClick={detail.reload}>Try again</button>
          </div>
        ) : !bundle ? (
          <div className="empty historyDetailEmpty">
            <h2>Loading transaction…</h2>
          </div>
        ) : (
          <>
            <div className="historyDetailHeader">
              <div className="minWidth0">
                <p className="resultKicker">Persistent review</p>
                <h2 className="breakWord">{bundle.detail.name}</h2>
                <p className="resultMeta">Updated {dateTimeLabel(bundle.detail.updated_at)}</p>
              </div>
              <div className="historyDetailActions">
                <span className={`workflowState large ${statusClass(bundle.detail.status)}`}>
                  {statusLabel(bundle.detail.status)}
                </span>
                {onAsk && (
                  <button type="button" className="askAboutButton" onClick={() => onAsk({ id: bundle.detail.id, name: bundle.detail.name })}>
                    Ask about this →
                  </button>
                )}
              </div>
            </div>

            <dl className="historySummaryRail">
              <div>
                <dt>Documents</dt>
                <dd>{bundle.detail.documents.length}</dd>
              </div>
              <div>
                <dt>Open issues</dt>
                <dd>{bundle.detail.review.open_issue_count}</dd>
              </div>
              <div>
                <dt>Resolved</dt>
                <dd>{bundle.detail.review.resolved_issue_count}</dd>
              </div>
              <div>
                <dt>Matched lines</dt>
                <dd>{bundle.reconciliation?.summary.matched_lines ?? "—"}</dd>
              </div>
            </dl>

            <div className="historyDocuments">
              <div className="sectionTitleRow">
                <span>Source documents</span>
                <span>{bundle.detail.documents.length}</span>
              </div>
              <div className="documentStrip historyDocumentStrip">
                {bundle.detail.documents.map((document) => (
                  <div className="documentCard" key={document.id}>
                    <span className="documentCardType">{documentTypeName(document.document_type)}</span>
                    <strong className="breakWord">{document.document_number ?? "No number"}</strong>
                    <small title={document.filename}>{document.filename}</small>
                    <div>
                      <span>{plainMoney(document.currency, document.total)}</span>
                      <span>
                        {document.overall_confidence === null ? "—" : `${(document.overall_confidence * 100).toFixed(0)}%`}
                      </span>
                    </div>
                  </div>
                ))}
              </div>
            </div>

            <div className="reviewQueue">
              <div className="sectionTitleRow">
                <span>Review trail</span>
                <span>{bundle.issues.length}</span>
              </div>
              {bundle.issues.length === 0 ? (
                <div className="clearState historyClearState">
                  {bundle.reconciliation
                    ? "No active reconciliation exceptions."
                    : "This transaction has not been reconciled yet. It needs a PO, delivery order and invoice."}
                </div>
              ) : (
                bundle.issues.map((issue) => (
                  <IssueCard key={issue.id} issue={issue} lookup={lookup} status={issue.status}>
                    {issue.status === "resolved" && (issue.resolved_by_name || issue.resolved_at) && (
                      <p className="resolvedBy">
                        Resolved{issue.resolved_by_name ? ` by ${issue.resolved_by_name}` : ""}
                        {issue.resolved_at ? ` · ${dateTimeLabel(issue.resolved_at)}` : ""}
                        {issue.resolution_note ? ` — “${issue.resolution_note}”` : ""}
                      </p>
                    )}
                    <div className="resolutionArea">
                      <label>
                        <span>Resolution note · optional</span>
                        <input
                          value={notes[issue.id] ?? issue.resolution_note ?? ""}
                          maxLength={1000}
                          onChange={(event) => setNotes((current) => ({ ...current, [issue.id]: event.target.value }))}
                          placeholder="e.g. Supplier confirmed corrected invoice"
                        />
                      </label>
                      <button
                        type="button"
                        className={issue.status === "resolved" ? "reopenButton" : "resolveButton"}
                        disabled={savingIssueId === issue.id}
                        onClick={() => void updateIssue(issue, issue.status === "resolved" ? "open" : "resolved")}
                      >
                        {savingIssueId === issue.id ? "Saving…" : issue.status === "resolved" ? "Reopen issue" : "Mark resolved"}
                      </button>
                    </div>
                  </IssueCard>
                ))
              )}
            </div>

            {bundle.activity.length > 0 && (
              <div className="activityLog">
                <div className="sectionTitleRow">
                  <span>Activity</span>
                  <span>{bundle.activity.length}</span>
                </div>
                <ol>
                  {bundle.activity.slice(-12).map((event) => (
                    <li key={event.id}>
                      <span>{ACTIVITY_LABEL[event.action] ?? event.action.replaceAll("_", " ")}</span>
                      <span>
                        {event.actor_name ? `${event.actor_name} · ` : ""}
                        {dateTimeLabel(event.created_at)}
                      </span>
                    </li>
                  ))}
                </ol>
              </div>
            )}

            <div className="detailFooter">
              <a
                className="secondaryButton"
                href={workspaceUrl(`/api/v1/exports/review-issues.csv?transaction_id=${bundle.detail.id}`)}
                download
              >
                Export issues (CSV)
              </a>
              <button type="button" className="dangerLink" onClick={() => setConfirmDelete(true)}>
                Delete transaction
              </button>
            </div>
          </>
        )}

        {actionError && !confirmDelete && <p className="error historyError" role="alert">{actionError}</p>}
      </section>

      <ConfirmDialog
        open={confirmDelete}
        title="Delete this transaction?"
        body={
          <p>
            <strong>{bundle?.detail.name}</strong>, its {bundle?.detail.documents.length ?? 0} documents, source files and
            review history will be permanently deleted. This cannot be undone.
          </p>
        }
        confirmLabel="Delete transaction"
        busy={deleting}
        error={confirmDelete ? actionError : ""}
        onConfirm={() => void deleteTransaction()}
        onCancel={() => setConfirmDelete(false)}
      />
    </section>
  );
}
