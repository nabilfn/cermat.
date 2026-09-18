# cermat.

**cermat.** is an AI operations agent for SMEs that reads business documents, turns them into structured records, reconciles related documents with deterministic rules, and routes discrepancies through a human review workflow.

## Phase 5 — Ask cermat.

Ask cermat. is a read-only, evidence-grounded question layer over the records cermat. already stores. Ask things like:

- What needs my attention?
- Why is PO-2026-001 flagged?
- Which suppliers have unresolved price discrepancies?
- Show transactions where invoiced quantity exceeded delivered quantity.

Every answer is built from persisted transactions, review issues and extraction evidence, and every factual line cites its source as a chip (`INV-4482 · p.1`) that opens the document, page, field, extracted value, snippet and confidence.

```text
                  Ask cermat.
                       │
                       ▼
             Structured intent          (model structured output, or keyword fallback)
                       │
                       ▼
             Safe query handlers        (fixed Python handlers; no model-written SQL)
                       │
                       ▼
                 PostgreSQL             (three fixed read-only SELECTs)
                       │
                       ▼
              Grounded context          (variances and counts calculated in Python)
                       │
                       ▼
                LLM synthesis           (numbers checked against the records)
                       │
                       ▼
            Answer + source evidence
```

> Retrieve facts deterministically. Let AI explain them clearly.

Open **Ask cermat.** from the mode bar, or press **Ask about this →** on a transaction in Review history to scope questions to it ("Why is this flagged?").

### Try it with demo data

```bash
docker compose exec api python -m scripts.seed_demo
```

This inserts five pre-extracted demo transactions (PO-2026-001, -091, -103, -114, -120) and runs the real reconciliation engine over them. It needs no model calls and skips transactions that already exist.

### Without a model key

If `OPENAI_API_KEY` is missing or invalid, Ask cermat. still works. A keyword planner interprets the question, and the answer is composed directly from the records. The answer says so in a notice.

## Phase 4

Phase 4 adds the persistent operating layer around the Phase 3 reconciliation engine:

1. Upload a PO, delivery order, and invoice.
2. Extract each source with the multimodal AI layer.
3. Run deterministic three-way matching.
4. Persist every reconciliation exception as a review issue.
5. Search transaction history.
6. Open a historical transaction and inspect its evidence.
7. Resolve or reopen individual issues with an optional resolution note.
8. Move the transaction from `Needs review` to `Resolved` when all active issues are closed.

A stable issue identity prevents rerunning the same reconciliation from creating duplicate review items. If the business exception materially changes, cermat. opens a new issue instead of silently reusing the old decision.

## Stack

- Web: Next.js + React + TypeScript
- API: FastAPI + Pydantic
- Database: PostgreSQL
- Vector extension: pgvector-ready PostgreSQL image
- AI extraction: OpenAI Responses API with structured extraction
- Reconciliation: deterministic Python rules
- Local development: Docker Compose

## Run

```bash
cp .env.example .env
```

Add your API key to `.env`:

```env
OPENAI_API_KEY=your_key_here
OPENAI_MODEL=gpt-5.6-luna
```

Then:

```bash
docker compose up --build
```

Open:

- Web: http://localhost:3000
- API: http://localhost:8000
- Swagger: http://localhost:8000/docs

## Workspace modes

### Single document

Extract one PO, DO, invoice, or receipt and inspect structured fields, line items, confidence, and source evidence.

### Three-way match

Upload one PO + DO + Invoice. cermat. compares ordered, delivered, and invoiced values using deterministic rules.

### Review history

Browse persisted transactions, search by transaction/supplier, filter by workflow state, inspect historical evidence, and resolve/reopen exceptions.

### Ask cermat.

Ask operational questions across the workspace or about one transaction. Answers read like a short analyst brief (headline, supporting lines, exceptions, related transactions) with source chips and an evidence panel. Ask cermat. is read-only: resolving, editing and deleting stay explicit UI actions.

## Workflow states

- **Open** — transaction is still collecting documents or waiting for reconciliation.
- **Needs review** — reconciliation found one or more active unresolved exceptions.
- **Resolved** — all active exceptions have been reviewed and closed by a human.
- **Matched** — deterministic reconciliation found no exceptions.

## Review issue persistence

Each reconciliation exception gets a stable hash based on the business meaning of the discrepancy — code, item, expected/actual values, and source field locations. This means:

- rerunning an unchanged transaction preserves its existing review status and note;
- a changed discrepancy receives a new review issue;
- exceptions that disappear on a later reconciliation become inactive rather than polluting the active queue.

## Tests

```bash
docker compose exec api python -m unittest discover -s tests -v
```

The test suite covers clean matching, quantity/price mismatches, stable review-issue identity, and Ask cermat.: every query intent, transaction and supplier scoping, unsupported and no-result questions, follow-ups, grounding checks on model output, and the read-only guarantees. Ask tests use an in-memory snapshot and a fake model, so they need no database or API key.

## Important architecture boundary

The AI layer answers:

> What does this document say?

The reconciliation layer answers:

> Do these values agree?

The review layer answers:

> Has a human accepted or resolved this exception?

Ask cermat. answers:

> What do my records say, and where is the evidence?

Keeping those responsibilities separate makes cermat. easier to test, audit, and explain.
