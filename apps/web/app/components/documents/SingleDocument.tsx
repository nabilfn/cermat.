"use client";

import { FormEvent, useState } from "react";
import { api, ApiError, errorMessage } from "../../lib/api";
import { bytes, fieldLabel, plainMoney } from "../../lib/format";
import type { DocumentRecord, DocumentType, ExtractionResult } from "../../lib/types";
import { FileDrop } from "../shared/FileDrop";

type Stage = "idle" | "uploading" | "extracting" | "done" | "failed";

const STAGE_LABEL: Record<Stage, string> = {
  idle: "Ready",
  uploading: "Uploading document…",
  extracting: "Extracting fields…",
  done: "Extraction complete",
  failed: "Stopped",
};

export default function SingleDocument() {
  const [documentType, setDocumentType] = useState<DocumentType>("invoice");
  const [file, setFile] = useState<File | null>(null);
  const [uploaded, setUploaded] = useState<DocumentRecord | null>(null);
  const [result, setResult] = useState<ExtractionResult | null>(null);
  const [stage, setStage] = useState<Stage>("idle");
  const [error, setError] = useState("");
  const busy = stage === "uploading" || stage === "extracting";

  async function extract(document: DocumentRecord) {
    setStage("extracting");
    try {
      const extraction = await api<ExtractionResult>(`/api/v1/documents/${document.id}/extract`, { method: "POST" });
      setResult(extraction);
      setStage("done");
    } catch (err) {
      setStage("failed");
      setError(
        err instanceof ApiError && err.code === "AI_PROVIDER_ERROR"
          ? `${err.message} The file is saved — retry when the provider is available.`
          : errorMessage(err)
      );
    }
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!file) {
      setError("Choose a document first.");
      return;
    }
    setError("");
    setResult(null);
    setUploaded(null);
    setStage("uploading");
    try {
      const form = new FormData();
      form.append("file", file);
      form.append("document_type", documentType);
      const document = await api<DocumentRecord>("/api/v1/documents", { method: "POST", form });
      setUploaded(document);
      await extract(document);
    } catch (err) {
      setStage("failed");
      setError(errorMessage(err));
    }
  }

  return (
    <section className="workspace">
      <form className="panel uploadPanel" onSubmit={submit}>
        <div className="panelLabel">01 / Intake</div>
        <h2>New document</h2>
        <p className="muted">Extract one business document and inspect every value against its source.</p>

        <label className="field">
          <span>Document type</span>
          <select value={documentType} onChange={(event) => setDocumentType(event.target.value as DocumentType)} disabled={busy}>
            <option value="purchase_order">Purchase order</option>
            <option value="delivery_order">Delivery order</option>
            <option value="invoice">Invoice</option>
            <option value="receipt">Receipt</option>
          </select>
        </label>

        <FileDrop file={file} onChange={setFile} disabled={busy} />

        <button type="submit" disabled={busy}>
          {busy ? STAGE_LABEL[stage] : "Run document review"}
        </button>

        <div className={`processing ${busy ? "active" : ""}`} aria-live="polite">
          <span>{STAGE_LABEL[stage]}</span>
          {uploaded && <span>{uploaded.page_count ?? 1} p · {bytes(uploaded.size_bytes)}</span>}
        </div>

        {error && (
          <div className="inlineError" role="alert">
            <p>{error}</p>
            {stage === "failed" && uploaded && !result && (
              <button type="button" className="secondaryButton" onClick={() => void extract(uploaded)}>
                Retry extraction
              </button>
            )}
          </div>
        )}
      </form>

      <section className="panel resultPanel" aria-live="polite">
        <div className="panelLabel">02 / Structured output</div>

        {!result ? (
          <div className="empty">
            <div className="emptyMark" aria-hidden="true">C</div>
            <h2>{busy ? STAGE_LABEL[stage] : "Nothing to review yet."}</h2>
            <p>
              Upload a document and cermat. will place extracted fields, confidence and source
              evidence here.
            </p>
          </div>
        ) : (
          <>
            <div className="resultHeader">
              <div className="minWidth0">
                <p className="resultKicker">Extraction complete</p>
                <h2 className="breakWord">{result.supplier_name ?? "Supplier not detected"}</h2>
                <p className="resultMeta breakWord">
                  {result.filename} · {result.model}
                </p>
              </div>
              <div className="confidence">
                {(result.overall_confidence * 100).toFixed(0)}%<span>confidence</span>
              </div>
            </div>

            <dl className="facts">
              <div>
                <dt>Document</dt>
                <dd>{result.document_number ?? "—"}</dd>
              </div>
              <div>
                <dt>Date</dt>
                <dd>{result.document_date ?? "—"}</dd>
              </div>
              <div>
                <dt>Subtotal</dt>
                <dd>{plainMoney(result.currency, result.subtotal)}</dd>
              </div>
              <div>
                <dt>Total</dt>
                <dd>{plainMoney(result.currency, result.total)}</dd>
              </div>
            </dl>

            <div className="lineItems">
              <div className="sectionTitleRow">
                <span>Line items</span>
                <span>{result.line_items.length}</span>
              </div>
              <div className="tableScroll">
                <table className="dataTable">
                  <thead>
                    <tr>
                      <th scope="col">Item</th>
                      <th scope="col">Qty</th>
                      <th scope="col">Unit</th>
                      <th scope="col">Total</th>
                    </tr>
                  </thead>
                  <tbody>
                    {result.line_items.length === 0 ? (
                      <tr>
                        <td colSpan={4} className="tableEmpty">No reliable line items detected.</td>
                      </tr>
                    ) : (
                      result.line_items.map((item, index) => (
                        <tr key={index}>
                          <td>
                            {item.description}
                            {item.sku && <small>{item.sku}</small>}
                          </td>
                          <td>{item.quantity ?? "—"}</td>
                          <td>{item.unit_price === null ? "—" : item.unit_price.toFixed(2)}</td>
                          <td>{item.line_total === null ? "—" : item.line_total.toFixed(2)}</td>
                        </tr>
                      ))
                    )}
                  </tbody>
                </table>
              </div>
            </div>

            {result.review_reasons.length > 0 && (
              <div className="reviewBox">
                <span className="evidenceLabel">Needs human review</span>
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
              {result.evidence.map((evidence, index) => (
                <div className="evidence" key={`${evidence.field_path}-${index}`}>
                  <div className="minWidth0">
                    <span className="evidenceLabel">{fieldLabel(evidence.field_path)}</span>
                    <strong className="breakWord">{evidence.source_text}</strong>
                  </div>
                  <span>
                    p.{evidence.page ?? "—"} · {(evidence.confidence * 100).toFixed(0)}%
                  </span>
                </div>
              ))}
            </div>
          </>
        )}
      </section>
    </section>
  );
}
