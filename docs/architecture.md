# Architecture — Phase 6

```text
Browser
  |
  v
Next.js workspace            (+ ATTENTION indicator in the shell)
  |-- Overview  ── supplier view
  |-- Single document
  |-- Three-way match
  |-- Review history
  `-- Ask cermat.
          |
          v
FastAPI
  |\
  | \--> Extraction service
  |       - PDF/image input
  |       - multimodal model
  |       - typed structured output
  |
  +----> Reconciliation engine
  |       - SKU-first matching
  |       - description fallback
  |       - quantity/price/arithmetic rules
  |
  +----> Review workflow
  |       - stable issue identity
  |       - open/resolved state
  |       - resolution notes
  |       - rerun preservation
  |
  +----> Intelligence layer (app/intelligence/)
  |       - operational metrics, priority, patterns,
  |         anomalies, trends, suppliers, data quality
  |       - cermat. brief (grounded + checked)
  |       - attention events (stable keys)
  |
  +----> Ask cermat. (read-only)
  |       - intent planner (structured output)
  |       - fixed query handlers
  |       - grounded context + guarded synthesis
  |
  `----> PostgreSQL / pgvector
          - documents
          - transaction_sets
          - transaction_documents
          - review_issues
```

## Responsibility boundary

### AI extraction

Reads messy documents and returns structured observations plus evidence.

### Deterministic reconciliation

Compares numerical and categorical values. The model does not decide whether `RM42 != RM44`.

### Human review

Stores whether an exception has been accepted/resolved and why. Human review state is separate from the raw reconciliation result.

## Stable issue identity

A SHA-256 key is derived from the discrepancy's code, item, expected/actual values, and source field paths. Document UUIDs are deliberately excluded so the same logical issue remains recognisable across a rerun with re-created source records.

## Ask cermat.

```text
                  Ask cermat.
                       │
                       ▼
             Structured intent
                       │
                       ▼
             Safe query handlers
                       │
                       ▼
                 PostgreSQL
                       │
                       ▼
              Grounded context
                       │
                       ▼
                LLM synthesis
                       │
                       ▼
            Answer + source evidence
```

### Modules

| Module | Responsibility |
| --- | --- |
| `app/routers/ask.py` | `POST /api/v1/ask`, `POST /api/v1/ask/stream`, `GET /api/v1/ask/suggestions`, `GET /api/v1/transactions/{id}/context` |
| `app/services/ask.py` | Pipeline orchestration, deterministic answer composer, grounding guard, follow-ups |
| `app/services/ask_planner.py` | Keyword fallback planner; resolves every entity to a database ID or canonical name |
| `app/services/ask_queries.py` | Read-only snapshot loader, one fixed handler per intent, Python variance/count calculations |
| `app/services/ask_context.py` | Typed result rows, source registry (`S1`, `S2`…), compact grounded context for the model |
| `app/services/ask_llm.py` | The only model calls: `plan` and `compose`, both returning validated Pydantic objects |
| `app/prompts/ask_cermat.py` | Planner and answer system prompts (never returned by the API) |

### Pipeline

1. **Snapshot.** `load_snapshot` runs three fixed SELECTs (transactions, linked documents, active review issues). When a `transaction_id` is supplied, every query is filtered to that transaction, so out-of-scope records never enter memory. Workspaces are SME-sized, so the handlers filter in Python. If volumes grow, the snapshot loader is the single place to push filters into SQL.
2. **Plan.** The model returns a `PlannerOutput` (intent enum plus filter values) through strict structured output. If no key is configured or the call fails, `heuristic_plan` does the same job with keyword rules. The planner cannot express SQL, columns or expressions.
3. **Resolve.** `resolve_plan` maps a transaction reference (a transaction name or any attached document number) to a transaction ID, and a supplier to its stored canonical name. Unknown references return a `not_found` answer instead of a guess. Follow-up entities from the previous turn are re-checked against the snapshot by ID; a fabricated ID is ignored. In transaction scope the scope always wins.
4. **Query.** `INTENT_HANDLERS[intent]` runs. Counts, severities, per-supplier aggregates, variance amounts, variance percentages and billed impact (price delta × invoiced quantity) are all calculated here in Python with `Decimal`.
5. **Context.** `grounded_context` builds the only business data the model sees. Document snippets, supplier names, item descriptions and resolution notes are clipped and labelled as untrusted.
6. **Synthesis.** The model returns `{headline, points[{text, source_ids}]}`. `accept_model_answer` rejects the whole answer if it contains any number that does not appear in the context, and drops citations to unknown source IDs. On rejection or failure, the deterministic composer's answer is used and a notice explains why.
7. **Response.** `AskResponse` returns the answer, typed issue/transaction/supplier rows, sources, notices, follow-up suggestions and a minimal conversation context for the next turn.

Unsupported, not-found and no-result questions never reach the answer model, so it cannot fall back to general knowledge.

### Safety boundary

- Read-only: the Ask modules never add, commit, delete or run raw SQL, and the session is rolled back after every request. A test scans the Ask modules to enforce this.
- The model has no database handle, connection string or tool. It receives a question plus minimal conversation state, or a JSON context object.
- Errors return generic messages. Stack traces, prompts, SQL and secrets are logged server-side only.
- Requests to resolve, edit or delete anything are classified as unsupported. Those actions stay explicit UI actions.

### Streaming

`POST /api/v1/ask/stream` emits NDJSON: `{"stage":"interpreting"}`, `{"stage":"searching"}`, `{"stage":"composing"}` (only when the model writes the answer), then `{"stage":"done","response":…}` or `{"stage":"error","detail":…}`. The UI's loading text follows these real stages.

## Intelligence & Operations (Phase 6)

> cermat. computes operational facts deterministically and uses AI to explain them.

```text
Business documents ─► AI extraction ─► Structured records ─► Deterministic reconciliation
      ─► Review issues ─► Operational metrics ─┬─► Rule-based signals
                                               └─► Pattern analysis
                                                        │
                                                        ▼
                                              Intelligence layer ─► Overview
```

### Modules

| Module | Responsibility |
| --- | --- |
| `intelligence/dataset.py` | Projected SQL loader (`load_dataset`), snapshot adapter for Ask, supplier identity, time windows |
| `intelligence/variance.py` | Standardised billed variance; per-currency totals |
| `intelligence/overview.py` | Current-state metrics, period activity, resolution performance, issue mix, data quality |
| `intelligence/priority.py` | Priority score, bands and reasons |
| `intelligence/patterns.py` | Recurring-pattern rules |
| `intelligence/anomalies.py` | Anomaly rules |
| `intelligence/suppliers.py` | Supplier list and detail |
| `intelligence/trends.py` | Bucketed trend series and direction |
| `intelligence/brief.py` | Brief facts, records-only brief, guarded model rewording, cache |
| `intelligence/attention.py` | Event candidates, idempotent sync, listing |
| `intelligence/config.py` | Threshold settings (single row, validated) |
| `routers/intelligence.py`, `routers/attention.py`, `routers/intelligence_settings.py` | HTTP layer only |
| `services/ask_intelligence.py` | Phase 6 Ask intents mapped onto the same functions |

All calculations are pure functions of `(dataset, settings, now)`, testable without HTTP or a database.

### Endpoints

```text
GET   /api/v1/intelligence/overview?period=7d|30d|90d|all
GET   /api/v1/intelligence/priority
GET   /api/v1/intelligence/trends?period=…&category=all|price|quantity|supplier|currency|missing_documents
GET   /api/v1/intelligence/suppliers?period=…
GET   /api/v1/intelligence/suppliers/{supplier_key}
GET   /api/v1/intelligence/patterns
GET   /api/v1/intelligence/anomalies
GET   /api/v1/intelligence/brief?period=…
GET   /api/v1/attention            (syncs events, then lists active ones)
PATCH /api/v1/attention/{id}       {seen?, dismissed?}
GET   /api/v1/settings/intelligence
PATCH /api/v1/settings/intelligence  (partial update, or {"reset": true})
```

### Data access and performance

`load_dataset` runs three SELECTs that project only the needed fields: `extraction_data->>'supplier_name'`, `->>'total'`, `json_array_length(evidence)`, `last_reconciliation->'lines'`, and `payload->>'expected'`/`'actual'`. Full extraction blobs never reach Python. Only **active** review issues are loaded. Metrics are derived on each request, not persisted. Indexes cover review-issue `created_at`, `resolved_at`, `(active, status)`, severity and code, transaction `created_at`/`updated_at`, and the extracted supplier name. They are created idempotently at startup because `create_all` does not add indexes to existing tables. At SME volumes no cache layer is needed. The brief caches model output per unique fact set.

### Time windows

`7d`, `30d` and `90d` are `[now − N days, now]`, both ends inclusive. The previous window is `[now − 2N, now − N)`. `all` has no start and no comparison. Bounds are calculated in code; the model never interprets dates. Trend buckets are UTC calendar days (7d/30d), Monday-start weeks (90d), or weeks/months for all time.

### Financial variance

| Exception | Signed variance |
| --- | --- |
| Invoice unit price ≠ PO | (invoice price − PO price) × invoiced quantity |
| Invoiced qty ≠ delivered qty | (invoiced − delivered) × invoice unit price |
| Invoice line arithmetic | printed line total − quantity × unit price |

Each variance records `signed_variance`, `absolute_variance`, `variance_percentage` and `currency`. Supplier-name, missing-item and delivered-vs-ordered exceptions have no billed amount and return `null`, never an estimate. Totals are grouped per currency and never converted. Percentage thresholds apply only to billed exceptions.

### Priority scoring

```text
priority_score = severity            high 40 · medium 20 · low 8
               + high-value variance +20 if |billed variance| ≥ high_value_variance_amount
               + high variance %     +15 if |variance %| ≥ high_variance_percentage
               + age                 1.5 per day open, capped at 15
               + overdue             +10 if open ≥ overdue_review_days
               + related issues      +4 per other open issue on the transaction, cap 12
               + recurring supplier  +12 if the supplier has a recurring pattern
               + issue type          currency 8 · arithmetic 6 · quantity/price 4 · …
```

Bands: **critical** = score ≥ 85 **and** high severity **and** (high-value variance or overdue). **High** = score ≥ 55. Otherwise **normal**. All weights and cut-offs are configurable. Each item returns its components as `priority_reasons`. This is a points system, not a machine-learning risk score.

### Recurring patterns

Default threshold: ≥ 3 occurrences within 90 days, across **at least two transactions**. Several exceptions on one transaction are one event, not a pattern.

| Pattern | Rule |
| --- | --- |
| `repeated_price_discrepancy` (and supplier, currency, arithmetic, missing/unexpected item variants) | same supplier + same issue family |
| `repeated_invoice_over_delivery` | same supplier, invoiced qty > delivered qty |
| `repeated_short_delivery` | same supplier, delivered qty < ordered qty |
| `repeated_item_price_mismatch` | same SKU (or item description) price-mismatched |
| `repeated_missing_document` | same required document missing from un-reconciled sets older than a day |
| `repeated_unresolved_supplier_issues` | same supplier with open issues across transactions |

### Anomaly rules

| Signal | Rule | Baseline |
| --- | --- | --- |
| `high_variance_percentage` | open billed exception with \|variance %\| ≥ threshold (10%) | supplier median \|variance %\| for that issue type |
| `high_value_variance` | \|billed variance\| ≥ threshold (500) | supplier median billed variance, same currency |
| `amount_above_supplier_median` | invoice total > 3 × supplier median of prior invoices | median of ≥ 4 prior invoices, same currency |
| `issue_frequency_spike` | last-30-day exceptions ≥ 2 × previous 30 days and ≥ 3 | previous 30 days; requires 60+ days of history |
| `new_supplier_high_severity` | supplier first seen < 30 days ago (≤ 2 transactions) with an open high-severity exception | only once the workspace predates the window |
| `long_unresolved_issue` | open ≥ 2 × review target | median resolution time |

Baselines need `min_history_for_baseline` (4) samples. Below that, the signal says there is not enough history instead of inventing a baseline.

### cermat. brief grounding

`brief_facts` selects computed numbers only. `records_brief` writes the brief from them in code (always available). A model may reword it. Its output is rejected, and the records brief used, if it contains any number not present in the facts or any word such as fraud, suspicious, risky or dishonest. The Overview labels which path produced the text.

### Attention events

Candidates are recomputed on `GET /attention` and synced with `INSERT … ON CONFLICT (event_key) DO NOTHING`, so repeated loads never duplicate events. When a condition stops holding the event is *cleared*. If it holds again, the same row is *restored*. Dismissed events stay dismissed. Anomalies about an issue that already has an issue-level event are not notified twice.

### Data quality vs reconciliation

Reconciliation issues are business discrepancies between documents (`review_issues`). Data-quality indicators are extraction completeness checks on a single document (low confidence, missing supplier, number, date, currency or evidence, plus unmatched lines). They are reported separately and never counted as exceptions.
