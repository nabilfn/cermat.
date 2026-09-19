# Data model

The schema is owned by Alembic (`apps/api/migrations`). `0001_baseline` is the Phase 1–6 schema and is written idempotently, so databases created before Alembic adopt it unchanged. `0002_workspaces_auth_audit` adds tenancy.

## Tenancy and identity (Phase 7)

| Table | Key columns | Notes |
|---|---|---|
| `users` | email (unique, lowercased), display_name, password_hash (Argon2id), last_login_at | |
| `workspaces` | name, is_demo, is_legacy, created_by → users (SET NULL) | `is_legacy`: holds data created before accounts existed |
| `workspace_members` | workspace_id → workspaces (CASCADE), user_id → users (CASCADE), role | unique (workspace_id, user_id); role ∈ {owner, member} (CHECK) |
| `auth_sessions` | token_hash (unique HMAC), csrf_hash, user_id (CASCADE), expires_at, last_seen_at | no plaintext tokens |
| `audit_events` | workspace_id (CASCADE), actor_user_id (SET NULL), action, entity_type, entity_id, metadata_json, request_id, created_at | append-only through the API; index (workspace_id, created_at) |

**Every business row belongs to a workspace.** `documents`, `transaction_sets`, `review_issues` and `attention_events` have a non-null `workspace_id → workspaces ON DELETE CASCADE`. `intelligence_settings` is keyed by `workspace_id`. Deleting a workspace removes all of its data. Stored files are deleted by the API after the rows are gone.

Phase 7 column changes:

- `documents.storage_path` (an absolute container path) became `storage_key` (a server-generated object key, null for demo records). New columns `page_count` and `error_code`, and `uploaded_by → users`. Statuses are `uploaded | processing | extracted | needs_review | failed` (the old `extracting` became `processing`).
- `transaction_sets.created_by`, `review_issues.resolved_by` (→ users, SET NULL).
- Attention `event_key` is unique **per workspace** (`uq_attention_workspace_event_key`).
- Composite indexes: `(workspace_id, updated_at)` on transactions, `(workspace_id, active, status)` on review issues, `(workspace_id, created_at)` on documents and audit events.

The sections below describe the business tables from earlier phases. All of them now carry `workspace_id`.

## documents

- id
- filename
- document_type
- MIME type / size
- status
- storage key (server-generated object key)
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

## intelligence_settings (Phase 6)

One row (`id = 1`) holding validated operational thresholds as JSON (`config`), plus `updated_at`. A missing or invalid row means defaults.

| Setting | Default |
| --- | --- |
| high_value_variance_amount | 500 (applied per currency, never converted) |
| high_variance_percentage | 10 |
| recurring_issue_min_count | 3 |
| recurring_issue_period_days | 90 |
| overdue_review_days | 7 |
| low_confidence_threshold | 0.75 |
| amount_median_multiplier | 3 |
| min_history_for_baseline | 4 |
| issue_spike_ratio | 2 |
| critical_score / high_score | 85 / 55 |
| priority_weights | see architecture.md |

## attention_events (Phase 6)

- id
- event_key (unique, stable — e.g. `high_severity_issue:<issue id>`, `recurring_pattern:<pattern key>`)
- event_type: `high_severity_issue` · `large_variance` · `overdue_review` · `recurring_pattern` · `anomaly`
- title, message, severity
- entity_type (`transaction` / `supplier` / `workspace`), entity_id, entity_label
- transaction_id (nullable, cascades on delete)
- created_at, seen_at, dismissed_at
- cleared_at — set when the condition no longer holds; cleared again → restored, not duplicated

## Derived, not stored

Every Overview metric, priority score, pattern, anomaly, trend point and supplier statistic is computed on request from `transaction_sets`, `documents` and **active** `review_issues`. Nothing is materialised.

- **Supplier identity**: suppliers exist only as extracted names. The key is the name normalised for case, punctuation and company suffixes (`Sdn. Bhd.`, `Ltd` …), e.g. `abc-supplies`. There is no fuzzy merging: "Delta Office" and "Delta Offices Trading" stay separate. A transaction belongs to the supplier on its purchase order.
- **Issue rate**: reconciled transactions with ≥ 1 active exception ÷ reconciled transactions.
- **Resolution time**: `resolved_at − created_at`. Reopening clears `resolved_at`, so the trend's "open at end of period" reflects current review state rather than a full audit history.
