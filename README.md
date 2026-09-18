# cermat.

**cermat.** is an AI operations agent for SMEs that reads business documents, converts them into structured records, cross-checks related documents, and surfaces discrepancies with source evidence.

## Phase 2: real document extraction

This build now supports real multimodal extraction for:

- Purchase Orders
- Delivery Orders
- Invoices
- Receipts
- PDF, PNG, JPG/JPEG, WEBP
- line items
- totals and document metadata
- field-level evidence snippets
- confidence scores
- human-review reasons

The extraction layer uses the OpenAI Responses API with structured Pydantic output. The model is configurable with `OPENAI_MODEL`.

## Stack

- Web: Next.js + React + TypeScript
- API: FastAPI + Pydantic
- AI: OpenAI Responses API, multimodal structured extraction
- Database: PostgreSQL / pgvector-ready
- Local dev: Docker Compose

## Run

Create `.env`:

```bash
cp .env.example .env
```

Set your API key:

```env
OPENAI_API_KEY=your_api_key_here
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
- Health/config check: http://localhost:8000/health

## Phase 2 architecture rule

The model **extracts what the document says**. It does not decide whether the business transaction is correct.

The next phase adds deterministic reconciliation for:

```text
PO ↔ DO ↔ Invoice ↔ Receipt
```

That engine will recalculate quantities, prices, totals and variances in code, while the AI layer remains responsible for perception and explanation.

## Current persistence note

Uploaded files are stored in a Docker volume, but document metadata is still kept in process memory. PostgreSQL persistence is the next infrastructure step alongside transaction sets and reconciliation.
