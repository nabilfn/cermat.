"use client";

import { FormEvent, useEffect, useMemo, useRef, useState } from "react";

type DocumentType = "purchase_order" | "delivery_order" | "invoice" | "receipt";
type Severity = "low" | "medium" | "high";
type TransactionStatus =
  | "collecting"
  | "ready"
  | "matched"
  | "review_required"
  | "insufficient_data"
  | "resolved";

type AskSource = {
  id: string;
  label: string;
  document_id: string;
  filename: string;
  document_type: DocumentType;
  document_number: string | null;
  transaction_id: string;
  transaction_name: string;
  page: number | null;
  field_path: string;
  value: string | null;
  source_text: string;
  confidence: number | null;
  snippet_available: boolean;
  preview_url: string | null;
};

type AskVariance = {
  kind: "money" | "quantity";
  currency: string | null;
  expected: number;
  actual: number;
  delta: number;
  percentage: number | null;
  billed_impact: number | null;
};

type AskIssueRow = {
  issue_id: string;
  transaction_id: string;
  transaction_name: string;
  supplier: string | null;
  code: string;
  family: string;
  title: string;
  severity: Severity;
  status: "open" | "resolved";
  item_description: string | null;
  expected: string | null;
  actual: string | null;
  delta: string | null;
  variance: AskVariance | null;
  explanation: string;
  resolution_note: string | null;
  resolved_at: string | null;
  source_ids: string[];
};

type AskTransactionRow = {
  transaction_id: string;
  name: string;
  supplier: string | null;
  status: TransactionStatus;
  currency: string | null;
  total: number | null;
  document_types: DocumentType[];
  missing_document_types: DocumentType[];
  open_issue_count: number;
  resolved_issue_count: number;
  highest_open_severity: Severity | null;
  updated_at: string;
};

type AskSupplierRow = {
  supplier: string;
  transaction_count: number;
  open_issue_count: number;
  resolved_issue_count: number;
  high_open_count: number;
  transaction_ids: string[];
  open_by_family: Record<string, number>;
};

type AskConversationContext = {
  previous_question: string | null;
  previous_intent: string | null;
  entities: Array<{ kind: "transaction" | "supplier"; id: string; label: string }>;
};

type AskResponse = {
  question: string;
  intent: string;
  filters: Record<string, string | null>;
  scope: "workspace" | "transaction";
  scope_transaction_id: string | null;
  scope_transaction_name: string | null;
  outcome: "answered" | "no_results" | "unsupported" | "not_found";
  answer: { headline: string; points: Array<{ text: string; source_ids: string[] }> };
  answer_mode: "model" | "records";
  metrics: {
    transaction_count: number;
    issue_count: number;
    open_issue_count: number;
    resolved_issue_count: number;
  };
  result_kind: "issues" | "transactions" | "suppliers" | "insights" | "none";
  issues: AskIssueRow[];
  transactions: AskTransactionRow[];
  suppliers: AskSupplierRow[];
  sources: AskSource[];
  notices: string[];
  follow_up_suggestions: string[];
  context: AskConversationContext;
};

type StreamEvent =
  | { stage: "interpreting" | "searching" | "composing" }
  | { stage: "done"; response: AskResponse }
  | { stage: "error"; status: number; detail: string };

type Turn = { id: number; response: AskResponse };

export type AskScope = { id: string; name: string } | null;

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

const STAGE_LABEL: Record<string, string> = {
  checking: "Checking workspace…",
  interpreting: "Interpreting question…",
  searching: "Searching records…",
  composing: "Composing answer from records…",
};

const OUTCOME_LABEL: Record<AskResponse["outcome"], string> = {
  answered: "Answer",
  no_results: "No matching records",
  unsupported: "Outside this workspace",
  not_found: "Not found",
};

const FAMILY_LABEL: Record<string, string> = {
  price: "Price",
  quantity: "Quantity",
  supplier: "Supplier",
  currency: "Currency",
  arithmetic: "Arithmetic",
  missing_item: "Missing item",
  unexpected_item: "Unexpected item",
  missing_line_items: "Line items",
  other: "Other",
};

function humanise(value: string) {
  return value.replaceAll("_", " ");
}

function fieldLabel(path: string) {
  const line = /^line_items\.(\d+)\.(.+)$/.exec(path);
  if (line) return `Line ${Number(line[1]) + 1} · ${humanise(line[2])}`;
  return humanise(path);
}

function fieldShort(path: string) {
  return humanise(path.split(".").pop() ?? path);
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

function signed(value: number, digits: number, prefix = "") {
  const sign = value > 0 ? "+" : value < 0 ? "−" : "";
  return `${sign}${prefix}${Math.abs(value).toLocaleString(undefined, {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  })}`;
}

function varianceLabel(variance: AskVariance) {
  const amount =
    variance.kind === "money"
      ? signed(variance.delta, 2, variance.currency ? `${variance.currency} ` : "")
      : `${signed(variance.delta, 0)} units`;
  return variance.percentage === null
    ? amount
    : `${amount} · ${signed(variance.percentage, 2)}%`;
}

function filterSummary(response: AskResponse) {
  return Object.entries(response.filters)
    .filter(([key, value]) => value && key !== "transaction_id")
    .map(([key, value]) => `${humanise(key)}: ${humanise(String(value))}`)
    .join(" · ");
}

async function readStream(
  response: Response,
  onEvent: (event: StreamEvent) => void
) {
  if (!response.body) throw new Error("Empty response.");
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let newline = buffer.indexOf("\n");
    while (newline >= 0) {
      const line = buffer.slice(0, newline).trim();
      buffer = buffer.slice(newline + 1);
      if (line) onEvent(JSON.parse(line) as StreamEvent);
      newline = buffer.indexOf("\n");
    }
  }
  if (buffer.trim()) onEvent(JSON.parse(buffer) as StreamEvent);
}

type AskWorkspaceProps = {
  scope: AskScope;
  onClearScope: () => void;
  onScope: (scope: { id: string; name: string }) => void;
  onOpenTransaction: (id: string) => void;
  /** A question asked from elsewhere (Overview, supplier view). Runs once per nonce. */
  request?: { text: string; nonce: number } | null;
};

export default function AskWorkspace({
  scope,
  onClearScope,
  onScope,
  onOpenTransaction,
  request = null,
}: AskWorkspaceProps) {
  const [question, setQuestion] = useState("");
  const [turns, setTurns] = useState<Turn[]>([]);
  const [activeTurnId, setActiveTurnId] = useState<number | null>(null);
  const [stage, setStage] = useState<string | null>(null);
  const [error, setError] = useState("");
  const [suggestions, setSuggestions] = useState<string[]>([]);
  const [selection, setSelection] = useState<{ id: string; at: string } | null>(null);
  const selectedSourceId = selection?.id ?? null;
  const [context, setContext] = useState<AskConversationContext | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const nextId = useRef(1);

  const scopeId = scope?.id ?? null;

  useEffect(() => {
    // Conversation memory never crosses a scope change.
    setContext(null);
    const url = scopeId
      ? `${API_URL}/api/v1/ask/suggestions?transaction_id=${scopeId}`
      : `${API_URL}/api/v1/ask/suggestions`;
    fetch(url)
      .then((response) => (response.ok ? response.json() : null))
      .then((payload) => setSuggestions(payload?.suggestions ?? []))
      .catch(() =>
        setSuggestions([
          "What needs my attention?",
          "Show high-severity issues.",
          "Which suppliers have unresolved discrepancies?",
          "Show invoice price mismatches.",
          "Which transactions were resolved recently?",
        ])
      );
  }, [scopeId]);

  const handledRequest = useRef<number | null>(null);
  useEffect(() => {
    if (!request || handledRequest.current === request.nonce || scopeId) return;
    handledRequest.current = request.nonce;
    setQuestion(request.text);
    void ask(request.text);
    // `ask` reads the latest state; re-running on its identity would repeat the question.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [request, scopeId]);

  const active = useMemo(
    () => turns.find((turn) => turn.id === activeTurnId) ?? turns[0] ?? null,
    [turns, activeTurnId]
  );
  const earlier = turns.filter((turn) => turn.id !== active?.id);
  const busy = stage !== null;

  async function ask(text: string) {
    const trimmed = text.trim();
    if (!trimmed || busy) return;

    setError("");
    setStage("checking");
    setSelection(null);

    try {
      const response = await fetch(`${API_URL}/api/v1/ask/stream`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          question: trimmed,
          transaction_id: scopeId,
          context,
        }),
      });
      if (!response.ok) {
        const payload = await response.json().catch(() => null);
        throw new Error(
          response.status === 422
            ? "Questions can be up to 500 characters."
            : payload?.detail ?? "The cermat. API returned an error."
        );
      }

      let finished = false;
      await readStream(response, (event) => {
        if (event.stage === "done") {
          finished = true;
          const id = nextId.current++;
          setTurns((current) => [{ id, response: event.response }, ...current].slice(0, 12));
          setActiveTurnId(id);
          setContext(event.response.context);
          setQuestion("");
        } else if (event.stage === "error") {
          finished = true;
          setError(event.detail);
        } else {
          setStage(event.stage);
        }
      });
      if (!finished) throw new Error("The answer was interrupted. Try again.");
    } catch (err) {
      setError(
        err instanceof TypeError
          ? "Cannot reach the cermat. API. Check that the API service is running."
          : err instanceof Error
            ? err.message
            : "Something went wrong."
      );
    } finally {
      setStage(null);
    }
  }

  function submit(event: FormEvent) {
    event.preventDefault();
    void ask(question);
  }

  function pick(text: string) {
    setQuestion(text);
    void ask(text);
  }

  function resetSession() {
    setTurns([]);
    setActiveTurnId(null);
    setContext(null);
    setSelection(null);
    setError("");
    inputRef.current?.focus();
  }

  const sources = active?.response.sources ?? [];
  const sourceById = useMemo(
    () => new Map(sources.map((source) => [source.id, source])),
    [sources]
  );
  const selectedSource = selectedSourceId ? sourceById.get(selectedSourceId) ?? null : null;

  function chips(ids: string[], inlineKey: string) {
    const known = ids.map((id) => sourceById.get(id)).filter(Boolean) as AskSource[];
    if (known.length === 0) return null;
    const selectedHere = selection?.at === inlineKey;
    const labelCounts = new Map<string, number>();
    known.forEach((source) =>
      labelCounts.set(source.label, (labelCounts.get(source.label) ?? 0) + 1)
    );
    return (
      <>
        <span className="askChips">
          {known.map((source) => (
            <button
              type="button"
              key={`${inlineKey}-${source.id}`}
              className={`sourceChip ${source.snippet_available ? "" : "noSnippet"} ${
                selectedSourceId === source.id ? "selected" : ""
              }`}
              aria-pressed={selectedSourceId === source.id}
              title={`${documentTypeName(source.document_type)} · ${source.field_path}`}
              onClick={() =>
                setSelection((current) =>
                  current?.id === source.id && current.at === inlineKey
                    ? null
                    : { id: source.id, at: inlineKey }
                )
              }
            >
              {source.label}
              {(labelCounts.get(source.label) ?? 0) > 1 && (
                <span className="chipField">{fieldShort(source.field_path)}</span>
              )}
            </button>
          ))}
        </span>
        {selectedHere && selectedSource && (
          <div className="askInlineEvidence">
            <EvidenceDetail
              source={selectedSource}
              onOpenTransaction={onOpenTransaction}
            />
          </div>
        )}
      </>
    );
  }

  const response = active?.response ?? null;

  return (
    <section className="askWorkspace">
      <div className="panel askPanel">
        <header className="askHeader">
          <div>
            <div className="panelLabel">Ask cermat.</div>
            <p className="askLede">
              Ask about transactions, suppliers, documents or exceptions.
            </p>
          </div>
          <div className="askScope" aria-live="polite">
            <span className="askScopeLabel">Scope</span>
            {scope ? (
              <span className="askScopeValue scoped">
                {scope.name}
                <button
                  type="button"
                  className="askScopeClear"
                  onClick={onClearScope}
                  aria-label="Clear transaction scope"
                >
                  ×
                </button>
              </span>
            ) : (
              <span className="askScopeValue">Whole workspace</span>
            )}
          </div>
        </header>

        <form className="askForm" onSubmit={submit}>
          <input
            ref={inputRef}
            className="askInput"
            value={question}
            maxLength={500}
            onChange={(event) => setQuestion(event.target.value)}
            placeholder={
              scope ? "Why is this transaction flagged?" : "What needs my attention right now?"
            }
            aria-label="Ask a question about your records"
            disabled={busy}
          />
          <button
            type="submit"
            className="askSubmit"
            disabled={busy || !question.trim()}
            aria-label="Ask"
          >
            →
          </button>
        </form>

        <div className={`askProgress ${busy ? "active" : ""}`} aria-live="polite">
          <span>{busy ? STAGE_LABEL[stage ?? "checking"] : "Read-only · answers cite persisted records"}</span>
          {turns.length > 0 && !busy && (
            <button type="button" className="askTextButton" onClick={resetSession}>
              New session
            </button>
          )}
        </div>

        {error && (
          <div className="askError" role="alert">
            <span>Request failed</span>
            {error}
          </div>
        )}

        {!response ? (
          <div className="askSuggestions">
            <div className="sectionTitleRow">
              <span>{scope ? "Ask about this transaction" : "Start with"}</span>
              <span>{suggestions.length}</span>
            </div>
            {suggestions.slice(0, 6).map((suggestion, index) => (
              <button
                type="button"
                key={suggestion}
                className="askSuggestion"
                onClick={() => pick(suggestion)}
                disabled={busy}
              >
                <span>{String(index + 1).padStart(2, "0")}</span>
                {suggestion}
              </button>
            ))}
          </div>
        ) : (
          <article className={`askReport ${response.outcome}`}>
            <div className="askReportMeta">
              <span className="askOutcome">{OUTCOME_LABEL[response.outcome]}</span>
              <span>{response.question}</span>
            </div>

            <h2 className="askHeadline">{response.answer.headline}</h2>

            {response.answer.points.length > 0 && (
              <ol className="askPoints">
                {response.answer.points.map((point, index) => (
                  <li key={index}>
                    <span className="askPointText">{point.text}</span>
                    {chips(point.source_ids, `p${index}`)}
                  </li>
                ))}
              </ol>
            )}

            {response.notices.length > 0 && (
              <ul className="askNotices">
                {response.notices.map((notice) => (
                  <li key={notice}>{notice}</li>
                ))}
              </ul>
            )}

            {(response.result_kind === "issues" || response.result_kind === "insights") &&
              response.issues.length > 0 && (
              <section className="askResults">
                <div className="sectionTitleRow">
                  <span>Exceptions</span>
                  <span>{response.issues.length}</span>
                </div>
                {response.issues.map((row) => (
                  <div className="askIssueRow" key={row.issue_id}>
                    <span className={`severity ${row.severity}`}>{row.severity}</span>
                    <div className="askIssueMain">
                      <strong>
                        {row.transaction_name}
                        <em>{FAMILY_LABEL[row.family] ?? humanise(row.family)}</em>
                      </strong>
                      <span>
                        {row.title}
                        {row.item_description ? ` — ${row.item_description}` : ""}
                      </span>
                      {row.resolution_note && (
                        <span className="askNote">Note: {row.resolution_note}</span>
                      )}
                      {chips(row.source_ids, row.issue_id)}
                    </div>
                    <div className="askFigures">
                      {(row.expected || row.actual) && (
                        <span>
                          {row.expected ?? "—"} → {row.actual ?? "—"}
                        </span>
                      )}
                      {row.variance && <strong>{varianceLabel(row.variance)}</strong>}
                    </div>
                    <span className={`issueState ${row.status}`}>{row.status}</span>
                  </div>
                ))}
              </section>
            )}

            {response.result_kind === "suppliers" && response.suppliers.length > 0 && (
              <section className="askResults">
                <div className="sectionTitleRow">
                  <span>Suppliers</span>
                  <span>{response.suppliers.length}</span>
                </div>
                <div className="askTableHead askSupplierGrid">
                  <span>Supplier</span>
                  <span>Open</span>
                  <span>High</span>
                  <span>Txns</span>
                </div>
                {response.suppliers.map((row) => (
                  <button
                    type="button"
                    className="askTableRow askSupplierGrid"
                    key={row.supplier}
                    onClick={() => pick(`Show open issues for ${row.supplier}`)}
                    disabled={busy}
                  >
                    <span className="askCellPrimary">{row.supplier}</span>
                    <span data-label="Open">{row.open_issue_count}</span>
                    <span data-label="High">{row.high_open_count}</span>
                    <span data-label="Txns">{row.transaction_count}</span>
                  </button>
                ))}
              </section>
            )}

            {response.transactions.length > 0 && response.result_kind !== "suppliers" && (
              <section className="askResults">
                <div className="sectionTitleRow">
                  <span>Related transactions</span>
                  <span>{response.transactions.length}</span>
                </div>
                {response.transactions.map((row) => (
                  <div className="askTxnRow" key={row.transaction_id}>
                    <div className="askTxnMain">
                      <strong>{row.name}</strong>
                      <span>{row.supplier ?? "Supplier not extracted"}</span>
                    </div>
                    <span className="askTxnSeverity">
                      {row.highest_open_severity ? (
                        <span className={`severity ${row.highest_open_severity}`}>
                          {row.highest_open_severity}
                        </span>
                      ) : row.missing_document_types.length > 0 ? (
                        <span className="askMissing">
                          Missing{" "}
                          {row.missing_document_types
                            .map((type) => documentTypeName(type).toLowerCase())
                            .join(", ")}
                        </span>
                      ) : (
                        <span className="askMissing">—</span>
                      )}
                    </span>
                    <span className={`workflowState ${statusClass(row.status)}`}>
                      {statusLabel(row.status)}
                    </span>
                    <span className="askTxnActions">
                      {scopeId !== row.transaction_id && (
                        <button
                          type="button"
                          className="askTextButton"
                          onClick={() => onScope({ id: row.transaction_id, name: row.name })}
                        >
                          Scope
                        </button>
                      )}
                      <button
                        type="button"
                        className="askTextButton"
                        onClick={() => onOpenTransaction(row.transaction_id)}
                      >
                        Open
                      </button>
                    </span>
                  </div>
                ))}
              </section>
            )}

            {response.follow_up_suggestions.length > 0 && (
              <div className="askFollowUps">
                <span>Next</span>
                {response.follow_up_suggestions.map((suggestion) => (
                  <button
                    type="button"
                    key={suggestion}
                    onClick={() => pick(suggestion)}
                    disabled={busy}
                  >
                    {suggestion}
                  </button>
                ))}
              </div>
            )}

            <footer className="askTrace">
              <span>Interpreted as {humanise(response.intent)}</span>
              {filterSummary(response) && <span>{filterSummary(response)}</span>}
              <span>
                {response.scope === "transaction"
                  ? `Scope: ${response.scope_transaction_name}`
                  : "Scope: workspace"}
              </span>
              <span>
                {response.answer_mode === "model"
                  ? "Wording by model · facts from records"
                  : "Composed directly from records"}
              </span>
            </footer>
          </article>
        )}

        {earlier.length > 0 && (
          <section className="askLedger">
            <div className="sectionTitleRow">
              <span>Earlier in this session</span>
              <span>{earlier.length}</span>
            </div>
            {earlier.map((turn) => (
              <button
                type="button"
                key={turn.id}
                className="askLedgerRow"
                onClick={() => {
                  setActiveTurnId(turn.id);
                  setSelection(null);
                }}
              >
                <span>{turn.response.question}</span>
                <strong>{turn.response.answer.headline}</strong>
              </button>
            ))}
          </section>
        )}
      </div>

      <aside className="panel askEvidence" aria-label="Evidence">
        <div className="panelLabel">Evidence</div>
        {selectedSource ? (
          <EvidenceDetail
            source={selectedSource}
            onOpenTransaction={onOpenTransaction}
            onClose={() => setSelection(null)}
          />
        ) : sources.length > 0 ? (
          <div className="askSourceIndex">
            <p className="askEvidenceHint">
              Select a source to inspect the extracted value and snippet.
            </p>
            {sources.map((source) => (
              <button
                type="button"
                key={source.id}
                className="askSourceIndexRow"
                onClick={() => setSelection({ id: source.id, at: "index" })}
              >
                <span>{source.label}</span>
                <small>
                  {documentTypeName(source.document_type)} · {fieldLabel(source.field_path)}
                </small>
              </button>
            ))}
          </div>
        ) : (
          <p className="askEvidenceHint">
            Sources cited by an answer appear here with the document, page, extracted
            value and snippet.
          </p>
        )}
      </aside>
    </section>
  );
}

function EvidenceDetail({
  source,
  onOpenTransaction,
  onClose,
}: {
  source: AskSource;
  onOpenTransaction: (id: string) => void;
  onClose?: () => void;
}) {
  return (
    <div className="evidenceDetail">
      <div className="evidenceDetailTop">
        <div>
          <span className="documentCardType">{documentTypeName(source.document_type)}</span>
          <strong>{source.document_number ?? source.filename}</strong>
          <small>{source.filename}</small>
        </div>
        {onClose && (
          <button type="button" className="askTextButton" onClick={onClose}>
            Close
          </button>
        )}
      </div>

      <dl className="evidenceFacts">
        <div>
          <dt>Page</dt>
          <dd>{source.page ?? "—"}</dd>
        </div>
        <div>
          <dt>Field</dt>
          <dd>{fieldLabel(source.field_path)}</dd>
        </div>
        <div>
          <dt>Extracted value</dt>
          <dd>{source.value ?? "—"}</dd>
        </div>
        <div>
          <dt>Confidence</dt>
          <dd>
            {source.confidence === null ? "—" : `${Math.round(source.confidence * 100)}%`}
          </dd>
        </div>
      </dl>

      {source.snippet_available ? (
        <blockquote className="evidenceQuote">{source.source_text}</blockquote>
      ) : (
        <p className="evidenceMissing">
          No source snippet was captured for this value. Check the original document.
        </p>
      )}

      <div className="evidenceFooter">
        <span>
          {source.preview_url ? (
            <a href={source.preview_url} target="_blank" rel="noreferrer">
              Open page preview
            </a>
          ) : (
            "Page preview not available yet"
          )}
        </span>
        <button
          type="button"
          className="askTextButton"
          onClick={() => onOpenTransaction(source.transaction_id)}
        >
          Open {source.transaction_name} →
        </button>
      </div>
    </div>
  );
}
