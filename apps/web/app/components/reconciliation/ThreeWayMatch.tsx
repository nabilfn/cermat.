"use client";

import { FormEvent, useId, useState } from "react";
import { api, errorMessage } from "../../lib/api";
import { documentTypeName, plainMoney } from "../../lib/format";
import type {
  DocumentRecord,
  ReconciliationResult,
  TransactionContext,
  TransactionRecordLite,
} from "../../lib/types";
import { confidenceLookup } from "../shared/Evidence";
import { ACCEPT } from "../shared/FileDrop";
import { IssueCard } from "../shared/IssueCard";

type Kind = "purchase_order" | "delivery_order" | "invoice";

const SLOTS: Array<{ type: Kind; short: string; label: string; description: string }> = [
  { type: "purchase_order", short: "PO", label: "Purchase order", description: "What was agreed and ordered" },
  { type: "delivery_order", short: "DO", label: "Delivery order", description: "What was physically delivered" },
  { type: "invoice", short: "INV", label: "Invoice", description: "What the supplier billed" },
];

/** Completed steps survive a failure, so "Retry" resumes instead of starting over. */
type Progress = {
  transactionId: string | null;
  documents: Partial<Record<Kind, { id: string; extracted: boolean; attached: boolean }>>;
};

const EMPTY: Progress = { transactionId: null, documents: {} };

export default function ThreeWayMatch({ onOpenTransaction }: { onOpenTransaction: (id: string) => void }) {
  const [name, setName] = useState("");
  const [files, setFiles] = useState<Record<Kind, File | null>>({
    purchase_order: null,
    delivery_order: null,
    invoice: null,
  });
  const [progress, setProgress] = useState<Progress>(EMPTY);
  const [status, setStatus] = useState("Ready");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<ReconciliationResult | null>(null);
  const [context, setContext] = useState<TransactionContext | null>(null);

  function chooseFile(type: Kind, file: File | null) {
    setFiles((current) => ({ ...current, [type]: file }));
    // A different file for a slot invalidates that slot's earlier progress.
    setProgress((current) => {
      const documents = { ...current.documents };
      delete documents[type];
      return { ...current, documents };
    });
  }

  async function run(event?: FormEvent) {
    event?.preventDefault();
    const missing = SLOTS.filter(({ type }) => !files[type] && !progress.documents[type]);
    if (missing.length) {
      setError(`Add the ${missing.map((m) => m.label.toLowerCase()).join(", ")}.`);
      return;
    }
    setBusy(true);
    setError("");
    setResult(null);
    const state: Progress = { transactionId: progress.transactionId, documents: { ...progress.documents } };
    try {
      if (!state.transactionId) {
        setStatus("Creating transaction…");
        const txn = await api<TransactionRecordLite>("/api/v1/transactions", {
          method: "POST",
          json: { name: name.trim() || null },
        });
        state.transactionId = txn.id;
        setProgress({ ...state });
      }
      for (const slot of SLOTS) {
        let doc = state.documents[slot.type];
        if (!doc) {
          setStatus(`Uploading ${slot.short}…`);
          const form = new FormData();
          form.append("file", files[slot.type] as File);
          form.append("document_type", slot.type);
          const uploaded = await api<DocumentRecord>("/api/v1/documents", { method: "POST", form });
          doc = { id: uploaded.id, extracted: false, attached: false };
          state.documents[slot.type] = doc;
          setProgress({ ...state, documents: { ...state.documents } });
        }
        if (!doc.extracted) {
          setStatus(`Extracting ${slot.short} fields…`);
          await api(`/api/v1/documents/${doc.id}/extract`, { method: "POST" });
          doc.extracted = true;
          setProgress({ ...state, documents: { ...state.documents } });
        }
        if (!doc.attached) {
          setStatus(`Linking ${slot.short}…`);
          await api(`/api/v1/transactions/${state.transactionId}/documents/${doc.id}`, { method: "POST" });
          doc.attached = true;
          setProgress({ ...state, documents: { ...state.documents } });
        }
      }
      setStatus("Comparing documents…");
      const reconciled = await api<ReconciliationResult>(`/api/v1/transactions/${state.transactionId}/reconcile`, {
        method: "POST",
      });
      setResult(reconciled);
      setStatus(reconciled.status === "matched" ? "Matched" : "Review ready");
      setContext(await api<TransactionContext>(`/api/v1/transactions/${state.transactionId}/context`).catch(() => null));
      setProgress(EMPTY);
      setFiles({ purchase_order: null, delivery_order: null, invoice: null });
    } catch (err) {
      setStatus("Stopped");
      setError(errorMessage(err, "The transaction could not be processed."));
    } finally {
      setBusy(false);
    }
  }

  const resumable = !busy && error && progress.transactionId;
  const lookup = confidenceLookup(context?.sources);

  return (
    <section className="workspace transactionWorkspace">
      <form className="panel transactionIntake" onSubmit={run}>
        <div className="panelLabel">01 / Transaction set</div>
        <h2>Three-way match</h2>
        <p className="muted">
          Supply the three records for one purchase. cermat. extracts each one, then compares the
          structured values in deterministic code.
        </p>

        <label className="field">
          <span>Transaction name · optional</span>
          <input
            className="textInput"
            value={name}
            maxLength={120}
            disabled={busy || progress.transactionId !== null}
            onChange={(event) => setName(event.target.value)}
            placeholder="e.g. Office chairs · September"
          />
        </label>

        <div className="documentSlots">
          {SLOTS.map((slot) => (
            <DocumentSlot
              key={slot.type}
              slot={slot}
              file={files[slot.type]}
              done={progress.documents[slot.type]}
              disabled={busy}
              onChange={(file) => chooseFile(slot.type, file)}
            />
          ))}
        </div>

        <button type="submit" disabled={busy}>
          {busy ? status : resumable ? "Retry from the failed step" : "Run three-way match"}
        </button>

        <div className={`processing ${busy ? "active" : ""}`} aria-live="polite">
          <span>{status}</span>
        </div>

        {error && (
          <div className="inlineError" role="alert">
            <p>{error}</p>
            {resumable && progress.transactionId && (
              <p className="inlineHint">
                Completed steps are kept.{" "}
                <button type="button" className="askTextButton" onClick={() => onOpenTransaction(progress.transactionId as string)}>
                  Open the partial transaction
                </button>
              </p>
            )}
          </div>
        )}

        <div className="engineNote">
          <span>Engine rule</span>
          AI reads the documents. Deterministic code decides the variance.
        </div>
      </form>

      <section className="panel reconciliationPanel" aria-live="polite">
        <div className="panelLabel">02 / Reconciliation</div>

        {!result ? (
          <div className="empty reconciliationEmpty">
            <div className="emptyMark matchMark" aria-hidden="true">↔</div>
            <h2>{busy ? status : "Waiting for a transaction."}</h2>
            <p>
              Add a purchase order, delivery order and invoice. Matching results appear here with
              the source values attached.
            </p>
          </div>
        ) : (
          <>
            <div className="reconciliationHeader">
              <div>
                <p className="resultKicker">Three-way match complete</p>
                <h2>
                  {result.status === "matched"
                    ? "Documents agree."
                    : result.status === "insufficient_data"
                      ? "More data needed."
                      : `${result.summary.issue_count} item${result.summary.issue_count === 1 ? "" : "s"} need attention.`}
                </h2>
              </div>
              <div className={`matchStatus ${result.status}`}>{result.status.replaceAll("_", " ")}</div>
            </div>

            <div className="documentStrip">
              {result.documents
                .filter((document) => document.document_type !== "receipt")
                .map((document) => (
                  <div className="documentCard" key={document.id}>
                    <span className="documentCardType">{documentTypeName(document.document_type)}</span>
                    <strong>{document.document_number ?? "No number"}</strong>
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

            <dl className="summaryRail">
              <div>
                <dt>Matched lines</dt>
                <dd>{result.summary.matched_lines}</dd>
              </div>
              <div>
                <dt>Review lines</dt>
                <dd>{result.summary.review_lines}</dd>
              </div>
              <div>
                <dt>High</dt>
                <dd>{result.summary.high}</dd>
              </div>
              <div>
                <dt>Medium</dt>
                <dd>{result.summary.medium}</dd>
              </div>
            </dl>

            <div className="matchLines">
              <div className="sectionTitleRow">
                <span>Line matching</span>
                <span>{result.lines.length}</span>
              </div>
              <div className="tableScroll">
                <table className="dataTable matchTable">
                  <thead>
                    <tr>
                      <th scope="col">Item</th>
                      <th scope="col">Ordered</th>
                      <th scope="col">Delivered</th>
                      <th scope="col">Invoiced</th>
                      <th scope="col">PO price</th>
                      <th scope="col">Invoice</th>
                    </tr>
                  </thead>
                  <tbody>
                    {result.lines.map((line) => (
                      <tr key={line.key}>
                        <td>
                          <span className="matchItem">
                            <i className={`lineDot ${line.status}`} aria-hidden="true" />
                            <span>
                              {line.description}
                              {line.sku && <small>{line.sku}</small>}
                              <small className="visuallyHidden">{line.status.replaceAll("_", " ")}</small>
                            </span>
                          </span>
                        </td>
                        <td>{line.po_quantity ?? "—"}</td>
                        <td>{line.delivered_quantity ?? "—"}</td>
                        <td>{line.invoice_quantity ?? "—"}</td>
                        <td>{line.po_unit_price === null ? "—" : line.po_unit_price.toFixed(2)}</td>
                        <td>{line.invoice_unit_price === null ? "—" : line.invoice_unit_price.toFixed(2)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>

            <div className="issueList">
              <div className="sectionTitleRow">
                <span>Exceptions</span>
                <span>{result.issues.length}</span>
              </div>
              {result.issues.length === 0 ? (
                <div className="clearState">No deterministic mismatches were found in the extracted values.</div>
              ) : (
                result.issues.map((issue, index) => <IssueCard key={`${issue.code}-${index}`} issue={issue} lookup={lookup} />)
              )}
              <button type="button" className="secondaryButton inlineAction" onClick={() => onOpenTransaction(result.transaction_id)}>
                Review in history →
              </button>
            </div>
          </>
        )}
      </section>
    </section>
  );
}

function DocumentSlot({
  slot,
  file,
  done,
  disabled,
  onChange,
}: {
  slot: (typeof SLOTS)[number];
  file: File | null;
  done: Progress["documents"][Kind];
  disabled: boolean;
  onChange: (file: File | null) => void;
}) {
  const id = useId();
  const state = done?.attached ? "Linked" : done?.extracted ? "Extracted" : done ? "Uploaded" : file ? "Added" : "Add";
  return (
    <label className={`documentSlot ${disabled ? "disabled" : ""}`} htmlFor={id}>
      <input
        id={id}
        className="visuallyHidden"
        type="file"
        accept={ACCEPT}
        disabled={disabled}
        onChange={(event) => onChange(event.target.files?.[0] ?? null)}
      />
      <span className="slotCode" aria-hidden="true">{slot.short}</span>
      <span className="slotBody">
        <strong>{slot.label}</strong>
        <small title={file?.name}>{file ? file.name : slot.description}</small>
      </span>
      <span className={`slotState ${file || done ? "ready" : ""}`}>{state}</span>
    </label>
  );
}
