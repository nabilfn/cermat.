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
