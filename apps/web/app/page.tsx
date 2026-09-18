"use client";

import { FormEvent, useState } from "react";

type DocumentType =
  | "purchase_order"
  | "delivery_order"
  | "invoice"
  | "receipt";

type ExtractionResult = {
  supplier_name: string | null;
  document_number: string | null;
  currency: string;
  subtotal: number | null;
  tax: number | null;
  total: number | null;
  confidence: number;
  line_items: Array<{
    description: string;
    quantity: number;
    unit_price: number;
    line_total: number;
  }>;
  evidence: Array<{
    field: string;
    source_text: string;
    page: number | null;
    confidence: number;
  }>;
  note: string;
};

const API_URL =
  process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

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
      setStatus("Extracting");

      const extractionResponse = await fetch(
        `${API_URL}/api/v1/documents/${uploaded.id}/extract`,
        { method: "POST" }
      );

      if (!extractionResponse.ok) {
        throw new Error("Extraction failed.");
      }

      const extraction = await extractionResponse.json();
      setResult(extraction);
      setStatus("Review ready");
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
                : "PDF · PNG · JPG · WEBP"}
            </span>
          </label>

          <button type="submit">Run document review</button>

          <div className="processing">
            <span>{status}</span>
            <span>{status === "Review ready" ? "100%" : "—"}</span>
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
                  <h2>{result.supplier_name}</h2>
                </div>
                <div className="confidence">
                  {(result.confidence * 100).toFixed(0)}%
                  <span>confidence</span>
                </div>
              </div>

              <div className="facts">
                <div>
                  <span>Document</span>
                  <strong>{result.document_number}</strong>
                </div>
                <div>
                  <span>Subtotal</span>
                  <strong>
                    {result.currency} {result.subtotal?.toFixed(2)}
                  </strong>
                </div>
                <div>
                  <span>Tax</span>
                  <strong>
                    {result.currency} {result.tax?.toFixed(2)}
                  </strong>
                </div>
                <div>
                  <span>Total</span>
                  <strong>
                    {result.currency} {result.total?.toFixed(2)}
                  </strong>
                </div>
              </div>

              <div className="lineItems">
                <div className="tableHead">
                  <span>Item</span>
                  <span>Qty</span>
                  <span>Unit</span>
                  <span>Total</span>
                </div>
                {result.line_items.map((item, index) => (
                  <div className="tableRow" key={index}>
                    <span>{item.description}</span>
                    <span>{item.quantity}</span>
                    <span>{item.unit_price.toFixed(2)}</span>
                    <span>{item.line_total.toFixed(2)}</span>
                  </div>
                ))}
              </div>

              <div className="evidence">
                <div>
                  <span className="evidenceLabel">Evidence</span>
                  <strong>{result.evidence[0]?.source_text}</strong>
                </div>
                <span>
                  p.{result.evidence[0]?.page ?? "—"} ·{" "}
                  {(
                    (result.evidence[0]?.confidence ?? 0) * 100
                  ).toFixed(0)}
                  %
                </span>
              </div>

              <p className="stubNote">{result.note}</p>
            </>
          )}
        </section>
      </section>
    </main>
  );
}
