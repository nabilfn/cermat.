# Product definition — Phase 3

## One-line pitch

cermat. reads SME purchase documents and tells an operator exactly where the PO, delivery and invoice disagree, with the source evidence attached.

## Initial user

Finance, procurement or operations staff who manually compare purchase orders, delivery orders and supplier invoices.

## Core job-to-be-done

> Given the records for a purchase, show me whether what we ordered, what arrived and what we were billed for agree — without making me re-key every document.

## Phase 3 inputs

- Purchase Order
- Delivery Order
- Invoice

Receipt extraction remains supported but is not yet part of the three-way matching rules.

## Phase 3 exception classes

- currency mismatch
- supplier mismatch
- missing line item
- unexpected delivery line
- unexpected invoice line
- ordered/delivered quantity variance
- delivered/invoiced quantity variance
- PO/invoice unit-price variance
- invoice line arithmetic mismatch

## Product principles

- Preserve the original extracted value.
- Surface uncertainty instead of inventing certainty.
- Prefer identifiers over fuzzy matching.
- Keep AI perception separate from deterministic calculations.
- Attach evidence to exceptions whenever available.
- Make human review the final authority.
