# Product definition

## One-line pitch

cermat. is an AI operations agent that reads SME business documents, reconciles purchase records, and keeps an evidence-backed human review trail for every exception.

## Initial user

An SME operations or finance staff member who currently checks purchase orders, delivery orders, invoices, and receipts manually.

## Core job-to-be-done

> Given the documents for a purchase, tell me whether what was ordered, delivered, invoiced, and paid matches; show me the evidence for any mismatch; and keep track of what my team has already reviewed.

## Current workflow

1. Document intake
2. AI extraction
3. Structured validation
4. Deterministic three-way reconciliation
5. Persistent exception queue
6. Human resolution/reopen
7. Searchable transaction history
8. Ask cermat.: evidence-grounded questions over all of the above

## Workflow states

- Open
- Needs review
- Resolved
- Matched

## Product principles

- Never silently mutate extracted data.
- Preserve source evidence for important fields.
- Separate model extraction from deterministic business rules.
- Preserve human decisions across reconciliation reruns.
- A materially changed discrepancy must become a new review item.
- Keep history operational and compact rather than turning it into a decorative analytics dashboard.
- Retrieve facts deterministically; let AI explain them clearly.

## Ask cermat.

A business-data assistant, not a general chatbot. It answers operational questions from persisted records only: what needs attention, why a transaction is flagged, which suppliers have open discrepancies, what was resolved and when.

- **Grounded**: every figure comes from records or from Python calculations on them. Model wording that introduces an unknown number is discarded.
- **Traceable**: factual lines carry source chips that open the document, page, field, extracted value, snippet and confidence.
- **Scoped**: questions run across the workspace, or against one transaction when opened from Review history.
- **Read-only**: it cannot resolve, edit or delete. Those stay explicit, human UI actions.
- **Honest about gaps**: unsupported questions, unknown transactions or suppliers, and empty results are stated plainly rather than filled in.
- **Report, not chat**: answers are a headline plus short supporting lines, then the exceptions and transactions behind them.
