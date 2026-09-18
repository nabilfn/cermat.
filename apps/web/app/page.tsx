"use client";

import { FormEvent, useState } from "react";

type DocumentType =
  | "purchase_order"
  | "delivery_order"
  | "invoice"
  | "receipt";

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

const API_URL =
  process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

function money(currency: string | null, value: number | null) {
  if (value === null) return "—";
  return `${currency ?? ""} ${value.toFixed(2)}`.trim();
}

export default function Home() {
  const [documentType, setDocumentType] =
    useState<DocumentType>("invoice");
  const [file, setFile] = useState<File | null>(null);
  const [result, setResult] = useState<ExtractionResult | null>(null);
  const [status, setStatus] = useState("Ready");
  const [error, setError] = useState("");

  async function submit(event: FormEvent) {
    event.preventDefault();

    if (!file) {
      setError("Choose a document first.");
      return;
    }

    setError("");
    setResult(null);
    setStatus("Uploading");

    try {
      const form = new FormData();
      form.append("file", file);
      form.append("document_type", documentType);

      const uploadResponse = await fetch(`${API_URL}/api/v1/documents`, {
        method: "POST",
        body: form,
      });

      if (!uploadResponse.ok) {
        const payload = await uploadResponse.json();
        throw new Error(payload.detail ?? "Upload failed.");
      }

      const uploaded = await uploadResponse.json();
      setStatus("Reading document");

      const extractionResponse = await fetch(
        `${API_URL}/api/v1/documents/${uploaded.id}/extract`,
        { method: "POST" }
      );

      if (!extractionResponse.ok) {
        const payload = await extractionResponse.json();
        throw new Error(payload.detail ?? "Extraction failed.");
      }

      const extraction = await extractionResponse.json();
      setResult(extraction);
      setStatus(
        extraction.review_reasons.length > 0 ? "Review needed" : "Review ready"
      );
    } catch (err) {
      setStatus("Ready");
      setError(err instanceof Error ? err.message : "Something went wrong.");
    }
  }

  return (
    <main className="shell">
      <header className="topbar">
        <a className="brand" href="#">
          cermat.
        </a>
        <div className="status">
          <span className="statusDot" />
          local workspace
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
                  <div className="tableEmpty">No reliable line items detected.</div>
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
                  <div className="evidence" key={`${evidence.field_path}-${index}`}>
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
    </main>
  );
}
