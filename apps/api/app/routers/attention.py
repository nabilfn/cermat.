"""Attention queue. GET syncs events from current conditions (idempotent per workspace)."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import WorkspaceContext, workspace_context
from app.core.errors import not_found
from app.database import get_session
from app.intelligence.anomalies import detect_anomalies
from app.intelligence.attention import attention_candidates, attention_record, list_attention, sync_attention
from app.intelligence.config import load_settings
from app.intelligence.dataset import load_dataset, utcnow
from app.intelligence.patterns import detect_patterns
from app.models import AttentionEventModel
from app.schemas import AttentionEventRecord, AttentionList, AttentionUpdate
from app.services import audit

router = APIRouter(prefix="/api/v1/attention", tags=["attention"])


@router.get("", response_model=AttentionList, summary="Attention queue for the current workspace")
async def get_attention(
    include_inactive: bool = Query(default=False),
    limit: int = Query(default=50, ge=1, le=200),
    ctx: WorkspaceContext = Depends(workspace_context),
    session: AsyncSession = Depends(get_session),
) -> AttentionList:
    dataset = await load_dataset(session, ctx.workspace_id)
    settings, _ = await load_settings(session, ctx.workspace_id)
    now = utcnow()
    candidates = attention_candidates(
        dataset, settings, now, detect_patterns(dataset, settings, now), detect_anomalies(dataset, settings, now)
    )
    await sync_attention(session, ctx.workspace_id, candidates, now)
    return await list_attention(session, ctx.workspace_id, include_inactive=include_inactive, limit=limit)


@router.patch("/{event_id}", response_model=AttentionEventRecord, summary="Mark seen or dismiss an event")
async def update_attention(
    event_id: UUID,
    payload: AttentionUpdate,
    ctx: WorkspaceContext = Depends(workspace_context),
    session: AsyncSession = Depends(get_session),
) -> AttentionEventRecord:
    event = await session.get(AttentionEventModel, event_id)
    if event is None or event.workspace_id != ctx.workspace_id:
        raise not_found("Attention event")
    now = datetime.now(timezone.utc)
    if payload.seen is not None:
        event.seen_at = (event.seen_at or now) if payload.seen else None
    if payload.dismissed is not None:
        newly_dismissed = payload.dismissed and event.dismissed_at is None
        event.dismissed_at = (event.dismissed_at or now) if payload.dismissed else None
        if payload.dismissed and event.seen_at is None:
            event.seen_at = now
        if newly_dismissed:
            audit.record(session, workspace_id=ctx.workspace_id, actor_user_id=ctx.user.id,
                         action="attention_dismissed", entity_type="attention_event", entity_id=event.id,
                         metadata={"event_type": event.event_type})
    await session.commit()
    await session.refresh(event)
    return attention_record(event)
