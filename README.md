# cermat.

**cermat.** is an AI operations agent for SMEs that reads business documents, turns them into structured records, reconciles related documents with deterministic rules, and routes discrepancies through a human review workflow.

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

The test suite covers clean matching, quantity/price mismatches, and stable review-issue identity.

## Important architecture boundary

The AI layer answers:

> What does this document say?

The reconciliation layer answers:

> Do these values agree?

The review layer answers:

> Has a human accepted or resolved this exception?

Keeping those responsibilities separate makes cermat. easier to test, audit, and explain.
