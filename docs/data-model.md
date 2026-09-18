# Data model — Phase 5

## documents

- id
- filename
- document_type
- MIME type / size
- status
- storage path
- extraction model
- extraction JSON
- created / updated timestamps

## transaction_sets

- id
- name
- status
- last reconciliation JSON
- created / updated timestamps

Transaction status may be:

- collecting
- ready
- matched
- review_required
- insufficient_data
- resolved

## transaction_documents

Links one transaction to at most one document of each supported type.

- id
- transaction_id
- document_id
- document_type
- created_at

## review_issues

Persists the human-review state for deterministic reconciliation exceptions.

- id
- transaction_id
- issue_key
- code
- title
- severity
- status (`open` / `resolved`)
- resolution_note
- payload (full evidence-backed reconciliation issue)
- active
- resolved_at
- created / updated timestamps

`(transaction_id, issue_key)` is unique.

### Active vs resolved

`status` represents the human decision. `active` represents whether the exception still exists in the latest reconciliation.

This distinction lets cermat. preserve historical review decisions without showing stale exceptions in the current queue.

## Ask cermat. (no new tables)

Ask cermat. reads existing records only. It adds no tables and never writes.

| Ask concept | Source |
| --- | --- |
| Transaction, status, timestamps | `transaction_sets` |
| Documents in a transaction | `transaction_documents` → `documents` |
| Supplier, document number, totals, evidence snippets, confidence | `documents.extraction_data` |
| Matched lines (used for billed impact) | `transaction_sets.last_reconciliation.lines` |
| Exceptions, severity, review status, notes | `review_issues` where `active = true` (payload holds expected/actual and sources) |

### Derived at query time

- **Issue family**: issue `code` mapped to `price`, `quantity`, `supplier`, `currency`, `arithmetic`, `missing_item`, `unexpected_item`, `missing_line_items`.
- **Canonical supplier** for a transaction: the purchase order's supplier (the agreed counterparty), falling back to the invoice and then any document. Supplier filters match any document in the transaction after normalising company suffixes.
- **Variance**: parsed from the persisted `expected`/`actual` values. Delta, percentage and billed impact are calculated with `Decimal`.
- **Source**: each cited `EvidenceReference` gets a per-response ID (`S1`…), a label (`INV-4482 · p.1`), and the confidence looked up from the document's extraction evidence by field path. `preview_url` is reserved for a future page viewer and is always `null` in Phase 5.

### Conversation context

`AskConversationContext` is returned with every answer and sent back with the next question. It holds only the previous question, the previous intent, and up to 12 entities (`transaction` by ID, `supplier` by name). It is a hint, never a fact: IDs are re-resolved against the database on every request.
