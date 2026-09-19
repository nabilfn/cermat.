# Demo script (2–3 minutes)

The story: **AI reads. Rules verify. Evidence proves. cermat. explains. Humans decide.**

## Before recording

- `docker compose up --build`, then sign up at http://localhost:3000.
- For the live upload part, set `OPENAI_API_KEY` and prepare three files for one purchase: a PO (10 chairs at RM42), a delivery order (8 delivered) and an invoice (10 invoiced at RM44). Use any PDFs or photos you have the rights to.
- Click **Load demo workspace** once, so the intelligence views have history. It opens a separate workspace labelled **DEMO DATA**. Your real workspace stays empty until you upload.

## Walkthrough

1. **Upload** (Three-way match, real workspace). Add the PO, DO and invoice and run the match. Point out the stage labels: *Uploading PO… Extracting PO fields… Comparing documents…*
2. **Extraction with evidence.** In *Single document* (or the document strip), each value has a source snippet, page and confidence. "AI reads the documents."
3. **Deterministic reconciliation** finds two exceptions:
   - *Invoiced quantity differs from delivered quantity*: expected 8, actual 10, difference +2 (high severity).
   - *Invoice price differs from purchase order*: RM42.00 → RM44.00, **+RM2.00 (+4.76%)**.
   "The model never decides a variance; code does."
4. **Inspect the evidence.** Each exception lists the delivery-order and invoice lines, field, page and confidence, with **Open ↗** to the original file.
5. **Resolve one issue with a note** in *Review history*, e.g. "Supplier issued credit note". The activity list shows *Resolved by <you> · time*.
6. **Overview updates.** Switch to the demo workspace for richer history: open exceptions and billed variance per currency, and the **priority queue** (expand an item to show the reasons behind its score). Then patterns, e.g. "ABC Supplies generated 7 price discrepancies in the last 90 days", and anomalies, e.g. "+18.20% price mismatch; supplier median 4.4%; threshold 10%".
7. **Ask cermat.:** *"What needs my attention?"* The answer is built from records, with evidence chips; click one to see the document, page, snippet and confidence. Optionally ask *"Why is PO-2026-097 flagged?"* or *"What changed in the last 30 days?"*
8. **Close:** "Every number came from deterministic code over persisted records; the AI read the documents and explained the results; a person made the decision."

## Demo data facts

The demo workspace is created by `app/services/demo.py`. It holds about 23 backdated transactions across five suppliers (MYR, USD and SGD). They cover clean matches, price and quantity discrepancies, a supplier-name mismatch, a currency mismatch, resolved and overdue issues, recurring supplier and item patterns, an 18.2% price anomaly, an invoice far above the supplier median, a new supplier with a high-severity exception, missing invoices and a low-confidence extraction. The records are pre-extracted and labelled `demo-data`, but reconciliation, review issues, metrics, patterns and anomalies are computed by the real engine.

- **Reset**: the **Reset demo** link in the banner, or `docker compose exec api python -m scripts.seed_demo you@example.com --reset`. Only the demo workspace is affected.
- Demo records have no source files, so their evidence shows snippets without an **Open** link.
