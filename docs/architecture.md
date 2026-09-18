# cermat. architecture — Phase 3

```text
Browser
  |
  v
Next.js workspace
  |
  v
FastAPI
  |
  +---------------------> PostgreSQL
  |                         - documents
  |                         - extraction JSON
  |                         - transaction sets
  |                         - reconciliation result
  |
  +--> Upload storage volume
  |
  +--> AI extraction service
  |      - PDF/image input
  |      - multimodal model
  |      - typed Pydantic output
  |      - evidence snippets
  |
  +--> Deterministic reconciliation engine
         - SKU matching
         - description fallback
         - quantity comparison
         - unit-price comparison
         - line arithmetic check
         - source evidence mapping
```

## Trust boundary

Uploaded files are untrusted input. Instructions printed inside a document do not become model instructions.

The extraction model may identify values, but it does not repair or reconcile them. Reconciliation operates on the extracted structured values and produces explicit exceptions without modifying the source data.

## Item matching order

1. exact normalized SKU match
2. unused line with the strongest normalized description match
3. description match must reach the configured similarity threshold
4. otherwise the line remains unmatched and is surfaced for review

This makes identifier-based matching dominant while still supporting invoices and delivery notes that omit SKUs.
