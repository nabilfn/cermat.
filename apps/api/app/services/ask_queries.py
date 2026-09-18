"""Deterministic, read-only query handlers for Ask cermat.

Nothing in this module accepts SQL, column names, or expressions from the
model. A validated ``AskPlan`` selects one handler from ``INTENT_HANDLERS``;
the handler filters an in-memory snapshot of persisted records using fixed
Python predicates. The only database access is ``load_snapshot``, which runs
three fixed SELECT statements.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    DocumentModel,
    ReviewIssueModel,
    TransactionDocumentModel,
    TransactionSetModel,
)
from app.schemas import (
    AskIntent,
    AskMetrics,
    AskPlan,
    AskSupplierRow,
    AskVariance,
    DocumentType,
    EvidenceReference,
    MatchedLine,
    ReconciliationResult,
)
from app.services.reconciliation import _normalise_supplier, _supplier_similarity

REQUIRED_DOCUMENT_TYPES = (
    DocumentType.purchase_order.value,
    DocumentType.delivery_order.value,
    DocumentType.invoice.value,
)
DOCUMENT_ORDER = {
    DocumentType.purchase_order.value: 0,
    DocumentType.delivery_order.value: 1,
    DocumentType.invoice.value: 2,
    DocumentType.receipt.value: 3,
}
SEVERITY_RANK = {"high": 0, "medium": 1, "low": 2}
STATUS_RANK = {"open": 0, "resolved": 1}
SUPPLIER_MATCH_THRESHOLD = 0.82

ISSUE_FAMILIES: dict[str, str] = {
    "invoice_price_variance": "price",
    "unit_price_mismatch": "price",
    "delivery_quantity_variance": "quantity",
    "invoice_delivery_quantity_variance": "quantity",
    "supplier_mismatch": "supplier",
    "currency_mismatch": "currency",
    "invoice_line_arithmetic_mismatch": "arithmetic",
    "item_missing_from_delivery": "missing_item",
    "item_missing_from_invoice": "missing_item",
    "unexpected_delivery_item": "unexpected_item",
    "unexpected_invoice_item": "unexpected_item",
    "missing_po_line_items": "missing_line_items",
    "missing_invoice_line_items": "missing_line_items",
}

FAMILY_LABELS: dict[str, str] = {
    "price": "price mismatch",
    "quantity": "quantity mismatch",
    "supplier": "supplier mismatch",
    "currency": "currency mismatch",
    "arithmetic": "line arithmetic error",
    "missing_item": "missing line item",
    "unexpected_item": "unexpected line item",
    "missing_line_items": "unreadable line items",
    "other": "other exception",
}


def family_of(code: str) -> str:
    return ISSUE_FAMILIES.get(code, "other")


# ---------------------------------------------------------------------------
# Snapshot of persisted records
# ---------------------------------------------------------------------------


@dataclass
class DocumentSnap:
    id: UUID
    filename: str
    document_type: str
    status: str
    document_number: str | None
    supplier_name: str | None
    currency: str | None
    total: float | None
    overall_confidence: float | None
    evidence: list[dict]


@dataclass
class IssueSnap:
    id: UUID
    transaction_id: UUID
    code: str
    title: str
    severity: str
    status: str
    item_description: str | None
    expected: str | None
    actual: str | None
    delta: str | None
    explanation: str
    sources: list[EvidenceReference]
    resolution_note: str | None
    resolved_at: datetime | None
    created_at: datetime
    updated_at: datetime

    @property
    def family(self) -> str:
        return family_of(self.code)


@dataclass
class TransactionSnap:
    id: UUID
    name: str
    status: str
    created_at: datetime
    updated_at: datetime
    documents: list[DocumentSnap] = field(default_factory=list)
    issues: list[IssueSnap] = field(default_factory=list)
    lines: list[MatchedLine] = field(default_factory=list)
    reconciled_at: datetime | None = None

    def _preferred_document(self) -> DocumentSnap | None:
        # The purchase order names the agreed counterparty, so it is canonical
        # for supplier identity; an invoice may carry a disputed supplier name.
        for document_type in (
            DocumentType.purchase_order.value,
            DocumentType.invoice.value,
        ):
            for document in self.documents:
                if document.document_type == document_type:
                    return document
        return self.documents[0] if self.documents else None

    @property
    def supplier(self) -> str | None:
        preferred = self._preferred_document()
        if preferred and preferred.supplier_name:
            return preferred.supplier_name
        return next(
            (doc.supplier_name for doc in self.documents if doc.supplier_name), None
        )

    @property
    def currency(self) -> str | None:
        preferred = self._preferred_document()
        return preferred.currency if preferred else None

    @property
    def total(self) -> float | None:
        preferred = self._preferred_document()
        return preferred.total if preferred else None

    @property
    def document_types(self) -> list[str]:
        return [doc.document_type for doc in self.documents]

    @property
    def missing_document_types(self) -> list[str]:
        present = set(self.document_types)
        return [value for value in REQUIRED_DOCUMENT_TYPES if value not in present]

    @property
    def open_issues(self) -> list[IssueSnap]:
        return [issue for issue in self.issues if issue.status == "open"]

    @property
    def resolved_issues(self) -> list[IssueSnap]:
        return [issue for issue in self.issues if issue.status == "resolved"]

    @property
    def highest_open_severity(self) -> str | None:
        ranked = sorted(
            (issue.severity for issue in self.open_issues),
            key=lambda value: SEVERITY_RANK.get(value, 9),
        )
        return ranked[0] if ranked else None

    @property
    def last_resolved_at(self) -> datetime | None:
        values = [issue.resolved_at for issue in self.issues if issue.resolved_at]
        return max(values) if values else None

    def document(self, document_id: UUID) -> DocumentSnap | None:
        return next((doc for doc in self.documents if doc.id == document_id), None)


@dataclass
class WorkspaceSnapshot:
    transactions: list[TransactionSnap]

    def transaction(self, transaction_id: UUID | None) -> TransactionSnap | None:
        if transaction_id is None:
            return None
        return next((t for t in self.transactions if t.id == transaction_id), None)

    def issue_pairs(self) -> list[tuple[TransactionSnap, IssueSnap]]:
        return [(txn, issue) for txn in self.transactions for issue in txn.issues]

    def suppliers(self) -> list[str]:
        seen: dict[str, str] = {}
        for txn in self.transactions:
            for doc in txn.documents:
                if doc.supplier_name:
                    seen.setdefault(_normalise_supplier(doc.supplier_name), doc.supplier_name)
        return sorted(seen.values())


def _parse_datetime(value: object) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    return None


def _document_snap(document: DocumentModel) -> DocumentSnap:
    extraction = document.extraction_data or {}
    evidence = extraction.get("evidence")
    return DocumentSnap(
        id=document.id,
        filename=document.filename,
        document_type=document.document_type,
        status=document.status,
        document_number=extraction.get("document_number"),
        supplier_name=extraction.get("supplier_name"),
        currency=extraction.get("currency"),
        total=extraction.get("total"),
        overall_confidence=extraction.get("overall_confidence"),
        evidence=evidence if isinstance(evidence, list) else [],
    )


def _issue_snap(issue: ReviewIssueModel) -> IssueSnap:
    payload = issue.payload or {}
    sources: list[EvidenceReference] = []
    for raw in payload.get("sources") or []:
        try:
            sources.append(EvidenceReference.model_validate(raw))
        except ValueError:
            continue
    return IssueSnap(
        id=issue.id,
        transaction_id=issue.transaction_id,
        code=issue.code,
        title=issue.title,
        severity=issue.severity,
        status=issue.status,
        item_description=payload.get("item_description"),
        expected=payload.get("expected"),
        actual=payload.get("actual"),
        delta=payload.get("delta"),
        explanation=payload.get("explanation") or "",
        sources=sources,
        resolution_note=issue.resolution_note,
        resolved_at=issue.resolved_at,
        created_at=issue.created_at,
        updated_at=issue.updated_at,
    )


def _transaction_snap(transaction: TransactionSetModel) -> TransactionSnap:
    lines: list[MatchedLine] = []
    reconciled_at: datetime | None = None
    if transaction.last_reconciliation:
        try:
            result = ReconciliationResult.model_validate(transaction.last_reconciliation)
            lines = result.lines
            reconciled_at = result.generated_at
        except ValueError:
            reconciled_at = _parse_datetime(
                transaction.last_reconciliation.get("generated_at")
            )
    return TransactionSnap(
        id=transaction.id,
        name=transaction.name,
        status=transaction.status,
        created_at=transaction.created_at,
        updated_at=transaction.updated_at,
        lines=lines,
        reconciled_at=reconciled_at,
    )


async def load_snapshot(
    session: AsyncSession, transaction_id: UUID | None = None
) -> WorkspaceSnapshot:
    """Read persisted records. Fixed SELECTs only — this function never writes."""
    query = select(TransactionSetModel).order_by(TransactionSetModel.updated_at.desc())
    if transaction_id is not None:
        query = query.where(TransactionSetModel.id == transaction_id)
    transactions = [
        _transaction_snap(row) for row in (await session.execute(query)).scalars().all()
    ]
    if not transactions:
        return WorkspaceSnapshot(transactions=[])

    by_id = {txn.id: txn for txn in transactions}
    ids = list(by_id)

    document_rows = await session.execute(
        select(TransactionDocumentModel.transaction_id, DocumentModel)
        .join(DocumentModel, TransactionDocumentModel.document_id == DocumentModel.id)
        .where(TransactionDocumentModel.transaction_id.in_(ids))
    )
    for txn_id, document in document_rows.all():
        by_id[txn_id].documents.append(_document_snap(document))
    for txn in transactions:
        txn.documents.sort(key=lambda doc: DOCUMENT_ORDER.get(doc.document_type, 99))

    issue_rows = await session.execute(
        select(ReviewIssueModel).where(
            ReviewIssueModel.transaction_id.in_(ids),
            ReviewIssueModel.active.is_(True),
        )
    )
    for issue in issue_rows.scalars().all():
        by_id[issue.transaction_id].issues.append(_issue_snap(issue))
    for txn in transactions:
        txn.issues.sort(key=issue_sort_key)

    return WorkspaceSnapshot(transactions=transactions)


# ---------------------------------------------------------------------------
# Deterministic calculations
# ---------------------------------------------------------------------------

_MONEY_RE = re.compile(r"^\s*(?:([A-Z]{3})\s+)?([+-]?[\d,]+(?:\.\d+)?)\s*$")
_CENT = Decimal("0.01")


def parse_amount(value: str | None) -> tuple[str | None, Decimal] | None:
    """Parse a value produced by the reconciliation formatter, e.g. ``MYR 1,044.00``."""
    if not value:
        return None
    match = _MONEY_RE.match(value)
    if not match:
        return None
    try:
        return match.group(1), Decimal(match.group(2).replace(",", ""))
    except InvalidOperation:
        return None


def _round(value: Decimal, places: Decimal = _CENT) -> float:
    return float(value.quantize(places, rounding=ROUND_HALF_UP))


def calculate_variance(txn: TransactionSnap, issue: IssueSnap) -> AskVariance | None:
    family = issue.family
    if family not in {"price", "quantity", "arithmetic"}:
        return None
    expected = parse_amount(issue.expected)
    actual = parse_amount(issue.actual)
    if expected is None or actual is None:
        return None

    currency = actual[0] or expected[0]
    delta = actual[1] - expected[1]
    percentage = (
        _round(delta / expected[1] * Decimal(100)) if expected[1] != 0 else None
    )

    if family == "quantity":
        return AskVariance(
            kind="quantity",
            currency=None,
            expected=float(expected[1]),
            actual=float(actual[1]),
            delta=float(delta),
            percentage=percentage,
        )

    billed_impact: float | None = None
    if family == "price" and issue.item_description:
        line = next(
            (
                candidate
                for candidate in txn.lines
                if candidate.description == issue.item_description
                and candidate.invoice_quantity is not None
            ),
            None,
        )
        if line is not None:
            billed_impact = _round(delta * Decimal(str(line.invoice_quantity)))

    return AskVariance(
        kind="money",
        currency=currency,
        expected=_round(expected[1]),
        actual=_round(actual[1]),
        delta=_round(delta),
        percentage=percentage,
        billed_impact=billed_impact,
    )


def issue_sort_key(issue: IssueSnap) -> tuple:
    return (
        STATUS_RANK.get(issue.status, 9),
        SEVERITY_RANK.get(issue.severity, 9),
        -issue.updated_at.timestamp(),
    )


def compute_metrics(
    pairs: list[tuple[TransactionSnap, IssueSnap]],
    transactions: list[TransactionSnap] | None = None,
) -> AskMetrics:
    txns = transactions
    if txns is None:
        seen: dict[UUID, TransactionSnap] = {}
        for txn, _ in pairs:
            seen.setdefault(txn.id, txn)
        txns = list(seen.values())
    suppliers = {
        _normalise_supplier(txn.supplier) for txn in txns if txn.supplier
    }
    return AskMetrics(
        transaction_count=len(txns),
        issue_count=len(pairs),
        open_issue_count=sum(issue.status == "open" for _, issue in pairs),
        resolved_issue_count=sum(issue.status == "resolved" for _, issue in pairs),
        by_severity=dict(Counter(issue.severity for _, issue in pairs)),
        by_family=dict(Counter(issue.family for _, issue in pairs)),
        by_status=dict(Counter(txn.status for txn in txns)),
        supplier_count=len(suppliers),
    )


# ---------------------------------------------------------------------------
# Entity resolution
# ---------------------------------------------------------------------------


def _normalise_ref(value: str | None) -> str:
    return re.sub(r"[^a-z0-9]", "", (value or "").lower())


def resolve_transaction_ref(
    snapshot: WorkspaceSnapshot, reference: str
) -> list[TransactionSnap]:
    """Match a user reference against transaction names and document numbers."""
    target = _normalise_ref(reference)
    if not target:
        return []

    def keys(txn: TransactionSnap) -> list[str]:
        values = [txn.name, *(doc.document_number for doc in txn.documents)]
        return [_normalise_ref(value) for value in values if value]

    exact = [txn for txn in snapshot.transactions if target in keys(txn)]
    if exact:
        return exact
    if len(target) < 4:
        return []
    return [
        txn
        for txn in snapshot.transactions
        if any(target in key or (len(key) >= 4 and key in target) for key in keys(txn))
    ]


def supplier_matches(candidate: str | None, wanted: str) -> bool:
    if not candidate:
        return False
    a = _normalise_supplier(candidate)
    b = _normalise_supplier(wanted)
    if not a or not b:
        return False
    if b in a or a in b:
        return True
    return _supplier_similarity(candidate, wanted) >= SUPPLIER_MATCH_THRESHOLD


def transaction_matches_supplier(txn: TransactionSnap, wanted: str) -> bool:
    return any(supplier_matches(doc.supplier_name, wanted) for doc in txn.documents)


def resolve_supplier(snapshot: WorkspaceSnapshot, wanted: str) -> str | None:
    """Return the canonical supplier name as stored, or None if unknown."""
    for name in snapshot.suppliers():
        if supplier_matches(name, wanted):
            return name
    return None


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------


@dataclass
class QueryResult:
    result_kind: str  # "issues" | "transactions" | "suppliers" | "insights" | "none"
    metrics: AskMetrics
    issues: list[tuple[TransactionSnap, IssueSnap]] = field(default_factory=list)
    transactions: list[TransactionSnap] = field(default_factory=list)
    suppliers: list[AskSupplierRow] = field(default_factory=list)
    focus: TransactionSnap | None = None
    total_matches: int = 0
    # Phase 6 intelligence intents: computed facts and code-written findings,
    # each finding paired with the issue IDs that support it.
    facts: dict | None = None
    insight_lines: list[tuple[str, list[UUID]]] = field(default_factory=list)


def select_issues(
    snapshot: WorkspaceSnapshot,
    plan: AskPlan,
    *,
    family: str | None = None,
    severity: str | None = None,
    default_status: str = "open",
) -> list[tuple[TransactionSnap, IssueSnap]]:
    filters = plan.filters
    status = filters.status or default_status
    severity = severity or filters.severity
    family = family or filters.issue_type

    selected: list[tuple[TransactionSnap, IssueSnap]] = []
    for txn, issue in snapshot.issue_pairs():
        if filters.transaction_id and txn.id != filters.transaction_id:
            continue
        if filters.supplier and not transaction_matches_supplier(txn, filters.supplier):
            continue
        if status != "any" and issue.status != status:
            continue
        if severity and issue.severity != severity:
            continue
        if family and issue.family != family:
            continue
        if filters.quantity_direction and not _quantity_direction_matches(
            issue, filters.quantity_direction
        ):
            continue
        selected.append((txn, issue))

    if status == "resolved":
        selected.sort(
            key=lambda pair: -(pair[1].resolved_at or pair[1].updated_at).timestamp()
        )
    else:
        selected.sort(key=lambda pair: issue_sort_key(pair[1]))
    return selected


def _quantity_direction_matches(issue: IssueSnap, direction: str) -> bool:
    expected = parse_amount(issue.expected)
    actual = parse_amount(issue.actual)
    if expected is None or actual is None:
        return False
    if direction == "invoice_over_delivery":
        return issue.code == "invoice_delivery_quantity_variance" and actual[1] > expected[1]
    if direction == "delivery_short_of_order":
        return issue.code == "delivery_quantity_variance" and actual[1] < expected[1]
    return False


def _issue_result(
    pairs: list[tuple[TransactionSnap, IssueSnap]], plan: AskPlan
) -> QueryResult:
    return QueryResult(
        result_kind="issues",
        metrics=compute_metrics(pairs),
        issues=pairs[: plan.limit],
        total_matches=len(pairs),
    )


def _transaction_result(
    transactions: list[TransactionSnap], plan: AskPlan
) -> QueryResult:
    pairs = [(txn, issue) for txn in transactions for issue in txn.issues]
    return QueryResult(
        result_kind="transactions",
        metrics=compute_metrics(pairs, transactions),
        transactions=transactions[: plan.limit],
        total_matches=len(transactions),
    )


def _scoped(snapshot: WorkspaceSnapshot, plan: AskPlan) -> list[TransactionSnap]:
    transactions = snapshot.transactions
    if plan.filters.transaction_id:
        transactions = [t for t in transactions if t.id == plan.filters.transaction_id]
    if plan.filters.supplier:
        transactions = [
            t for t in transactions if transaction_matches_supplier(t, plan.filters.supplier)
        ]
    return transactions


def handle_open_issues(snapshot: WorkspaceSnapshot, plan: AskPlan) -> QueryResult:
    return _issue_result(select_issues(snapshot, plan), plan)


def handle_high_severity(snapshot: WorkspaceSnapshot, plan: AskPlan) -> QueryResult:
    return _issue_result(select_issues(snapshot, plan, severity="high"), plan)


def handle_resolved_issues(snapshot: WorkspaceSnapshot, plan: AskPlan) -> QueryResult:
    return _issue_result(
        select_issues(snapshot, plan, default_status="resolved"), plan
    )


def _family_handler(family: str) -> Callable[[WorkspaceSnapshot, AskPlan], QueryResult]:
    def handler(snapshot: WorkspaceSnapshot, plan: AskPlan) -> QueryResult:
        return _issue_result(select_issues(snapshot, plan, family=family), plan)

    return handler


def handle_transaction(snapshot: WorkspaceSnapshot, plan: AskPlan) -> QueryResult:
    txn = snapshot.transaction(plan.filters.transaction_id)
    if txn is None:
        return QueryResult(result_kind="none", metrics=AskMetrics())
    pairs = select_issues(snapshot, plan, default_status="any")
    return QueryResult(
        result_kind="issues",
        metrics=compute_metrics(pairs, [txn]),
        issues=pairs[: plan.limit],
        focus=txn,
        total_matches=len(pairs),
    )


def handle_supplier_issues(snapshot: WorkspaceSnapshot, plan: AskPlan) -> QueryResult:
    if not plan.filters.supplier:
        return handle_supplier_summary(snapshot, plan)
    return _issue_result(select_issues(snapshot, plan), plan)


def handle_supplier_summary(snapshot: WorkspaceSnapshot, plan: AskPlan) -> QueryResult:
    status = plan.filters.status or "open"
    family = plan.filters.issue_type
    groups: dict[str, dict] = defaultdict(
        lambda: {"name": None, "transactions": [], "issues": []}
    )
    for txn in _scoped(snapshot, plan):
        if not txn.supplier:
            continue
        group = groups[_normalise_supplier(txn.supplier)]
        group["name"] = group["name"] or txn.supplier
        group["transactions"].append(txn)
        for issue in txn.issues:
            if family and issue.family != family:
                continue
            if plan.filters.severity and issue.severity != plan.filters.severity:
                continue
            group["issues"].append(issue)

    rows: list[AskSupplierRow] = []
    counted: list[tuple[TransactionSnap, IssueSnap]] = []
    for group in groups.values():
        issues: list[IssueSnap] = group["issues"]
        relevant = [i for i in issues if status == "any" or i.status == status]
        if (family or plan.filters.severity or plan.filters.status) and not relevant:
            continue
        open_issues = [i for i in issues if i.status == "open"]
        by_txn = {txn.id: txn for txn in group["transactions"]}
        counted.extend((by_txn[i.transaction_id], i) for i in relevant)
        rows.append(
            AskSupplierRow(
                supplier=group["name"],
                transaction_count=len(group["transactions"]),
                open_issue_count=len(open_issues),
                resolved_issue_count=sum(i.status == "resolved" for i in issues),
                high_open_count=sum(i.severity == "high" for i in open_issues),
                transaction_ids=[txn.id for txn in group["transactions"]],
                open_by_family=dict(Counter(i.family for i in open_issues)),
            )
        )

    rows.sort(
        key=lambda row: (
            -row.open_issue_count,
            -row.high_open_count,
            row.supplier.lower(),
        )
    )
    metrics = compute_metrics(counted)
    metrics.supplier_count = len(rows)
    counted.sort(key=lambda pair: issue_sort_key(pair[1]))
    return QueryResult(
        result_kind="suppliers",
        metrics=metrics,
        # The underlying issues make every supplier count traceable to evidence.
        issues=counted[: plan.limit],
        suppliers=rows[: plan.limit],
        total_matches=len(rows),
    )


def handle_missing_documents(snapshot: WorkspaceSnapshot, plan: AskPlan) -> QueryResult:
    transactions = [
        txn
        for txn in _scoped(snapshot, plan)
        if txn.missing_document_types
        or txn.reconciled_at is None
        or any(doc.status in {"uploaded", "extracting", "failed"} for doc in txn.documents)
    ]
    return _transaction_result(transactions, plan)


def handle_recent_transactions(snapshot: WorkspaceSnapshot, plan: AskPlan) -> QueryResult:
    transactions = sorted(
        _scoped(snapshot, plan), key=lambda txn: txn.updated_at, reverse=True
    )
    return _transaction_result(transactions, plan)


def handle_resolved_transactions(
    snapshot: WorkspaceSnapshot, plan: AskPlan
) -> QueryResult:
    transactions = sorted(
        (txn for txn in _scoped(snapshot, plan) if txn.status == "resolved"),
        key=lambda txn: txn.last_resolved_at or txn.updated_at,
        reverse=True,
    )
    return _transaction_result(transactions, plan)


def handle_transaction_search(snapshot: WorkspaceSnapshot, plan: AskPlan) -> QueryResult:
    search = _normalise_ref(plan.filters.search)
    transactions = _scoped(snapshot, plan)
    if search:
        transactions = [
            txn
            for txn in transactions
            if any(
                search in _normalise_ref(value)
                for value in (
                    txn.name,
                    *(doc.supplier_name for doc in txn.documents),
                    *(doc.document_number for doc in txn.documents),
                    *(doc.filename for doc in txn.documents),
                )
                if value
            )
        ]
    return _transaction_result(transactions, plan)


def handle_general_summary(snapshot: WorkspaceSnapshot, plan: AskPlan) -> QueryResult:
    transactions = _scoped(snapshot, plan)
    pairs = [(txn, issue) for txn in transactions for issue in txn.issues]
    attention = sorted(
        (txn for txn in transactions if txn.open_issues),
        key=lambda txn: (
            SEVERITY_RANK.get(txn.highest_open_severity or "", 9),
            -len(txn.open_issues),
            -txn.updated_at.timestamp(),
        ),
    )
    return QueryResult(
        result_kind="transactions",
        metrics=compute_metrics(pairs, transactions),
        transactions=attention[: plan.limit],
        total_matches=len(attention),
    )


def handle_unsupported(_: WorkspaceSnapshot, __: AskPlan) -> QueryResult:
    return QueryResult(result_kind="none", metrics=AskMetrics())


INTENT_HANDLERS: dict[AskIntent, Callable[[WorkspaceSnapshot, AskPlan], QueryResult]] = {
    AskIntent.list_open_issues: handle_open_issues,
    AskIntent.list_high_severity_issues: handle_high_severity,
    AskIntent.list_resolved_issues: handle_resolved_issues,
    AskIntent.transaction_details: handle_transaction,
    AskIntent.transaction_explanation: handle_transaction,
    AskIntent.supplier_issues: handle_supplier_issues,
    AskIntent.supplier_issue_summary: handle_supplier_summary,
    AskIntent.price_discrepancies: _family_handler("price"),
    AskIntent.quantity_discrepancies: _family_handler("quantity"),
    AskIntent.supplier_mismatches: _family_handler("supplier"),
    AskIntent.currency_mismatches: _family_handler("currency"),
    AskIntent.arithmetic_mismatches: _family_handler("arithmetic"),
    AskIntent.missing_documents: handle_missing_documents,
    AskIntent.recent_transactions: handle_recent_transactions,
    AskIntent.resolved_transactions: handle_resolved_transactions,
    AskIntent.transaction_search: handle_transaction_search,
    AskIntent.general_summary: handle_general_summary,
    AskIntent.unsupported: handle_unsupported,
}

TRANSACTION_INTENTS = {AskIntent.transaction_details, AskIntent.transaction_explanation}


def run_query(snapshot: WorkspaceSnapshot, plan: AskPlan) -> QueryResult:
    handler = INTENT_HANDLERS.get(plan.intent, handle_unsupported)
    return handler(snapshot, plan)
