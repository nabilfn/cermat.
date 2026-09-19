"""CSV export of review issues. Workspace-scoped like every other read."""

from __future__ import annotations

import csv
import io
from datetime import datetime, timezone
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import WorkspaceContext, workspace_context
from app.database import get_session
from app.models import ReviewIssueModel, TransactionSetModel, UserModel
from app.services import audit
from app.services.transactions import get_transaction

router = APIRouter(prefix="/api/v1/exports", tags=["exports"])
MAX_ROWS = 10_000
COLUMNS = [
    "transaction", "transaction_status", "issue", "code", "severity", "status", "item",
    "expected", "actual", "difference", "explanation", "resolution_note", "resolved_by",
    "resolved_at", "created_at", "sources",
]


def safe_cell(value: object) -> str:
    """Neutralise spreadsheet formula injection (=, +, -, @, tab, CR at cell start)."""
    text = "" if value is None else str(value)
    return "'" + text if text[:1] in {"=", "+", "-", "@", "\t", "\r"} else text


@router.get("/review-issues.csv", summary="Export review issues as CSV", response_class=Response)
async def export_review_issues(
    status: Literal["open", "resolved", "all"] = Query(default="all"),
    transaction_id: UUID | None = Query(default=None),
    ctx: WorkspaceContext = Depends(workspace_context),
    session: AsyncSession = Depends(get_session),
) -> Response:
    query = (
        select(ReviewIssueModel, TransactionSetModel.name, TransactionSetModel.status, UserModel.display_name)
        .join(TransactionSetModel, TransactionSetModel.id == ReviewIssueModel.transaction_id)
        .outerjoin(UserModel, UserModel.id == ReviewIssueModel.resolved_by)
        .where(ReviewIssueModel.workspace_id == ctx.workspace_id, ReviewIssueModel.active.is_(True))
        .order_by(TransactionSetModel.name, ReviewIssueModel.created_at)
        .limit(MAX_ROWS)
    )
    if transaction_id is not None:
        await get_transaction(session, ctx.workspace_id, transaction_id)  # 404 if not in this workspace
        query = query.where(ReviewIssueModel.transaction_id == transaction_id)
    if status != "all":
        query = query.where(ReviewIssueModel.status == status)
    rows = (await session.execute(query)).all()

    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(COLUMNS)
    for issue, transaction_name, transaction_status, resolver in rows:
        payload = issue.payload or {}
        sources = "; ".join(
            f"{s.get('filename')} p.{s.get('page') or '-'} {s.get('field_path')}"
            for s in payload.get("sources") or []
        )
        writer.writerow([
            safe_cell(v)
            for v in (
                transaction_name, transaction_status, issue.title, issue.code, issue.severity, issue.status,
                payload.get("item_description"), payload.get("expected"), payload.get("actual"),
                payload.get("delta"), payload.get("explanation"), issue.resolution_note,
                resolver if issue.status == "resolved" else None,
                issue.resolved_at.isoformat() if issue.resolved_at else None,
                issue.created_at.isoformat(), sources,
            )
        ])

    audit.record(session, workspace_id=ctx.workspace_id, actor_user_id=ctx.user.id, action="export_downloaded",
                 entity_type="export", entity_id=str(transaction_id) if transaction_id else None,
                 metadata={"rows": len(rows), "status": status})
    await session.commit()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M")
    return Response(
        "﻿" + buffer.getvalue(),  # BOM so spreadsheet apps detect UTF-8
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="cermat-review-issues-{stamp}.csv"',
            "Cache-Control": "private, no-store",
        },
    )
