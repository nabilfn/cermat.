"""Workspace-scoped persistence helpers for documents, transactions and review issues.

Every lookup takes the caller's workspace id. A record from another workspace
is indistinguishable from a missing one (404), so ids cannot be probed.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import Float, and_, case, cast, exists, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ApiError, not_found
from app.models import (
    DocumentModel,
    ReviewIssueModel,
    TransactionDocumentModel,
    TransactionSetModel,
    UserModel,
)
from app.schemas import (
    AIExtraction,
    DocumentRecord,
    DocumentType,
    ReconciliationIssue,
    ReconciliationResult,
    ReviewIssueRecord,
    ReviewSummary,
    TransactionDetail,
    TransactionDocumentSummary,
    TransactionHistoryRecord,
    TransactionPage,
    TransactionRecord,
)
from app.services.reconciliation import ReconciliationDocument, reconcile_three_way
from app.services.review import make_issue_key

REQUIRED_THREE_WAY_TYPES = {
    DocumentType.purchase_order.value,
    DocumentType.delivery_order.value,
    DocumentType.invoice.value,
}
DOCUMENT_ORDER = {"purchase_order": 0, "delivery_order": 1, "invoice": 2, "receipt": 3}
OPEN_STATES = ("collecting", "ready", "insufficient_data")


# ---------------------------------------------------------------------------
# Record builders
# ---------------------------------------------------------------------------


def document_record(document: DocumentModel) -> DocumentRecord:
    return DocumentRecord(
        id=document.id,
        filename=document.filename,
        document_type=DocumentType(document.document_type),
        mime_type=document.mime_type,
        size_bytes=document.size_bytes,
        page_count=document.page_count,
        status=document.status,
        error_code=document.error_code,
        has_source_file=document.storage_key is not None,
        created_at=document.created_at,
    )


def document_summary(document: DocumentModel) -> TransactionDocumentSummary:
    extraction = document.extraction_data or {}
    return TransactionDocumentSummary(
        id=document.id,
        filename=document.filename,
        document_type=DocumentType(document.document_type),
        status=document.status,
        document_number=extraction.get("document_number"),
        supplier_name=extraction.get("supplier_name"),
        currency=extraction.get("currency"),
        total=extraction.get("total"),
        overall_confidence=extraction.get("overall_confidence"),
    )


def transaction_record(transaction: TransactionSetModel) -> TransactionRecord:
    return TransactionRecord(
        id=transaction.id,
        name=transaction.name,
        status=transaction.status,
        created_at=transaction.created_at,
        updated_at=transaction.updated_at,
    )


def review_issue_record(issue: ReviewIssueModel, resolved_by_name: str | None = None) -> ReviewIssueRecord:
    payload = ReconciliationIssue.model_validate(issue.payload)
    return ReviewIssueRecord(
        id=issue.id,
        transaction_id=issue.transaction_id,
        issue_key=issue.issue_key,
        status=issue.status,
        resolution_note=issue.resolution_note,
        resolved_by_name=resolved_by_name if issue.status == "resolved" else None,
        active=issue.active,
        resolved_at=issue.resolved_at,
        created_at=issue.created_at,
        updated_at=issue.updated_at,
        **payload.model_dump(),
    )


# ---------------------------------------------------------------------------
# Scoped lookups
# ---------------------------------------------------------------------------


async def get_transaction(session: AsyncSession, workspace_id: UUID, transaction_id: UUID) -> TransactionSetModel:
    transaction = await session.get(TransactionSetModel, transaction_id)
    if transaction is None or transaction.workspace_id != workspace_id:
        raise not_found("Transaction")
    return transaction


async def get_document(session: AsyncSession, workspace_id: UUID, document_id: UUID) -> DocumentModel:
    document = await session.get(DocumentModel, document_id)
    if document is None or document.workspace_id != workspace_id:
        raise not_found("Document")
    return document


async def get_issue(
    session: AsyncSession, workspace_id: UUID, transaction_id: UUID, issue_id: UUID
) -> ReviewIssueModel:
    issue = await session.get(ReviewIssueModel, issue_id)
    if (
        issue is None
        or issue.workspace_id != workspace_id
        or issue.transaction_id != transaction_id
        or not issue.active
    ):
        raise not_found("Review issue")
    return issue


async def transaction_documents(session: AsyncSession, transaction_id: UUID) -> list[DocumentModel]:
    result = await session.execute(
        select(DocumentModel)
        .join(TransactionDocumentModel, TransactionDocumentModel.document_id == DocumentModel.id)
        .where(TransactionDocumentModel.transaction_id == transaction_id)
    )
    return sorted(result.scalars().all(), key=lambda d: DOCUMENT_ORDER.get(d.document_type, 99))


def _issue_order(issue: ReviewIssueModel) -> tuple:
    return (
        {"open": 0, "resolved": 1}.get(issue.status, 9),
        {"high": 0, "medium": 1, "low": 2}.get(issue.severity, 9),
        issue.created_at,
    )


async def review_issues(
    session: AsyncSession, transaction_id: UUID, *, active_only: bool = True
) -> list[tuple[ReviewIssueModel, str | None]]:
    query = (
        select(ReviewIssueModel, UserModel.display_name)
        .outerjoin(UserModel, UserModel.id == ReviewIssueModel.resolved_by)
        .where(ReviewIssueModel.transaction_id == transaction_id)
    )
    if active_only:
        query = query.where(ReviewIssueModel.active.is_(True))
    rows = (await session.execute(query)).all()
    return sorted(((row[0], row[1]) for row in rows), key=lambda pair: _issue_order(pair[0]))


async def review_summary(session: AsyncSession, transaction_id: UUID) -> ReviewSummary:
    issues = [issue for issue, _ in await review_issues(session, transaction_id)]
    return ReviewSummary(
        issue_count=len(issues),
        open_issue_count=sum(i.status == "open" for i in issues),
        resolved_issue_count=sum(i.status == "resolved" for i in issues),
        high_count=sum(i.severity == "high" for i in issues),
    )


async def refresh_transaction_status(session: AsyncSession, transaction: TransactionSetModel) -> None:
    issues = [issue for issue, _ in await review_issues(session, transaction.id)]
    if issues:
        transaction.status = "review_required" if any(i.status == "open" for i in issues) else "resolved"
    elif transaction.last_reconciliation:
        reconciliation = ReconciliationResult.model_validate(transaction.last_reconciliation)
        transaction.status = reconciliation.status


async def transaction_detail(session: AsyncSession, transaction: TransactionSetModel) -> TransactionDetail:
    documents = await transaction_documents(session, transaction.id)
    return TransactionDetail(
        **transaction_record(transaction).model_dump(),
        documents=[document_summary(document) for document in documents],
        review=await review_summary(session, transaction.id),
    )


# ---------------------------------------------------------------------------
# History: paginated, searchable, batched (no per-row queries)
# ---------------------------------------------------------------------------


def _like(term: str) -> str:
    escaped = term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


async def history_page(
    session: AsyncSession,
    workspace_id: UUID,
    *,
    limit: int,
    offset: int,
    query: str | None,
    status: str | None,
) -> TransactionPage:
    conditions = [TransactionSetModel.workspace_id == workspace_id]
    term = (query or "").strip()
    if term:
        pattern = _like(term)
        data = DocumentModel.extraction_data
        conditions.append(
            or_(
                TransactionSetModel.name.ilike(pattern, escape="\\"),
                exists(
                    select(1)
                    .select_from(TransactionDocumentModel)
                    .join(DocumentModel, DocumentModel.id == TransactionDocumentModel.document_id)
                    .where(
                        TransactionDocumentModel.transaction_id == TransactionSetModel.id,
                        or_(
                            data["supplier_name"].as_string().ilike(pattern, escape="\\"),
                            data["document_number"].as_string().ilike(pattern, escape="\\"),
                            DocumentModel.filename.ilike(pattern, escape="\\"),
                        ),
                    )
                ),
            )
        )
    if status == "open":
        conditions.append(TransactionSetModel.status.in_(OPEN_STATES))
    elif status:
        conditions.append(TransactionSetModel.status == status)

    total = await session.scalar(select(func.count()).select_from(TransactionSetModel).where(and_(*conditions)))
    transactions = (
        await session.execute(
            select(TransactionSetModel)
            .where(and_(*conditions))
            .order_by(TransactionSetModel.updated_at.desc(), TransactionSetModel.id)
            .limit(limit)
            .offset(offset)
        )
    ).scalars().all()
    ids = [t.id for t in transactions]

    documents: dict[UUID, list] = {i: [] for i in ids}
    counts: dict[UUID, tuple[int, int, int, int]] = {}
    if ids:
        data = DocumentModel.extraction_data
        doc_rows = await session.execute(
            select(
                TransactionDocumentModel.transaction_id,
                DocumentModel.document_type,
                data["supplier_name"].as_string(),
                data["currency"].as_string(),
                cast(data["total"].as_string(), Float),
            )
            .join(DocumentModel, DocumentModel.id == TransactionDocumentModel.document_id)
            .where(TransactionDocumentModel.transaction_id.in_(ids))
        )
        for txn_id, doc_type, supplier, currency, total_value in doc_rows.all():
            documents[txn_id].append((doc_type, supplier, currency, total_value))
        count_rows = await session.execute(
            select(
                ReviewIssueModel.transaction_id,
                func.count(),
                func.sum(case((ReviewIssueModel.status == "open", 1), else_=0)),
                func.sum(case((ReviewIssueModel.status == "resolved", 1), else_=0)),
                func.sum(case((ReviewIssueModel.severity == "high", 1), else_=0)),
            )
            .where(ReviewIssueModel.transaction_id.in_(ids), ReviewIssueModel.active.is_(True))
            .group_by(ReviewIssueModel.transaction_id)
        )
        for txn_id, total_count, open_count, resolved_count, high_count in count_rows.all():
            counts[txn_id] = (total_count, int(open_count or 0), int(resolved_count or 0), int(high_count or 0))

    items: list[TransactionHistoryRecord] = []
    for transaction in transactions:
        docs = sorted(documents[transaction.id], key=lambda d: {"invoice": 0}.get(d[0], 1))
        preferred = docs[0] if docs else (None, None, None, None)
        issue_count, open_count, resolved_count, high_count = counts.get(transaction.id, (0, 0, 0, 0))
        items.append(
            TransactionHistoryRecord(
                **transaction_record(transaction).model_dump(),
                document_count=len(docs),
                issue_count=issue_count,
                open_issue_count=open_count,
                resolved_issue_count=resolved_count,
                high_count=high_count,
                supplier_name=preferred[1],
                currency=preferred[2],
                total=preferred[3],
            )
        )
    return TransactionPage(items=items, total=int(total or 0), limit=limit, offset=offset)


# ---------------------------------------------------------------------------
# Reconciliation and review-issue sync
# ---------------------------------------------------------------------------


async def sync_review_issues(
    session: AsyncSession, transaction: TransactionSetModel, reconciliation: ReconciliationResult
) -> list[ReviewIssueModel]:
    existing = (
        await session.execute(select(ReviewIssueModel).where(ReviewIssueModel.transaction_id == transaction.id))
    ).scalars().all()
    by_key = {issue.issue_key: issue for issue in existing}
    for stored in existing:
        stored.active = False

    for issue in reconciliation.issues:
        key = make_issue_key(issue)
        stored = by_key.get(key)
        if stored is None:
            stored = ReviewIssueModel(
                workspace_id=transaction.workspace_id,
                transaction_id=transaction.id,
                issue_key=key,
                code=issue.code,
                title=issue.title,
                severity=issue.severity,
                status="open",
                payload=issue.model_dump(mode="json"),
                active=True,
            )
            session.add(stored)
            by_key[key] = stored
        else:
            stored.code = issue.code
            stored.title = issue.title
            stored.severity = issue.severity
            stored.payload = issue.model_dump(mode="json")
            stored.active = True
    await session.flush()
    return [issue for issue, _ in await review_issues(session, transaction.id)]


async def reconcile(session: AsyncSession, transaction: TransactionSetModel) -> ReconciliationResult:
    documents = await transaction_documents(session, transaction.id)
    by_type = {document.document_type: document for document in documents}
    missing = REQUIRED_THREE_WAY_TYPES - set(by_type)
    if missing:
        readable = ", ".join(sorted(m.replace("_", " ") for m in missing))
        raise ApiError(409, "RECONCILIATION_FAILED", f"Three-way matching still needs: {readable}.")
    for document_type in REQUIRED_THREE_WAY_TYPES:
        if not by_type[document_type].extraction_data:
            raise ApiError(
                409,
                "RECONCILIATION_FAILED",
                f"The {document_type.replace('_', ' ')} has not been extracted yet.",
            )

    def as_input(document_type: DocumentType) -> ReconciliationDocument:
        document = by_type[document_type.value]
        return ReconciliationDocument(
            id=document.id,
            filename=document.filename,
            document_type=document_type,
            extraction=AIExtraction.model_validate(document.extraction_data),
        )

    result = reconcile_three_way(
        transaction_id=transaction.id,
        po=as_input(DocumentType.purchase_order),
        delivery=as_input(DocumentType.delivery_order),
        invoice=as_input(DocumentType.invoice),
        documents=[document_summary(document) for document in documents],
    )
    transaction.last_reconciliation = result.model_dump(mode="json")
    await sync_review_issues(session, transaction, result)
    await refresh_transaction_status(session, transaction)
    transaction.updated_at = datetime.now(timezone.utc)
    return result
