# Architecture

```text
Browser
  |
  v
Next.js web
  |
  v
FastAPI
  | \
  |  \--> Extraction service (next)
  |        - PDF/image parser
  |        - multimodal LLM
  |        - structured validator
  |
  +----> PostgreSQL / pgvector
  |
  +----> Reconciliation engine
           - PO vs DO
           - DO vs Invoice
           - Invoice vs Receipt
           - deterministic variance rules
```

## Important boundary

The model extracts and explains.

The reconciliation engine decides numerical mismatches using deterministic code wherever possible.

That separation makes cermat. easier to test, audit, and demonstrate in a portfolio.
