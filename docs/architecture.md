# Architecture — Phase 4

```text
Browser
  |
  v
Next.js workspace
  |-- Single document
  |-- Three-way match
  `-- Review history
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
