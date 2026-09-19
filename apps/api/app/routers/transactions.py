"""Transactions, reconciliation, review issues, activity and deletion."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import WorkspaceContext, workspace_context
from app.core.errors import ApiError
from app.database import get_session
from app.models import (
    AuditEventModel,
    ReviewIssueModel,
    TransactionDocumentModel,
    TransactionSetModel,
    UserModel,
)
from app.schemas import (
    AuditEventRecord,
    ReconciliationResult,
    ReviewIssueRecord,
    ReviewIssueUpdate,
    TransactionCreate,
    TransactionDetail,
    TransactionPage,
    TransactionRecord,
)
from app.services import audit
from app.services.storage import get_storage
from app.services.transactions import (
    REQUIRED_THREE_WAY_TYPES,
    get_document,
    get_issue,
    get_transaction,
    history_page,
    reconcile,
    refresh_transaction_status,
    review_issue_record,
    review_issues,
    transaction_detail,
    transaction_documents,
    transaction_record,
)

logger = logging.getLogger("cermat.transactions")
router = APIRouter(prefix="/api/v1/transactions", tags=["transactions"])


@router.get("", response_model=TransactionPage, summary="Transaction history (paginated, searchable)")
async def list_transactions(
    q: str | None = Query(default=None, max_length=120, description="Transaction name, document number or supplier"),
    status: Literal["open", "review_required", "resolved", "matched"] | None = Query(default=None),
    limit: int = Query(default=25, ge=1, le=100),
    offset: int = Query(default=0, ge=0, le=100_000),
    ctx: WorkspaceContext = Depends(workspace_context),
    session: AsyncSession = Depends(get_session),
) -> TransactionPage:
    return await history_page(session, ctx.workspace_id, limit=limit, offset=offset, query=q, status=status)


@router.post("", response_model=TransactionRecord, status_code=201, summary="Create a transaction set")
async def create_transaction(
    payload: TransactionCreate,
    ctx: WorkspaceContext = Depends(workspace_context),
    session: AsyncSession = Depends(get_session),
) -> TransactionRecord:
    name = (payload.name or "").strip() or f"Review {datetime.now(timezone.utc):%Y-%m-%d %H:%M UTC}"
    transaction = TransactionSetModel(
        workspace_id=ctx.workspace_id, name=name, status="collecting", created_by=ctx.user.id
    )
    session.add(transaction)
    await session.flush()
    audit.record(session, workspace_id=ctx.workspace_id, actor_user_id=ctx.user.id, action="transaction_created",
                 entity_type="transaction", entity_id=transaction.id)
    await session.commit()
    return transaction_record(transaction)


@router.get("/{transaction_id}", response_model=TransactionDetail, summary="Transaction detail")
async def get_transaction_detail(
    transaction_id: UUID,
    ctx: WorkspaceContext = Depends(workspace_context),
    session: AsyncSession = Depends(get_session),
) -> TransactionDetail:
    return await transaction_detail(session, await get_transaction(session, ctx.workspace_id, transaction_id))


@router.post(
    "/{transaction_id}/documents/{document_id}",
    response_model=TransactionDetail,
    summary="Attach a document to a transaction",
)
async def attach_document(
    transaction_id: UUID,
    document_id: UUID,
    ctx: WorkspaceContext = Depends(workspace_context),
    session: AsyncSession = Depends(get_session),
) -> TransactionDetail:
    transaction = await get_transaction(session, ctx.workspace_id, transaction_id)
    document = await get_document(session, ctx.workspace_id, document_id)
    if await session.scalar(
        select(TransactionDocumentModel.id).where(
            TransactionDocumentModel.transaction_id == transaction.id,
            TransactionDocumentModel.document_type == document.document_type,
        )
    ):
        raise ApiError(409, "CONFLICT", f"This transaction already has a {document.document_type.replace('_', ' ')}.")
    session.add(
        TransactionDocumentModel(transaction_id=transaction.id, document_id=document.id, document_type=document.document_type)
    )
    try:
        await session.flush()
    except IntegrityError as exc:
        await session.rollback()
        raise ApiError(409, "CONFLICT", "This document is already attached to a transaction.") from exc

    types = {d.document_type for d in await transaction_documents(session, transaction.id)}
    transaction.status = "ready" if REQUIRED_THREE_WAY_TYPES <= types else "collecting"
    audit.record(session, workspace_id=ctx.workspace_id, actor_user_id=ctx.user.id, action="document_attached",
                 entity_type="transaction", entity_id=transaction.id,
                 metadata={"document_id": str(document.id), "document_type": document.document_type})
    await session.commit()
    return await transaction_detail(session, transaction)


@router.post("/{transaction_id}/reconcile", response_model=ReconciliationResult, summary="Run three-way matching")
async def reconcile_transaction(
    transaction_id: UUID,
    ctx: WorkspaceContext = Depends(workspace_context),
    session: AsyncSession = Depends(get_session),
) -> ReconciliationResult:
    transaction = await get_transaction(session, ctx.workspace_id, transaction_id)
    result = await reconcile(session, transaction)
    audit.record(session, workspace_id=ctx.workspace_id, actor_user_id=ctx.user.id, action="reconciliation_run",
                 entity_type="transaction", entity_id=transaction.id,
                 metadata={"status": result.status, "issues": result.summary.issue_count})
    await session.commit()
    return result


@router.get(
    "/{transaction_id}/reconciliation",
    response_model=ReconciliationResult,
    summary="Latest reconciliation result",
)
async def get_last_reconciliation(
    transaction_id: UUID,
    ctx: WorkspaceContext = Depends(workspace_context),
    session: AsyncSession = Depends(get_session),
) -> ReconciliationResult:
    transaction = await get_transaction(session, ctx.workspace_id, transaction_id)
    if not transaction.last_reconciliation:
        raise ApiError(404, "NOT_FOUND", "No reconciliation has been run yet.")
    return ReconciliationResult.model_validate(transaction.last_reconciliation)


@router.get("/{transaction_id}/issues", response_model=list[ReviewIssueRecord], summary="Active review issues")
async def list_review_issues(
    transaction_id: UUID,
    ctx: WorkspaceContext = Depends(workspace_context),
    session: AsyncSession = Depends(get_session),
) -> list[ReviewIssueRecord]:
    transaction = await get_transaction(session, ctx.workspace_id, transaction_id)
    return [review_issue_record(issue, name) for issue, name in await review_issues(session, transaction.id)]


@router.patch(
    "/{transaction_id}/issues/{issue_id}",
    response_model=ReviewIssueRecord,
    summary="Resolve or reopen a review issue",
)
async def update_review_issue(
    transaction_id: UUID,
    issue_id: UUID,
    payload: ReviewIssueUpdate,
    ctx: WorkspaceContext = Depends(workspace_context),
    session: AsyncSession = Depends(get_session),
) -> ReviewIssueRecord:
    transaction = await get_transaction(session, ctx.workspace_id, transaction_id)
    issue = await get_issue(session, ctx.workspace_id, transaction.id, issue_id)
    previous = issue.status
    issue.status = payload.status
    issue.resolution_note = (payload.resolution_note or "").strip() or None
    resolved = payload.status == "resolved"
    issue.resolved_at = datetime.now(timezone.utc) if resolved else None
    issue.resolved_by = ctx.user.id if resolved else None
    if previous != payload.status:
        audit.record(session, workspace_id=ctx.workspace_id, actor_user_id=ctx.user.id,
                     action="issue_resolved" if resolved else "issue_reopened",
                     entity_type="review_issue", entity_id=issue.id,
                     metadata={"transaction_id": str(transaction.id), "code": issue.code,
                               "with_note": issue.resolution_note is not None})
    await refresh_transaction_status(session, transaction)
    await session.commit()
    await session.refresh(issue)
    return review_issue_record(issue, ctx.user.display_name if resolved else None)


@router.get(
    "/{transaction_id}/activity",
    response_model=list[AuditEventRecord],
    summary="Compact activity history for a transaction",
)
async def transaction_activity(
    transaction_id: UUID,
    ctx: WorkspaceContext = Depends(workspace_context),
    session: AsyncSession = Depends(get_session),
) -> list[AuditEventRecord]:
    transaction = await get_transaction(session, ctx.workspace_id, transaction_id)
    document_ids = [str(d.id) for d in await transaction_documents(session, transaction.id)]
    issue_ids = [
        str(i)
        for i in (
            await session.execute(select(ReviewIssueModel.id).where(ReviewIssueModel.transaction_id == transaction.id))
        ).scalars()
    ]
    rows = await session.execute(
        select(AuditEventModel, UserModel.display_name)
        .outerjoin(UserModel, UserModel.id == AuditEventModel.actor_user_id)
        .where(
            AuditEventModel.workspace_id == ctx.workspace_id,
            AuditEventModel.entity_id.in_([str(transaction.id), *document_ids, *issue_ids]),
        )
        .order_by(AuditEventModel.created_at.asc())
        .limit(100)
    )
    return [
        AuditEventRecord(id=e.id, action=e.action, entity_type=e.entity_type, entity_id=e.entity_id,
                         actor_name=name, metadata=e.metadata_json or {}, created_at=e.created_at)
        for e, name in rows.all()
    ]


@router.delete("/{transaction_id}", status_code=204, summary="Delete a transaction, its documents, issues and files")
async def delete_transaction(
    transaction_id: UUID,
    ctx: WorkspaceContext = Depends(workspace_context),
    session: AsyncSession = Depends(get_session),
) -> Response:
    transaction = await get_transaction(session, ctx.workspace_id, transaction_id)
    documents = await transaction_documents(session, transaction.id)
    keys = [d.storage_key for d in documents if d.storage_key]
    name = transaction.name
    await session.delete(transaction)  # cascades links, review issues, attention events
    for document in documents:
        await session.delete(document)
    audit.record(session, workspace_id=ctx.workspace_id, actor_user_id=ctx.user.id, action="transaction_deleted",
                 entity_type="transaction", entity_id=transaction_id,
                 metadata={"name": name, "documents": len(documents)})
    await session.commit()
    storage = get_storage()
    for key in keys:
        try:
            await storage.delete(key)
        except Exception:  # noqa: BLE001
            logger.warning("storage_delete_failed", extra={"event": "storage_delete_failed", "key": key})
    return Response(status_code=204)

