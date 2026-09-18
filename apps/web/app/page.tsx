"use client";

import { FormEvent, useState } from "react";
import AskWorkspace, { AskScope } from "./components/AskWorkspace";
import AttentionIndicator from "./components/AttentionIndicator";
import HistoryWorkspace from "./components/HistoryWorkspace";
import OverviewWorkspace from "./components/OverviewWorkspace";

type DocumentType =
  | "purchase_order"
  | "delivery_order"
  | "invoice"
  | "receipt";

type Mode = "overview" | "document" | "transaction" | "history" | "ask";

type ExtractionResult = {
  document_id: string;
  filename: string;
  model: string;
  supplier_name: string | null;
  supplier_registration_no: string | null;
  document_number: string | null;
  document_date: string | null;
  currency: string | null;
  subtotal: number | null;
  tax: number | null;
  total: number | null;
  overall_confidence: number;
  line_items: Array<{
    description: string;
    sku: string | null;
    quantity: number | null;
    unit_price: number | null;
    line_total: number | null;
  }>;
  evidence: Array<{
    field_path: string;
    source_text: string;
    page: number | null;
    confidence: number;
  }>;
  review_reasons: string[];
};

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

type ReconciliationResult = {
  transaction_id: string;
  status: "matched" | "review_required" | "insufficient_data";
  documents: TransactionDocumentSummary[];
  summary: {
    issue_count: number;
    high: number;
    medium: number;
    low: number;
    matched_lines: number;
    review_lines: number;
  };
  lines: Array<{
    key: string;
    description: string;
    sku: string | null;
    po_quantity: number | null;
    delivered_quantity: number | null;
    invoice_quantity: number | null;
    po_unit_price: number | null;
    invoice_unit_price: number | null;
    status: "matched" | "review_required" | "unmatched";
  }>;
  issues: Array<{
    code: string;
    title: string;
    severity: "low" | "medium" | "high";
    item_description: string | null;
    expected: string | null;
    actual: string | null;
    delta: string | null;
    explanation: string;
    sources: Array<{
      document_id: string;
      filename: string;
      document_type: DocumentType;
      field_path: string;
      source_text: string;
      page: number | null;
      value: string | null;
    }>;
  }>;
  generated_at: string;
};

const API_URL =
  process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

const transactionDocumentTypes: Array<{
  type: "purchase_order" | "delivery_order" | "invoice";
  short: string;
  label: string;
  description: string;
}> = [
  {
    type: "purchase_order",
    short: "PO",
    label: "Purchase order",
    description: "What was agreed and ordered",
  },
  {
    type: "delivery_order",
    short: "DO",
    label: "Delivery order",
    description: "What was physically delivered",
  },
  {
    type: "invoice",
    short: "INV",
    label: "Invoice",
    description: "What the supplier billed",
  },
];

function money(currency: string | null, value: number | null) {
  if (value === null) return "—";
  return `${currency ?? ""} ${value.toFixed(2)}`.trim();
}

function qty(value: number | null) {
  return value === null ? "—" : String(value);
}

function documentTypeName(type: DocumentType) {
  return {
    purchase_order: "Purchase order",
    delivery_order: "Delivery order",
    invoice: "Invoice",
    receipt: "Receipt",
  }[type];
}

async function responseJson<T>(response: Response): Promise<T> {
  const payload = await response.json();
  if (!response.ok) {
    throw new Error(payload.detail ?? "Request failed.");
  }
  return payload as T;
}

export default function Home() {
  const [mode, setMode] = useState<Mode>("overview");
  const [askScope, setAskScope] = useState<AskScope>(null);
  const [historyFocusId, setHistoryFocusId] = useState<string | null>(null);
  const [supplierKey, setSupplierKey] = useState<string | null>(null);
  const [askRequest, setAskRequest] = useState<{ text: string; nonce: number } | null>(null);

  function openTransaction(id: string) {
    setHistoryFocusId(id);
    setMode("history");
  }

  function askAbout(question: string) {
    setAskScope(null);
    setAskRequest({ text: question, nonce: Date.now() });
    setMode("ask");
  }

  const [documentType, setDocumentType] =
    useState<DocumentType>("invoice");
  const [file, setFile] = useState<File | null>(null);
  const [result, setResult] = useState<ExtractionResult | null>(null);
  const [status, setStatus] = useState("Ready");
  const [error, setError] = useState("");

  const [transactionName, setTransactionName] = useState("");
  const [transactionFiles, setTransactionFiles] = useState<
    Record<"purchase_order" | "delivery_order" | "invoice", File | null>
  >({
    purchase_order: null,
    delivery_order: null,
    invoice: null,
  });
  const [transactionStatus, setTransactionStatus] = useState("Ready");
  const [transactionError, setTransactionError] = useState("");
  const [reconciliation, setReconciliation] =
    useState<ReconciliationResult | null>(null);
  const [busy, setBusy] = useState(false);

  async function uploadAndExtract(
    uploadFile: File,
    type: DocumentType
  ): Promise<ExtractionResult> {
    const form = new FormData();
    form.append("file", uploadFile);
    form.append("document_type", type);

    const uploadResponse = await fetch(`${API_URL}/api/v1/documents`, {
      method: "POST",
      body: form,
    });
    const uploaded = await responseJson<{ id: string }>(uploadResponse);

    const extractionResponse = await fetch(
      `${API_URL}/api/v1/documents/${uploaded.id}/extract`,
      { method: "POST" }
    );
    return responseJson<ExtractionResult>(extractionResponse);
  }

  async function submit(event: FormEvent) {
    event.preventDefault();

    if (!file) {
      setError("Choose a document first.");
      return;
    }

    setError("");
    setResult(null);
    setStatus("Reading document");

    try {
      const extraction = await uploadAndExtract(file, documentType);
      setResult(extraction);
      setStatus(
        extraction.review_reasons.length > 0 ? "Review needed" : "Review ready"
      );
    } catch (err) {
      setStatus("Ready");
      setError(err instanceof Error ? err.message : "Something went wrong.");
    }
  }

  async function runTransaction(event: FormEvent) {
    event.preventDefault();

    const missing = transactionDocumentTypes.filter(
      ({ type }) => !transactionFiles[type]
    );
    if (missing.length > 0) {
      setTransactionError(
        `Add ${missing.map((item) => item.label.toLowerCase()).join(", ")}.`
      );
      return;
    }

    setBusy(true);
    setTransactionError("");
    setReconciliation(null);

    try {
      setTransactionStatus("Creating transaction set");
      const transactionResponse = await fetch(`${API_URL}/api/v1/transactions`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: transactionName || null }),
      });
      const transaction = await responseJson<{ id: string }>(transactionResponse);

      for (const definition of transactionDocumentTypes) {
        const transactionFile = transactionFiles[definition.type];
        if (!transactionFile) continue;

        setTransactionStatus(`Reading ${definition.short}`);
        const extraction = await uploadAndExtract(
          transactionFile,
          definition.type
        );

        setTransactionStatus(`Linking ${definition.short}`);
        const attachResponse = await fetch(
          `${API_URL}/api/v1/transactions/${transaction.id}/documents/${extraction.document_id}`,
          { method: "POST" }
        );
        await responseJson(attachResponse);
      }

      setTransactionStatus("Matching quantities and prices");
      const reconciliationResponse = await fetch(
        `${API_URL}/api/v1/transactions/${transaction.id}/reconcile`,
        { method: "POST" }
      );
      const reconciled = await responseJson<ReconciliationResult>(
        reconciliationResponse
      );
      setReconciliation(reconciled);
      setTransactionStatus(
        reconciled.status === "matched" ? "Matched" : "Review ready"
      );
    } catch (err) {
      setTransactionStatus("Ready");
      setTransactionError(
        err instanceof Error ? err.message : "Transaction review failed."
      );
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="shell">
      <header className="topbar">
        <a className="brand" href="#">
          cermat.
        </a>
        <div className="topbarRight">
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
          <div className="status">
            <span className="statusDot" />
            local workspace
          </div>
        </div>
      </header>

      <section className="hero">
        <p className="eyebrow">AI OPERATIONS INTELLIGENCE</p>
        <h1>Read less. Catch more.</h1>
        <p className="lede">
          Turn purchase documents into structured records, cross-check the
          numbers, and surface exactly what needs human attention.
        </p>
      </section>

      <nav className="modeBar" aria-label="Workspace mode">
        <button
          type="button"
          className={`modeButton ${mode === "overview" ? "active" : ""}`}
          onClick={() => setMode("overview")}
        >
          Overview
        </button>
        <button
          type="button"
          className={`modeButton ${mode === "document" ? "active" : ""}`}
          onClick={() => setMode("document")}
        >
          Single document
        </button>
        <button
          type="button"
          className={`modeButton ${mode === "transaction" ? "active" : ""}`}
          onClick={() => setMode("transaction")}
        >
          Three-way match
        </button>
        <button
          type="button"
          className={`modeButton ${mode === "history" ? "active" : ""}`}
          onClick={() => setMode("history")}
        >
          Review history
        </button>
        <button
          type="button"
          className={`modeButton ${mode === "ask" ? "active" : ""}`}
          onClick={() => setMode("ask")}
        >
          Ask cermat.
        </button>
        <span className="modeHint">
          {mode === "overview"
            ? "Rules calculate · AI explains"
            : mode === "document"
            ? "Extract and inspect one source"
            : mode === "transaction"
              ? "PO ↔ DO ↔ Invoice"
              : mode === "history"
                ? "Open → Review → Resolved"
                : "Questions → Records → Evidence"}
        </span>
      </nav>

      {mode === "overview" ? (
        <OverviewWorkspace
          supplierKey={supplierKey}
          onSupplier={setSupplierKey}
          onOpenTransaction={openTransaction}
          onAsk={askAbout}
          onStart={setMode}
        />
      ) : mode === "document" ? (
        <section className="workspace">
          <form className="panel uploadPanel" onSubmit={submit}>
            <div className="panelLabel">01 / INTAKE</div>
            <h2>New document</h2>
            <p className="muted">
              Add one business document to begin the review workflow.
            </p>

            <label className="field">
              <span>Document type</span>
              <select
                value={documentType}
                onChange={(event) =>
                  setDocumentType(event.target.value as DocumentType)
                }
              >
                <option value="purchase_order">Purchase order</option>
                <option value="delivery_order">Delivery order</option>
                <option value="invoice">Invoice</option>
                <option value="receipt">Receipt</option>
              </select>
            </label>

            <label className="dropzone">
              <input
                type="file"
                accept=".pdf,.png,.jpg,.jpeg,.webp"
                onChange={(event) =>
                  setFile(event.target.files?.[0] ?? null)
                }
              />
              <span className="dropTitle">
                {file ? file.name : "Choose PDF or image"}
              </span>
              <span className="dropMeta">
                {file
                  ? `${Math.max(file.size / 1024, 1).toFixed(0)} KB`
                  : "PDF · PNG · JPG · WEBP · MAX 15 MB"}
              </span>
            </label>

            <button type="submit">Run document review</button>

            <div className="processing">
              <span>{status}</span>
              <span>{result ? "100%" : "—"}</span>
            </div>

            {error && <p className="error">{error}</p>}
          </form>

          <section className="panel resultPanel">
            <div className="panelLabel">02 / STRUCTURED OUTPUT</div>

            {!result ? (
              <div className="empty">
                <div className="emptyMark">C</div>
                <h2>Nothing to review yet.</h2>
                <p>
                  Upload a document and cermat. will place extracted fields,
                  confidence, and source evidence here.
                </p>
              </div>
            ) : (
              <>
                <div className="resultHeader">
                  <div>
                    <p className="resultKicker">Extraction complete</p>
                    <h2>{result.supplier_name ?? "Supplier not detected"}</h2>
                    <p className="resultMeta">
                      {result.filename} · {result.model}
                    </p>
                  </div>
                  <div className="confidence">
                    {(result.overall_confidence * 100).toFixed(0)}%
                    <span>confidence</span>
                  </div>
                </div>

                <div className="facts">
                  <div>
                    <span>Document</span>
                    <strong>{result.document_number ?? "—"}</strong>
                  </div>
                  <div>
                    <span>Date</span>
                    <strong>{result.document_date ?? "—"}</strong>
                  </div>
                  <div>
                    <span>Subtotal</span>
                    <strong>{money(result.currency, result.subtotal)}</strong>
                  </div>
                  <div>
                    <span>Total</span>
                    <strong>{money(result.currency, result.total)}</strong>
                  </div>
                </div>

                <div className="lineItems">
                  <div className="sectionTitleRow">
                    <span>Line items</span>
                    <span>{result.line_items.length}</span>
                  </div>
                  <div className="tableHead">
                    <span>Item</span>
                    <span>Qty</span>
                    <span>Unit</span>
                    <span>Total</span>
                  </div>
                  {result.line_items.length === 0 ? (
                    <div className="tableEmpty">
                      No reliable line items detected.
                    </div>
                  ) : (
                    result.line_items.map((item, index) => (
                      <div className="tableRow" key={index}>
                        <span>
                          {item.description}
                          {item.sku && <small>{item.sku}</small>}
                        </span>
                        <span>{item.quantity ?? "—"}</span>
                        <span>
                          {item.unit_price === null
                            ? "—"
                            : item.unit_price.toFixed(2)}
                        </span>
                        <span>
                          {item.line_total === null
                            ? "—"
                            : item.line_total.toFixed(2)}
                        </span>
                      </div>
                    ))
                  )}
                </div>

                {result.review_reasons.length > 0 && (
                  <div className="reviewBox">
                    <span className="evidenceLabel">Human review</span>
                    {result.review_reasons.map((reason) => (
                      <p key={reason}>{reason}</p>
                    ))}
                  </div>
                )}

                <div className="evidenceList">
                  <div className="sectionTitleRow">
                    <span>Evidence</span>
                    <span>{result.evidence.length}</span>
                  </div>
                  {result.evidence.slice(0, 8).map((evidence, index) => (
                    <div
                      className="evidence"
                      key={`${evidence.field_path}-${index}`}
                    >
                      <div>
                        <span className="evidenceLabel">
                          {evidence.field_path}
                        </span>
                        <strong>{evidence.source_text}</strong>
                      </div>
                      <span>
                        p.{evidence.page ?? "—"} ·{" "}
                        {(evidence.confidence * 100).toFixed(0)}%
                      </span>
                    </div>
                  ))}
                </div>
              </>
            )}
          </section>
        </section>
      ) : mode === "transaction" ? (
        <section className="workspace transactionWorkspace">
          <form className="panel transactionIntake" onSubmit={runTransaction}>
            <div className="panelLabel">01 / TRANSACTION SET</div>
            <h2>Three-way match</h2>
            <p className="muted">
              Supply the three records for one purchase. cermat. extracts them
              separately, then compares the structured values in code.
            </p>

            <label className="field">
              <span>Transaction name · optional</span>
              <input
                className="textInput"
                value={transactionName}
                onChange={(event) => setTransactionName(event.target.value)}
                placeholder="e.g. Office chairs · September"
              />
            </label>

            <div className="documentSlots">
              {transactionDocumentTypes.map((definition) => {
                const selected = transactionFiles[definition.type];
                return (
                  <label className="documentSlot" key={definition.type}>
                    <input
                      type="file"
                      accept=".pdf,.png,.jpg,.jpeg,.webp"
                      disabled={busy}
                      onChange={(event) =>
                        setTransactionFiles((current) => ({
                          ...current,
                          [definition.type]: event.target.files?.[0] ?? null,
                        }))
                      }
                    />
                    <span className="slotCode">{definition.short}</span>
                    <span className="slotBody">
                      <strong>{definition.label}</strong>
                      <small>
                        {selected ? selected.name : definition.description}
                      </small>
                    </span>
                    <span className={`slotState ${selected ? "ready" : ""}`}>
                      {selected ? "Added" : "Add"}
                    </span>
                  </label>
                );
              })}
            </div>

            <button type="submit" disabled={busy}>
              {busy ? "Running three-way match…" : "Run three-way match"}
            </button>

            <div className="processing">
              <span>{transactionStatus}</span>
              <span>{reconciliation ? "100%" : "—"}</span>
            </div>

            {transactionError && (
              <p className="error">{transactionError}</p>
            )}

            <div className="engineNote">
              <span>ENGINE RULE</span>
              AI reads the documents. Deterministic code decides the variance.
            </div>
          </form>

          <section className="panel reconciliationPanel">
            <div className="panelLabel">02 / RECONCILIATION</div>

            {!reconciliation ? (
              <div className="empty reconciliationEmpty">
                <div className="emptyMark matchMark">↔</div>
                <h2>Waiting for a transaction.</h2>
                <p>
                  Add a purchase order, delivery order, and invoice. Matching
                  results will appear here with the source values attached.
                </p>
              </div>
            ) : (
              <>
                <div className="reconciliationHeader">
                  <div>
                    <p className="resultKicker">Three-way match complete</p>
                    <h2>
                      {reconciliation.status === "matched"
                        ? "Documents agree."
                        : reconciliation.status === "insufficient_data"
                          ? "More data needed."
                          : `${reconciliation.summary.issue_count} item${
                              reconciliation.summary.issue_count === 1 ? "" : "s"
                            } need attention.`}
                    </h2>
                  </div>
                  <div
                    className={`matchStatus ${reconciliation.status}`}
                  >
                    {reconciliation.status.replaceAll("_", " ")}
                  </div>
                </div>

                <div className="documentStrip">
                  {reconciliation.documents
                    .filter((document) => document.document_type !== "receipt")
                    .map((document) => (
                      <div className="documentCard" key={document.id}>
                        <span className="documentCardType">
                          {documentTypeName(document.document_type)}
                        </span>
                        <strong>{document.document_number ?? "No number"}</strong>
                        <small>{document.filename}</small>
                        <div>
                          <span>
                            {money(document.currency, document.total)}
                          </span>
                          <span>
                            {document.overall_confidence === null
                              ? "—"
                              : `${(
                                  document.overall_confidence * 100
                                ).toFixed(0)}%`}
                          </span>
                        </div>
                      </div>
                    ))}
                </div>

                <div className="summaryRail">
                  <div>
                    <span>Matched lines</span>
                    <strong>{reconciliation.summary.matched_lines}</strong>
                  </div>
                  <div>
                    <span>Review lines</span>
                    <strong>{reconciliation.summary.review_lines}</strong>
                  </div>
                  <div>
                    <span>High</span>
                    <strong>{reconciliation.summary.high}</strong>
                  </div>
                  <div>
                    <span>Medium</span>
                    <strong>{reconciliation.summary.medium}</strong>
                  </div>
                </div>

                <div className="matchLines">
                  <div className="sectionTitleRow">
                    <span>Line matching</span>
                    <span>{reconciliation.lines.length}</span>
                  </div>
                  <div className="matchTableHead">
                    <span>Item</span>
                    <span>Ordered</span>
                    <span>Delivered</span>
                    <span>Invoiced</span>
                    <span>PO price</span>
                    <span>Invoice</span>
                  </div>
                  {reconciliation.lines.map((line) => (
                    <div className="matchTableRow" key={line.key}>
                      <span className="matchItem">
                        <i className={`lineDot ${line.status}`} />
                        <span>
                          {line.description}
                          {line.sku && <small>{line.sku}</small>}
                        </span>
                      </span>
                      <span>{qty(line.po_quantity)}</span>
                      <span>{qty(line.delivered_quantity)}</span>
                      <span>{qty(line.invoice_quantity)}</span>
                      <span>
                        {line.po_unit_price === null
                          ? "—"
                          : line.po_unit_price.toFixed(2)}
                      </span>
                      <span>
                        {line.invoice_unit_price === null
                          ? "—"
                          : line.invoice_unit_price.toFixed(2)}
                      </span>
                    </div>
                  ))}
                </div>

                <div className="issueList">
                  <div className="sectionTitleRow">
                    <span>Exceptions</span>
                    <span>{reconciliation.issues.length}</span>
                  </div>

                  {reconciliation.issues.length === 0 ? (
                    <div className="clearState">
                      No deterministic mismatches were found in the extracted
                      values.
                    </div>
                  ) : (
                    reconciliation.issues.map((issue, index) => (
                      <article className="issueCard" key={`${issue.code}-${index}`}>
                        <div className="issueTopline">
                          <span className={`severity ${issue.severity}`}>
                            {issue.severity}
                          </span>
                          <span>{issue.code.replaceAll("_", " ")}</span>
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
                            {issue.sources.map((source, sourceIndex) => (
                              <div
                                className="sourceRow"
                                key={`${source.document_id}-${source.field_path}-${sourceIndex}`}
                              >
                                <span className="sourceType">
                                  {documentTypeName(source.document_type)}
                                </span>
                                <span className="sourceQuote">
                                  {source.source_text}
                                </span>
                                <span className="sourcePage">
                                  p.{source.page ?? "—"}
                                </span>
                              </div>
                            ))}
                          </div>
                        )}
                      </article>
                    ))
                  )}
                </div>
              </>
            )}
          </section>
        </section>
      ) : mode === "history" ? (
        <HistoryWorkspace
          focusId={historyFocusId}
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
    </main>
  );
}
