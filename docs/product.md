# Product definition

## One-line pitch

cermat. is an AI operations agent that reads SME business documents, reconciles purchase records, and keeps an evidence-backed human review trail for every exception.

## Initial user

An SME operations or finance staff member who currently checks purchase orders, delivery orders, invoices, and receipts manually.

## Core job-to-be-done

> Given the documents for a purchase, tell me whether what was ordered, delivered, invoiced, and paid matches; show me the evidence for any mismatch; and keep track of what my team has already reviewed.

## Current workflow

1. Document intake
2. AI extraction
3. Structured validation
4. Deterministic three-way reconciliation
5. Persistent exception queue
6. Human resolution/reopen
7. Searchable transaction history
8. Ask cermat.: evidence-grounded questions over all of the above
9. Intelligence & Operations: overview, priority queue, supplier intelligence, patterns, anomalies, attention queue

## Workflow states

- Open
- Needs review
- Resolved
- Matched

## Product principles

- Never silently mutate extracted data.
- Preserve source evidence for important fields.
- Separate model extraction from deterministic business rules.
- Preserve human decisions across reconciliation reruns.
- A materially changed discrepancy must become a new review item.
- Keep history operational and compact rather than turning it into a decorative analytics dashboard.
- Retrieve facts deterministically; let AI explain them clearly.

## Ask cermat.

A business-data assistant, not a general chatbot. It answers operational questions from persisted records only: what needs attention, why a transaction is flagged, which suppliers have open discrepancies, what was resolved and when.

- **Grounded**: every figure comes from records or from Python calculations on them. Model wording that introduces an unknown number is discarded.
- **Traceable**: factual lines carry source chips that open the document, page, field, extracted value, snippet and confidence.
- **Scoped**: questions run across the workspace, or against one transaction when opened from Review history.
- **Read-only**: it cannot resolve, edit or delete. Those stay explicit, human UI actions.
- **Honest about gaps**: unsupported questions, unknown transactions or suppliers, and empty results are stated plainly rather than filled in.
- **Report, not chat**: answers are a headline plus short supporting lines, then the exceptions and transactions behind them.


## Intelligence & Operations

cermat. moves from "here is a discrepancy" to "here is what is happening across your operations, why it matters, and where to look first".

> **Rules calculate. Data proves. AI explains. Humans decide.**

- **Overview first.** The landing page answers: what needs attention, what changed, which suppliers repeat exceptions, and whether things are improving or worsening.
- **Deterministic facts.** Counts, rankings, variance totals, rates, trends and thresholds are calculated in code. AI only words the brief, and that wording is checked against the numbers.
- **Explainable prioritisation.** Every priority score lists the points behind it. "Critical" requires a stated rule, not a feeling.
- **Patterns are earned.** One-off exceptions are never called a pattern. A pattern needs repeats across transactions within a window.
- **Anomalies show their working.** Observed value, baseline, threshold and reason — never "AI detected suspicious activity".
- **Neutral language.** No accusations or speculation about motives. No words like fraud or suspicious.
- **Honest emptiness.** New workspaces get onboarding. Thin history says "not enough history" instead of drawing a trend.
- **Workflow health, not people tracking.** Resolution metrics describe the review queue, not individuals.
- **Quiet notifications.** An in-app `ATTENTION n` queue. Each condition notifies once.
- **Not a BI dashboard.** Rows, thin rules and a small number of restrained charts. No gauges, donuts or KPI card walls.


## Production readiness (Phase 7)

cermat. is a multi-user product rather than a local demo:

- **Accounts and workspaces**: sign up, sign in, owner/member roles, and strict workspace isolation.
- **Accountability**: every upload, extraction, reconciliation, resolution, settings change and deletion is recorded with who and when, and shown as compact activity on each transaction.
- **Evidence first**: every exception shows the source document, type, page, field, snippet and confidence, and links to the original file.
- **Honest states**: operational loading language ("Extracting fields…"), recoverable failures with retry, clear empty states with one next action, and demo data that is always labelled.
- **Practical output**: CSV export of review issues, respecting workspace permissions.
- **Deployable**: migrations, production images, environment validation, health and readiness checks, and structured logs.
