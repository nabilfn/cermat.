# cermat.

**cermat.** is an AI operations agent for SMEs that reads purchase documents, converts them into structured records, and deterministically reconciles what was ordered, delivered, and invoiced.

## Phase 3: transaction reconciliation

This build adds the first core operations workflow:

```text
Purchase Order  ↔  Delivery Order  ↔  Invoice
       AI extraction      ↓
                 deterministic matching
                          ↓
              evidence-backed exceptions
```

### What works

- real PDF/image extraction through the OpenAI Responses API
- structured supplier, document, total and line-item data
- field-level source evidence and extraction confidence
- PostgreSQL persistence for document metadata, extraction payloads and transaction sets
- persisted uploaded files in a Docker volume
- transaction sets linking one PO, DO and invoice
- SKU-first line-item matching
- description-similarity fallback when SKU is unavailable
- ordered-vs-delivered quantity checks
- delivered-vs-invoiced quantity checks
- PO-vs-invoice unit-price checks
- invoice line arithmetic checks
- supplier and currency consistency checks
- evidence references attached to reconciliation exceptions
- persisted latest reconciliation result

## Architecture rule

The AI layer is responsible for **perception**:

> What does this document actually say?

The reconciliation layer is responsible for **business comparisons**:

> Do the extracted values agree?

The reconciliation engine does not ask the model to judge arithmetic or variances. It uses deterministic Python code and preserves the extracted source values.

## Stack

- Web: Next.js + React + TypeScript
- API: FastAPI + Pydantic
- AI: OpenAI Responses API with structured multimodal extraction
- Database: PostgreSQL (pgvector-ready image)
- ORM: SQLAlchemy async + asyncpg
- Local runtime: Docker Compose

## Run

Create `.env` if you have not already:

```bash
cp .env.example .env
```

Set your OpenAI key:

```env
OPENAI_API_KEY=your_api_key_here
OPENAI_MODEL=gpt-5.6-luna
```

Then rebuild:

```bash
docker compose down
docker compose up --build
```

Open:

- Web: http://localhost:3000
- API: http://localhost:8000
- Swagger: http://localhost:8000/docs
- Health: http://localhost:8000/health

The API creates its development tables automatically on startup. A later production-hardening phase should replace `create_all` with Alembic migrations.

## Three-way match workflow

In the web app choose **Three-way match** and add:

1. Purchase Order
2. Delivery Order
3. Invoice

cermat. will:

1. store each file
2. extract each document independently
3. persist the structured result
4. create a transaction set
5. link the documents
6. match line items
7. run deterministic variance checks
8. return issues with source evidence

### Example

```text
PO:       Office Chair × 10 @ RM42
DO:       Office Chair × 8
Invoice:  Office Chair × 10 @ RM44
```

The engine flags:

- delivered quantity differs from ordered quantity
- invoiced quantity differs from delivered quantity
- invoice unit price differs from the PO

No model is asked to decide whether `42 != 44`.

## Current limitations

Phase 3 intentionally keeps the rule engine conservative:

- one PO, one DO and one invoice per transaction set
- item matching is SKU-first with description similarity as fallback
- no configurable business tolerance policy yet
- no split deliveries or multiple invoices yet
- no credit notes
- no receipt/payment reconciliation yet
- no user accounts / tenant isolation yet
- no Alembic migrations yet

Those are good next steps after the core matching workflow is stable.
