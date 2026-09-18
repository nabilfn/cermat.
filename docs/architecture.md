# Architecture — Phase 5

```text
Browser
  |
  v
Next.js workspace
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
