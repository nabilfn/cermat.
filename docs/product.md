# Product definition

## One-line pitch

cermat. is an AI operations agent that reads SME business documents, converts them into structured records, cross-checks related documents, and explains discrepancies with evidence.

## Initial user

An SME operations or finance staff member who currently checks purchase orders, delivery orders, invoices, and receipts manually.

## MVP job-to-be-done

> "Given the documents for a purchase, tell me whether what was ordered, delivered, invoiced, and paid matches — and show me exactly where anything differs."

## MVP document types

- Purchase Order (PO)
- Delivery Order (DO)
- Invoice
- Receipt

## MVP outputs

- Supplier
- document number
- document date
- currency
- subtotal / tax / total
- line items
- quantities
- unit prices
- linked documents
- discrepancy flags
- evidence references
- confidence

## AI principles

- Never silently mutate extracted data.
- Preserve source evidence for every important extracted field.
- Separate deterministic reconciliation rules from model-generated explanations.
- Ask for human review when confidence is low or documents conflict.
- Treat uploaded documents as untrusted input.
