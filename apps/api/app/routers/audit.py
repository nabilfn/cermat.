"""Workspace audit trail (read-only; newest first, keyset pagination)."""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import WorkspaceContext, workspace_context
from app.database import get_session
from app.models import AuditEventModel, UserModel
from app.schemas import AuditEventRecord, AuditPage

router = APIRouter(prefix="/api/v1/audit", tags=["audit"])


@router.get("", response_model=AuditPage, summary="Audit events for the current workspace")
async def list_audit_events(
    limit: int = Query(default=50, ge=1, le=200),
    before: datetime | None = Query(default=None, description="Return events older than this timestamp"),
    action: str | None = Query(default=None, max_length=60),
    ctx: WorkspaceContext = Depends(workspace_context),
    session: AsyncSession = Depends(get_session),
) -> AuditPage:
    query = (
        select(AuditEventModel, UserModel.display_name)
        .outerjoin(UserModel, UserModel.id == AuditEventModel.actor_user_id)
        .where(AuditEventModel.workspace_id == ctx.workspace_id)
        .order_by(AuditEventModel.created_at.desc())
        .limit(limit + 1)
    )
    if before is not None:
        query = query.where(AuditEventModel.created_at < before)
    if action:
        query = query.where(AuditEventModel.action == action)
    rows = (await session.execute(query)).all()
    items = [
        AuditEventRecord(id=e.id, action=e.action, entity_type=e.entity_type, entity_id=e.entity_id,
                         actor_name=name, metadata=e.metadata_json or {}, created_at=e.created_at)
        for e, name in rows[:limit]
    ]
    return AuditPage(items=items, next_before=items[-1].created_at if len(rows) > limit else None)
