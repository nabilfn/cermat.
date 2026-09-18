# Phase 3 data model

## documents

Stores source-document metadata and the latest extraction.

- id
- filename
- document_type
- mime_type
- size_bytes
- status
- storage_path
- extraction_model
- extraction_data (JSON)
- created_at
- updated_at

## transaction_sets

Represents one operational purchase review.

- id
- name
- status
- last_reconciliation (JSON)
- created_at
- updated_at

## transaction_documents

Links source documents into a transaction set.

- id
- transaction_id
- document_id
- document_type
- created_at

Current Phase 3 rule: a transaction set can contain at most one document of each type.

## extraction_data JSON

Contains:

- supplier name / registration number
- document number / date
- currency
- subtotal / tax / total
- line items
- field-level source evidence
- confidence
- review reasons

## last_reconciliation JSON

Contains:

- status
- document snapshots
- summary counts
- matched lines
- deterministic issues
- source evidence references
- generation timestamp

A later normalized analytics layer can split line items, evidence and issues into dedicated tables when query/reporting requirements justify it.
