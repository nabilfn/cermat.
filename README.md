# cermat.

**cermat.** is an AI operations agent for SMEs that turns business documents into structured records, reconciles them across workflows, and surfaces discrepancies with evidence.

## First vertical slice

This starter focuses on one workflow:

1. Upload an invoice, purchase order, delivery order, or receipt.
2. Create a document record.
3. Run extraction.
4. Normalize key fields.
5. Compare documents in a transaction set.
6. Flag mismatches for human review.

The extraction endpoint is deliberately a stub in this first build. The API contract and UI are ready so the actual multimodal/LLM extraction layer can be plugged in next without rebuilding the product around it.

## Stack

- Web: Next.js + React + TypeScript
- API: FastAPI + Pydantic
- Database: PostgreSQL
- Vector extension: pgvector-ready PostgreSQL image
- Local dev: Docker Compose

## Run

```bash
cp .env.example .env
docker compose up --build
```

Open:

- Web: http://localhost:3000
- API: http://localhost:8000
- Swagger: http://localhost:8000/docs

## What works now

- Upload UI
- Document-type selection
- API document creation
- Extraction contract
- Review result UI
- Health endpoint
- PostgreSQL service ready for persistence

## Next build

Replace the extraction stub with:

- PDF/image parsing
- multimodal model extraction
- structured JSON validation
- line-item extraction
- evidence spans/page references
- confidence scores
- reconciliation rules
- PostgreSQL persistence
