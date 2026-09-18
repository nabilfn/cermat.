# Data model — Phase 4

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
