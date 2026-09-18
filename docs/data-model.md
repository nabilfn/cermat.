# Initial data model

## documents
- id
- filename
- document_type
- status
- created_at

## extracted_documents
- document_id
- supplier_name
- document_number
- document_date
- currency
- subtotal
- tax
- total
- confidence
- raw_json

## line_items
- id
- document_id
- description
- sku
- quantity
- unit_price
- line_total

## evidence
- id
- document_id
- field_path
- page
- source_text
- confidence

## transaction_sets
Groups related PO / DO / Invoice / Receipt documents.

## discrepancies
- id
- transaction_set_id
- discrepancy_type
- severity
- expected_value
- actual_value
- explanation
- status
