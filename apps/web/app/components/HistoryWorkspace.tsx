"use client";

import { useEffect, useMemo, useState } from "react";

type DocumentType =
  | "purchase_order"
  | "delivery_order"
  | "invoice"
  | "receipt";

type TransactionStatus =
  | "collecting"
  | "ready"
  | "matched"
  | "review_required"
  | "insufficient_data"
  | "resolved";

type TransactionDocumentSummary = {
  id: string;
  filename: string;
  document_type: DocumentType;
  status: string;
  document_number: string | null;
  supplier_name: string | null;
  currency: string | null;
  total: number | null;
  overall_confidence: number | null;
};

type HistoryItem = {
  id: string;
  name: string;
  status: TransactionStatus;
  created_at: string;
  updated_at: string;
  document_count: number;
  issue_count: number;
  open_issue_count: number;
  resolved_issue_count: number;
  high_count: number;
  supplier_name: string | null;
  currency: string | null;
  total: number | null;
};

type TransactionDetail = {
  id: string;
  name: string;
  status: TransactionStatus;
  created_at: string;
  updated_at: string;
  documents: TransactionDocumentSummary[];
  review: {
    issue_count: number;
    open_issue_count: number;
    resolved_issue_count: number;
    high_count: number;
  };
};

type ReviewIssue = {
  id: string;
  transaction_id: string;
  issue_key: string;
  code: string;
  title: string;
  severity: "low" | "medium" | "high";
  status: "open" | "resolved";
  item_description: string | null;
  expected: string | null;
  actual: string | null;
  delta: string | null;
  explanation: string;
  resolution_note: string | null;
  active: boolean;
  resolved_at: string | null;
  created_at: string;
  updated_at: string;
  sources: Array<{
    document_id: string;
    filename: string;
    document_type: DocumentType;
    field_path: string;
    source_text: string;
    page: number | null;
    value: string | null;
  }>;
};

type ReconciliationResult = {
  transaction_id: string;
  status: "matched" | "review_required" | "insufficient_data";
  summary: {
    issue_count: number;
    high: number;
    medium: number;
    low: number;
    matched_lines: number;
    review_lines: number;
  };
};

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

async function responseJson<T>(response: Response): Promise<T> {
  const payload = await response.json();
  if (!response.ok) {
    throw new Error(payload.detail ?? "Request failed.");
  }
  return payload as T;
}

function money(currency: string | null, value: number | null) {
  if (value === null) return "—";
  return `${currency ?? ""} ${value.toFixed(2)}`.trim();
}

function documentTypeName(type: DocumentType) {
  return {
    purchase_order: "Purchase order",
    delivery_order: "Delivery order",
    invoice: "Invoice",
    receipt: "Receipt",
  }[type];
}

function statusLabel(status: TransactionStatus) {
  return {
    collecting: "Open",
    ready: "Open",
    matched: "Matched",
    review_required: "Needs review",
    insufficient_data: "More data",
    resolved: "Resolved",
  }[status];
}

function statusClass(status: TransactionStatus) {
  if (status === "collecting" || status === "ready" || status === "insufficient_data") {
    return "open";
  }
  return status;
}

function dateLabel(value: string) {
  return new Intl.DateTimeFormat(undefined, {
    day: "2-digit",
    month: "short",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(value));
}

export default function HistoryWorkspace() {
  const [history, setHistory] = useState<HistoryItem[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [detail, setDetail] = useState<TransactionDetail | null>(null);
  const [issues, setIssues] = useState<ReviewIssue[]>([]);
  const [reconciliation, setReconciliation] =
    useState<ReconciliationResult | null>(null);
  const [query, setQuery] = useState("");
  const [filter, setFilter] = useState("all");
  const [notes, setNotes] = useState<Record<string, string>>({});
  const [loading, setLoading] = useState(true);
  const [detailLoading, setDetailLoading] = useState(false);
  const [error, setError] = useState("");
  const [savingIssueId, setSavingIssueId] = useState<string | null>(null);

  async function loadHistory(preferredId?: string | null) {
    const response = await fetch(`${API_URL}/api/v1/transactions`);
    const items = await responseJson<HistoryItem[]>(response);
    setHistory(items);

    const candidate = preferredId ?? selectedId ?? items[0]?.id ?? null;
    if (candidate && items.some((item) => item.id === candidate)) {
      setSelectedId(candidate);
    } else {
      setSelectedId(items[0]?.id ?? null);
    }
  }

  async function loadDetail(transactionId: string) {
    setDetailLoading(true);
    setError("");
    try {
      const [detailResponse, issuesResponse, reconciliationResponse] =
        await Promise.all([
          fetch(`${API_URL}/api/v1/transactions/${transactionId}`),
          fetch(`${API_URL}/api/v1/transactions/${transactionId}/issues`),
          fetch(`${API_URL}/api/v1/transactions/${transactionId}/reconciliation`),
        ]);

      setDetail(await responseJson<TransactionDetail>(detailResponse));
      const loadedIssues = await responseJson<ReviewIssue[]>(issuesResponse);
      setIssues(loadedIssues);
      setNotes(
        Object.fromEntries(
          loadedIssues.map((issue) => [issue.id, issue.resolution_note ?? ""])
        )
      );

      if (reconciliationResponse.ok) {
        setReconciliation(
          (await reconciliationResponse.json()) as ReconciliationResult
        );
      } else if (reconciliationResponse.status === 404) {
        setReconciliation(null);
      } else {
        await responseJson(reconciliationResponse);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not load transaction.");
    } finally {
      setDetailLoading(false);
    }
  }

  useEffect(() => {
    (async () => {
      try {
        await loadHistory();
      } catch (err) {
        setError(err instanceof Error ? err.message : "Could not load history.");
      } finally {
        setLoading(false);
      }
    })();
    // Load once when the workspace opens.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (selectedId) {
      void loadDetail(selectedId);
    } else {
      setDetail(null);
      setIssues([]);
      setReconciliation(null);
    }
  }, [selectedId]);

  const filtered = useMemo(() => {
    const search = query.trim().toLowerCase();
    return history.filter((item) => {
      const matchesSearch =
        !search ||
        item.name.toLowerCase().includes(search) ||
        (item.supplier_name ?? "").toLowerCase().includes(search);

      const matchesFilter =
        filter === "all" ||
        (filter === "open" &&
          ["collecting", "ready", "insufficient_data"].includes(item.status)) ||
        item.status === filter;

      return matchesSearch && matchesFilter;
    });
  }, [history, query, filter]);

  async function updateIssue(issue: ReviewIssue, status: "open" | "resolved") {
    if (!detail) return;
    setSavingIssueId(issue.id);
    setError("");
    try {
      const response = await fetch(
        `${API_URL}/api/v1/transactions/${detail.id}/issues/${issue.id}`,
        {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            status,
            resolution_note: notes[issue.id]?.trim() || null,
          }),
        }
      );
      await responseJson<ReviewIssue>(response);
      await Promise.all([loadDetail(detail.id), loadHistory(detail.id)]);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not update issue.");
    } finally {
      setSavingIssueId(null);
    }
  }

  return (
    <section className="historyWorkspace">
      <aside className="panel historyListPanel">
        <div className="historyListTop">
          <div>
            <div className="panelLabel">01 / TRANSACTION HISTORY</div>
            <h2>Review queue</h2>
          </div>
          <span className="historyCount">{history.length}</span>
        </div>

        <div className="historyTools">
          <input
            className="historySearch"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="Search transaction or supplier"
            aria-label="Search transaction history"
          />
          <select
            className="historyFilter"
            value={filter}
            onChange={(event) => setFilter(event.target.value)}
            aria-label="Filter transaction history"
          >
            <option value="all">All states</option>
            <option value="open">Open</option>
            <option value="review_required">Needs review</option>
            <option value="resolved">Resolved</option>
            <option value="matched">Matched</option>
          </select>
        </div>

        <div className="historyRows">
          {loading ? (
            <div className="historyEmpty">Loading transaction history…</div>
          ) : filtered.length === 0 ? (
            <div className="historyEmpty">No transactions match this view.</div>
          ) : (
            filtered.map((item) => (
              <button
                type="button"
                className={`historyRow ${selectedId === item.id ? "selected" : ""}`}
                key={item.id}
                onClick={() => setSelectedId(item.id)}
              >
                <div className="historyRowTop">
                  <strong>{item.name}</strong>
                  <span className={`workflowState ${statusClass(item.status)}`}>
                    {statusLabel(item.status)}
                  </span>
                </div>
                <span className="historySupplier">
                  {item.supplier_name ?? "Supplier pending"}
                </span>
                <div className="historyRowMeta">
                  <span>{dateLabel(item.updated_at)}</span>
                  <span>{item.document_count}/3 docs</span>
                  {item.open_issue_count > 0 && (
                    <span>{item.open_issue_count} open</span>
                  )}
                </div>
              </button>
            ))
          )}
        </div>
      </aside>

      <section className="panel historyDetailPanel">
        <div className="panelLabel">02 / REVIEW WORKSPACE</div>

        {!selectedId || !detail ? (
          <div className="empty historyDetailEmpty">
            <div className="emptyMark">H</div>
            <h2>{detailLoading ? "Loading review…" : "No transaction selected."}</h2>
            <p>Select a transaction to inspect its documents and review trail.</p>
          </div>
        ) : (
          <>
            <div className="historyDetailHeader">
              <div>
                <p className="resultKicker">Persistent review</p>
                <h2>{detail.name}</h2>
                <p className="resultMeta">
                  Updated {dateLabel(detail.updated_at)}
                </p>
              </div>
              <span className={`workflowState large ${statusClass(detail.status)}`}>
                {statusLabel(detail.status)}
              </span>
            </div>

            <div className="historySummaryRail">
              <div>
                <span>Documents</span>
                <strong>{detail.documents.length}</strong>
              </div>
              <div>
                <span>Open issues</span>
                <strong>{detail.review.open_issue_count}</strong>
              </div>
              <div>
                <span>Resolved</span>
                <strong>{detail.review.resolved_issue_count}</strong>
              </div>
              <div>
                <span>Matched lines</span>
                <strong>{reconciliation?.summary.matched_lines ?? "—"}</strong>
              </div>
            </div>

            <div className="historyDocuments">
              <div className="sectionTitleRow">
                <span>Source documents</span>
                <span>{detail.documents.length}</span>
              </div>
              <div className="documentStrip historyDocumentStrip">
                {detail.documents.map((document) => (
                  <div className="documentCard" key={document.id}>
                    <span className="documentCardType">
                      {documentTypeName(document.document_type)}
                    </span>
                    <strong>{document.document_number ?? "No number"}</strong>
                    <small>{document.filename}</small>
                    <div>
                      <span>{money(document.currency, document.total)}</span>
                      <span>
                        {document.overall_confidence === null
                          ? "—"
                          : `${(document.overall_confidence * 100).toFixed(0)}%`}
                      </span>
                    </div>
                  </div>
                ))}
              </div>
            </div>

            <div className="reviewQueue">
              <div className="sectionTitleRow">
                <span>Review trail</span>
                <span>{issues.length}</span>
              </div>

              {issues.length === 0 ? (
                <div className="clearState historyClearState">
                  {reconciliation
                    ? "No active reconciliation exceptions."
                    : "This transaction has not been reconciled yet."}
                </div>
              ) : (
                issues.map((issue) => (
                  <article
                    className={`issueCard reviewIssueCard ${issue.status}`}
                    key={issue.id}
                  >
                    <div className="issueTopline">
                      <span className={`severity ${issue.severity}`}>
                        {issue.severity}
                      </span>
                      <span>{issue.code.replaceAll("_", " ")}</span>
                      <span className={`issueState ${issue.status}`}>
                        {issue.status}
                      </span>
                    </div>

                    <h3>{issue.title}</h3>
                    {issue.item_description && (
                      <p className="issueItem">{issue.item_description}</p>
                    )}

                    {(issue.expected || issue.actual || issue.delta) && (
                      <div className="varianceGrid">
                        <div>
                          <span>Expected</span>
                          <strong>{issue.expected ?? "—"}</strong>
                        </div>
                        <div>
                          <span>Actual</span>
                          <strong>{issue.actual ?? "—"}</strong>
                        </div>
                        <div>
                          <span>Delta</span>
                          <strong>{issue.delta ?? "—"}</strong>
                        </div>
                      </div>
                    )}

                    <p className="issueExplanation">{issue.explanation}</p>

                    {issue.sources.length > 0 && (
                      <div className="sourceStack">
                        {issue.sources.map((source, index) => (
                          <div
                            className="sourceRow"
                            key={`${source.document_id}-${source.field_path}-${index}`}
                          >
                            <span className="sourceType">
                              {documentTypeName(source.document_type)}
                            </span>
                            <span className="sourceQuote">{source.source_text}</span>
                            <span className="sourcePage">p.{source.page ?? "—"}</span>
                          </div>
                        ))}
                      </div>
                    )}

                    <div className="resolutionArea">
                      <label>
                        <span>Resolution note · optional</span>
                        <input
                          value={notes[issue.id] ?? ""}
                          onChange={(event) =>
                            setNotes((current) => ({
                              ...current,
                              [issue.id]: event.target.value,
                            }))
                          }
                          placeholder="e.g. Supplier confirmed corrected invoice"
                        />
                      </label>
                      <button
                        type="button"
                        className={issue.status === "resolved" ? "reopenButton" : "resolveButton"}
                        disabled={savingIssueId === issue.id}
                        onClick={() =>
                          void updateIssue(
                            issue,
                            issue.status === "resolved" ? "open" : "resolved"
                          )
                        }
                      >
                        {savingIssueId === issue.id
                          ? "Saving…"
                          : issue.status === "resolved"
                            ? "Reopen issue"
                            : "Mark resolved"}
                      </button>
                    </div>
                  </article>
                ))
              )}
            </div>
          </>
        )}

        {error && <p className="error historyError">{error}</p>}
      </section>
    </section>
  );
}
