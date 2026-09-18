"""Build typed result rows, source references, and the grounded LLM context."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from app.schemas import (
    AskIssueRow,
    AskPlan,
    AskSource,
    AskSupplierRow,
    AskTransactionRow,
    DocumentType,
    EvidenceReference,
    TransactionContext,
    TransactionDocumentSummary,
)
from app.services.ask_queries import (
    FAMILY_LABELS,
    IssueSnap,
    QueryResult,
    TransactionSnap,
    calculate_variance,
)

NO_SNIPPET = "No source snippet was captured for this structured value."
MAX_SNIPPET = 220
MAX_NOTE = 300
MAX_TEXT = 300
HEADER_FIELDS = ("total", "supplier_name", "document_number")


def _clip(value: str | None, limit: int) -> str | None:
    if value is None:
        return None
    value = " ".join(value.split())
    return value if len(value) <= limit else value[: limit - 1] + "…"


class SourceRegistry:
    """Assigns short stable ids (S1, S2…) to evidence so answers can cite them."""

    def __init__(self) -> None:
        self._by_key: dict[tuple[UUID, str], AskSource] = {}

    @property
    def sources(self) -> list[AskSource]:
        return list(self._by_key.values())

    def ids(self) -> set[str]:
        return {source.id for source in self._by_key.values()}

    def add(self, txn: TransactionSnap, reference: EvidenceReference) -> str:
        key = (reference.document_id, reference.field_path)
        existing = self._by_key.get(key)
        if existing:
            return existing.id

        document = txn.document(reference.document_id)
        confidence = None
        if document is not None:
            match = next(
                (
                    item
                    for item in document.evidence
                    if isinstance(item, dict)
                    and item.get("field_path") == reference.field_path
                ),
                None,
            )
            if match is not None and isinstance(match.get("confidence"), (int, float)):
                confidence = float(match["confidence"])

        document_number = document.document_number if document else None
        label = document_number or reference.filename
        if reference.page is not None:
            label = f"{label} · p.{reference.page}"

        source = AskSource(
            id=f"S{len(self._by_key) + 1}",
            label=label,
            document_id=reference.document_id,
            filename=reference.filename,
            document_type=reference.document_type,
            document_number=document_number,
            transaction_id=txn.id,
            transaction_name=txn.name,
            page=reference.page,
            field_path=reference.field_path,
            value=reference.value,
            source_text=_clip(reference.source_text, MAX_SNIPPET) or "",
            confidence=confidence,
            snippet_available=reference.source_text != NO_SNIPPET,
        )
        self._by_key[key] = source
        return source.id

    def add_document_headers(self, txn: TransactionSnap) -> list[str]:
        """Cite header evidence (e.g. printed totals) for a focused transaction."""
        ids: list[str] = []
        for document in txn.documents:
            for field_name in HEADER_FIELDS:
                evidence = next(
                    (
                        item
                        for item in document.evidence
                        if isinstance(item, dict) and item.get("field_path") == field_name
                    ),
                    None,
                )
                if evidence is None or not evidence.get("source_text"):
                    continue
                value = {
                    "total": document.total,
                    "supplier_name": document.supplier_name,
                    "document_number": document.document_number,
                }[field_name]
                ids.append(
                    self.add(
                        txn,
                        EvidenceReference(
                            document_id=document.id,
                            filename=document.filename,
                            document_type=DocumentType(document.document_type),
                            field_path=field_name,
                            source_text=str(evidence["source_text"]),
                            page=evidence.get("page"),
                            value=None if value is None else str(value),
                        ),
                    )
                )
                break
        return ids


def issue_row(
    txn: TransactionSnap, issue: IssueSnap, registry: SourceRegistry
) -> AskIssueRow:
    return AskIssueRow(
        issue_id=issue.id,
        transaction_id=txn.id,
        transaction_name=txn.name,
        supplier=txn.supplier,
        code=issue.code,
        family=issue.family,
        title=issue.title,
        severity=issue.severity,
        status=issue.status,
        item_description=issue.item_description,
        expected=issue.expected,
        actual=issue.actual,
        delta=issue.delta,
        variance=calculate_variance(txn, issue),
        explanation=issue.explanation,
        resolution_note=issue.resolution_note,
        resolved_at=issue.resolved_at,
        source_ids=[registry.add(txn, source) for source in issue.sources],
    )


def transaction_row(txn: TransactionSnap) -> AskTransactionRow:
    return AskTransactionRow(
        transaction_id=txn.id,
        name=txn.name,
        supplier=txn.supplier,
        status=txn.status,
        currency=txn.currency,
        total=txn.total,
        document_types=[DocumentType(value) for value in txn.document_types],
        missing_document_types=[
            DocumentType(value) for value in txn.missing_document_types
        ],
        open_issue_count=len(txn.open_issues),
        resolved_issue_count=len(txn.resolved_issues),
        highest_open_severity=txn.highest_open_severity,
        updated_at=txn.updated_at,
    )


def document_summary(txn: TransactionSnap) -> list[TransactionDocumentSummary]:
    return [
        TransactionDocumentSummary(
            id=doc.id,
            filename=doc.filename,
            document_type=DocumentType(doc.document_type),
            status=doc.status,
            document_number=doc.document_number,
            supplier_name=doc.supplier_name,
            currency=doc.currency,
            total=doc.total,
            overall_confidence=doc.overall_confidence,
        )
        for doc in txn.documents
    ]


class BuiltResult:
    def __init__(
        self,
        issues: list[AskIssueRow],
        transactions: list[AskTransactionRow],
        suppliers: list[AskSupplierRow],
        registry: SourceRegistry,
        focus_source_ids: list[str],
    ) -> None:
        self.issues = issues
        self.transactions = transactions
        self.suppliers = suppliers
        self.registry = registry
        self.focus_source_ids = focus_source_ids

    def shown(self, result_kind: str) -> int:
        return {
            "issues": len(self.issues),
            "suppliers": len(self.suppliers),
            "transactions": len(self.transactions),
        }.get(result_kind, 0)


def build_rows(result: QueryResult) -> BuiltResult:
    registry = SourceRegistry()
    issues = [issue_row(txn, issue, registry) for txn, issue in result.issues]
    focus_source_ids: list[str] = []
    transactions = [transaction_row(txn) for txn in result.transactions]
    if result.focus is not None:
        focus_source_ids = registry.add_document_headers(result.focus)
        transactions = [transaction_row(result.focus)]
    elif result.result_kind == "issues":
        seen: dict[UUID, TransactionSnap] = {}
        for txn, _ in result.issues:
            seen.setdefault(txn.id, txn)
        transactions = [transaction_row(txn) for txn in seen.values()]
    return BuiltResult(issues, transactions, result.suppliers, registry, focus_source_ids)


def transaction_context(txn: TransactionSnap) -> TransactionContext:
    registry = SourceRegistry()
    issues = [issue_row(txn, issue, registry) for issue in txn.issues]
    registry.add_document_headers(txn)
    return TransactionContext(
        transaction=transaction_row(txn),
        documents=document_summary(txn),
        issues=issues,
        sources=registry.sources,
        reconciled_at=txn.reconciled_at,
    )


# ---------------------------------------------------------------------------
# Grounded LLM context
# ---------------------------------------------------------------------------


def _variance_payload(row: AskIssueRow) -> dict[str, Any] | None:
    variance = row.variance
    if variance is None:
        return None
    payload: dict[str, Any] = {
        "kind": variance.kind,
        "expected": variance.expected,
        "actual": variance.actual,
        "difference": variance.delta,
        "difference_percent": variance.percentage,
    }
    if variance.currency:
        payload["currency"] = variance.currency
    if variance.billed_impact is not None:
        payload["billed_impact_on_invoice_quantity"] = variance.billed_impact
    return payload


def grounded_context(
    *,
    question: str,
    plan: AskPlan,
    scope_name: str | None,
    result: QueryResult,
    built: BuiltResult,
) -> dict[str, Any]:
    """The only business data the answer model ever sees."""
    metrics = result.metrics
    context: dict[str, Any] = {
        "question": question,
        "interpreted_as": plan.intent.value,
        "scope": f"transaction {scope_name}" if scope_name else "whole workspace",
        "filters": {
            key: value
            for key, value in plan.filters.model_dump(mode="json").items()
            if value is not None and key != "transaction_id"
        },
        "totals_calculated_by_cermat": {
            "matching_records": result.total_matches,
            "records_listed_below": built.shown(result.result_kind),
            "transactions": metrics.transaction_count,
            "issues": metrics.issue_count,
            "open_issues": metrics.open_issue_count,
            "resolved_issues": metrics.resolved_issue_count,
            "issues_by_severity": metrics.by_severity,
            "issues_by_type": {
                FAMILY_LABELS.get(key, key): value for key, value in metrics.by_family.items()
            },
            "transactions_by_status": metrics.by_status,
        },
    }

    if result.focus is not None:
        focus = result.focus
        context["transaction"] = {
            "name": focus.name,
            "status": focus.status,
            "supplier": focus.supplier,
            "documents": [
                {
                    "type": doc.document_type,
                    "number": doc.document_number,
                    "filename": doc.filename,
                    "supplier": doc.supplier_name,
                    "currency": doc.currency,
                    "printed_total": doc.total,
                    "extraction_confidence": doc.overall_confidence,
                }
                for doc in focus.documents
            ],
            "missing_document_types": focus.missing_document_types,
            "reconciled": focus.reconciled_at is not None,
            "document_source_ids": built.focus_source_ids,
        }

    if built.issues:
        context["issues"] = [
            {
                "transaction": row.transaction_name,
                "supplier": row.supplier,
                "type": FAMILY_LABELS.get(row.family, row.family),
                "title": row.title,
                "severity": row.severity,
                "review_status": row.status,
                "item": row.item_description,
                "expected": row.expected,
                "actual": row.actual,
                "calculated_variance": _variance_payload(row),
                "rule_explanation": _clip(row.explanation, MAX_TEXT),
                "resolution_note": _clip(row.resolution_note, MAX_NOTE),
                "resolved_at": row.resolved_at.date().isoformat() if row.resolved_at else None,
                "source_ids": row.source_ids,
            }
            for row in built.issues
        ]

    if result.result_kind == "transactions" and built.transactions:
        context["transactions"] = [
            {
                "name": row.name,
                "supplier": row.supplier,
                "status": row.status,
                "printed_total": row.total,
                "currency": row.currency,
                "missing_document_types": [t.value for t in row.missing_document_types],
                "open_issues": row.open_issue_count,
                "resolved_issues": row.resolved_issue_count,
                "highest_open_severity": row.highest_open_severity,
                "updated": row.updated_at.date().isoformat(),
            }
            for row in built.transactions
        ]

    if built.suppliers:
        context["suppliers"] = [
            {
                "supplier": row.supplier,
                "transactions": row.transaction_count,
                "open_issues": row.open_issue_count,
                "resolved_issues": row.resolved_issue_count,
                "high_severity_open": row.high_open_count,
                "open_by_type": {
                    FAMILY_LABELS.get(key, key): value
                    for key, value in row.open_by_family.items()
                },
            }
            for row in built.suppliers
        ]

    if built.registry.sources:
        context["sources"] = [
            {
                "id": source.id,
                "document": source.document_number or source.filename,
                "document_type": source.document_type.value,
                "page": source.page,
                "field": source.field_path,
                "extracted_value": source.value,
                "untrusted_document_snippet": source.source_text
                if source.snippet_available
                else None,
            }
            for source in built.registry.sources
        ]

    return context
