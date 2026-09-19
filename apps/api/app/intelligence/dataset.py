"""Typed operational facts that every intelligence calculation runs on.

``load_dataset`` reads PostgreSQL with column projections — only the handful
of extracted fields the metrics need, never whole extraction JSON blobs.
``dataset_from_snapshot`` builds the same facts from Ask cermat.'s snapshot, so
Ask answers and the Overview share one set of calculations.

Only *active* review issues are loaded: an issue that disappeared on a later
reconciliation is history, not a current or trending exception.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    DocumentModel,
    ReviewIssueModel,
    TransactionDocumentModel,
    TransactionSetModel,
)
from app.services.ask_queries import (
    DOCUMENT_ORDER,
    REQUIRED_DOCUMENT_TYPES,
    SEVERITY_RANK,
    family_of,
)
from app.services.reconciliation import _normalise_supplier

if TYPE_CHECKING:
    from app.services.ask_queries import WorkspaceSnapshot

PERIOD_DAYS: dict[str, int | None] = {"7d": 7, "30d": 30, "90d": 90, "all": None}


def supplier_key(name: str | None) -> str | None:
    """Stable supplier identity: exact match after normalising company suffixes.

    "ABC Supplies Sdn. Bhd." and "ABC Supplies Sdn Bhd" share a key; "Delta
    Office" and "Delta Offices Trading" do not. No fuzzy merging.
    """
    normalised = _normalise_supplier(name)
    return "-".join(normalised.split()) or None


@dataclass(frozen=True)
class DocFact:
    id: UUID
    transaction_id: UUID
    document_type: str
    filename: str
    status: str
    supplier_name: str | None
    document_number: str | None
    document_date: str | None
    currency: str | None
    total: float | None
    overall_confidence: float | None
    evidence_count: int | None
    extracted: bool


@dataclass
class IssueFact:
    id: UUID
    transaction_id: UUID
    code: str
    title: str
    severity: str
    status: str
    item_description: str | None
    expected: str | None
    actual: str | None
    created_at: datetime
    resolved_at: datetime | None

    @property
    def family(self) -> str:
        return family_of(self.code)

    @property
    def is_open(self) -> bool:
        return self.status == "open"


@dataclass
class TxnFact:
    id: UUID
    name: str
    status: str
    created_at: datetime
    updated_at: datetime
    reconciled: bool = False
    lines: list[dict[str, Any]] = field(default_factory=list)
    documents: list[DocFact] = field(default_factory=list)
    issues: list[IssueFact] = field(default_factory=list)

    def _document(self, *types: str) -> DocFact | None:
        for document_type in types:
            for document in self.documents:
                if document.document_type == document_type:
                    return document
        return None

    @property
    def supplier(self) -> str | None:
        # The purchase order names the agreed counterparty.
        for document_type in ("purchase_order", "invoice", "delivery_order", "receipt"):
            document = self._document(document_type)
            if document and document.supplier_name:
                return document.supplier_name
        return None

    @property
    def supplier_key(self) -> str | None:
        return supplier_key(self.supplier)

    @property
    def currency(self) -> str | None:
        for document in (self._document("invoice"), self._document("purchase_order")):
            if document and document.currency:
                return document.currency.upper()
        return None

    @property
    def invoice(self) -> DocFact | None:
        return self._document("invoice")

    @property
    def missing_document_types(self) -> list[str]:
        present = {document.document_type for document in self.documents}
        return [value for value in REQUIRED_DOCUMENT_TYPES if value not in present]

    @property
    def open_issues(self) -> list[IssueFact]:
        return [issue for issue in self.issues if issue.is_open]

    @property
    def highest_open_severity(self) -> str | None:
        ranked = sorted((i.severity for i in self.open_issues), key=lambda s: SEVERITY_RANK.get(s, 9))
        return ranked[0] if ranked else None


@dataclass
class IntelDataset:
    transactions: list[TxnFact]

    def __post_init__(self) -> None:
        self._by_id = {txn.id: txn for txn in self.transactions}

    def transaction(self, transaction_id: UUID) -> TxnFact | None:
        return self._by_id.get(transaction_id)

    def issue_pairs(self) -> list[tuple[TxnFact, IssueFact]]:
        return [(txn, issue) for txn in self.transactions for issue in txn.issues]

    def open_pairs(self) -> list[tuple[TxnFact, IssueFact]]:
        return [(txn, issue) for txn, issue in self.issue_pairs() if issue.is_open]

    @property
    def is_empty(self) -> bool:
        return not self.transactions


# ---------------------------------------------------------------------------
# Time windows — calculated in code, never interpreted by a model
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Window:
    period: str
    start: datetime | None  # inclusive; None = all time
    end: datetime  # inclusive
    previous_start: datetime | None  # previous window is [previous_start, start)

    def contains(self, moment: datetime | None) -> bool:
        if moment is None:
            return False
        return (self.start is None or moment >= self.start) and moment <= self.end

    def in_previous(self, moment: datetime | None) -> bool:
        if moment is None or self.start is None or self.previous_start is None:
            return False
        return self.previous_start <= moment < self.start


def window_for(period: str, now: datetime) -> Window:
    if period not in PERIOD_DAYS:
        raise ValueError(f"Unsupported period: {period}")
    days = PERIOD_DAYS[period]
    if days is None:
        return Window(period=period, start=None, end=now, previous_start=None)
    start = now - timedelta(days=days)
    return Window(period=period, start=start, end=now, previous_start=start - timedelta(days=days))


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _aware(value: datetime | None) -> datetime | None:
    if value is not None and value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


# ---------------------------------------------------------------------------
# Loaders
# ---------------------------------------------------------------------------


async def load_dataset(session: AsyncSession, workspace_id: UUID) -> IntelDataset:
    """Three projected SELECTs over one workspace. Read-only."""
    reconciliation = TransactionSetModel.last_reconciliation
    txn_rows = await session.execute(
        select(
            TransactionSetModel.id,
            TransactionSetModel.name,
            TransactionSetModel.status,
            TransactionSetModel.created_at,
            TransactionSetModel.updated_at,
            reconciliation["lines"].label("lines"),
            reconciliation["generated_at"].as_string().label("reconciled_at"),
        ).where(TransactionSetModel.workspace_id == workspace_id)
    )
    transactions: dict[UUID, TxnFact] = {}
    for row in txn_rows.all():
        transactions[row.id] = TxnFact(
            id=row.id,
            name=row.name,
            status=row.status,
            created_at=_aware(row.created_at),
            updated_at=_aware(row.updated_at),
            reconciled=row.reconciled_at is not None,
            lines=row.lines if isinstance(row.lines, list) else [],
        )
    if not transactions:
        return IntelDataset(transactions=[])

    data = DocumentModel.extraction_data
    doc_rows = await session.execute(
        select(
            TransactionDocumentModel.transaction_id,
            DocumentModel.id,
            DocumentModel.document_type,
            DocumentModel.filename,
            DocumentModel.status,
            data["supplier_name"].as_string().label("supplier_name"),
            data["document_number"].as_string().label("document_number"),
            data["document_date"].as_string().label("document_date"),
            data["currency"].as_string().label("currency"),
            data["total"].as_float().label("total"),
            data["overall_confidence"].as_float().label("overall_confidence"),
            func.json_array_length(data["evidence"]).label("evidence_count"),
            (data.is_not(None)).label("extracted"),
        )
        .join(DocumentModel, TransactionDocumentModel.document_id == DocumentModel.id)
        .where(DocumentModel.workspace_id == workspace_id)
    )
    for row in doc_rows.all():
        txn = transactions.get(row.transaction_id)
        if txn is None:
            continue
        txn.documents.append(
            DocFact(
                id=row.id,
                transaction_id=row.transaction_id,
                document_type=row.document_type,
                filename=row.filename,
                status=row.status,
                supplier_name=row.supplier_name,
                document_number=row.document_number,
                document_date=row.document_date,
                currency=row.currency,
                total=row.total,
                overall_confidence=row.overall_confidence,
                evidence_count=row.evidence_count,
                extracted=bool(row.extracted),
            )
        )

    payload = ReviewIssueModel.payload
    issue_rows = await session.execute(
        select(
            ReviewIssueModel.id,
            ReviewIssueModel.transaction_id,
            ReviewIssueModel.code,
            ReviewIssueModel.title,
            ReviewIssueModel.severity,
            ReviewIssueModel.status,
            ReviewIssueModel.created_at,
            ReviewIssueModel.resolved_at,
            payload["item_description"].as_string().label("item_description"),
            payload["expected"].as_string().label("expected"),
            payload["actual"].as_string().label("actual"),
        ).where(ReviewIssueModel.workspace_id == workspace_id, ReviewIssueModel.active.is_(True))
    )
    for row in issue_rows.all():
        txn = transactions.get(row.transaction_id)
        if txn is None:
            continue
        txn.issues.append(
            IssueFact(
                id=row.id,
                transaction_id=row.transaction_id,
                code=row.code,
                title=row.title,
                severity=row.severity,
                status=row.status,
                item_description=row.item_description,
                expected=row.expected,
                actual=row.actual,
                created_at=_aware(row.created_at),
                resolved_at=_aware(row.resolved_at) if row.status == "resolved" else None,
            )
        )

    for txn in transactions.values():
        txn.documents.sort(key=lambda doc: DOCUMENT_ORDER.get(doc.document_type, 99))
    return IntelDataset(transactions=list(transactions.values()))


def dataset_from_snapshot(snapshot: "WorkspaceSnapshot") -> IntelDataset:
    """Adapt Ask cermat.'s snapshot (already loaded) into intelligence facts."""
    transactions: list[TxnFact] = []
    for snap in snapshot.transactions:
        txn = TxnFact(
            id=snap.id,
            name=snap.name,
            status=snap.status,
            created_at=_aware(snap.created_at),
            updated_at=_aware(snap.updated_at),
            reconciled=snap.reconciled_at is not None,
            lines=[line.model_dump(mode="json") for line in snap.lines],
        )
        for doc in snap.documents:
            txn.documents.append(
                DocFact(
                    id=doc.id,
                    transaction_id=snap.id,
                    document_type=doc.document_type,
                    filename=doc.filename,
                    status=doc.status,
                    supplier_name=doc.supplier_name,
                    document_number=doc.document_number,
                    document_date=None,
                    currency=doc.currency,
                    total=doc.total,
                    overall_confidence=doc.overall_confidence,
                    evidence_count=len(doc.evidence),
                    extracted=doc.overall_confidence is not None,
                )
            )
        for issue in snap.issues:
            txn.issues.append(
                IssueFact(
                    id=issue.id,
                    transaction_id=snap.id,
                    code=issue.code,
                    title=issue.title,
                    severity=issue.severity,
                    status=issue.status,
                    item_description=issue.item_description,
                    expected=issue.expected,
                    actual=issue.actual,
                    created_at=_aware(issue.created_at),
                    resolved_at=_aware(issue.resolved_at) if issue.status == "resolved" else None,
                )
            )
        transactions.append(txn)
    return IntelDataset(transactions=transactions)
