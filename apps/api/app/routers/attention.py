"""Attention queue. GET syncs events from current conditions (idempotent)."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_session
from app.intelligence.anomalies import detect_anomalies
from app.intelligence.attention import (
    attention_candidates,
    attention_record,
    list_attention,
    sync_attention,
)
from app.intelligence.config import load_settings
from app.intelligence.dataset import load_dataset, utcnow
from app.intelligence.patterns import detect_patterns
from app.models import AttentionEventModel
from app.schemas import AttentionEventRecord, AttentionList, AttentionUpdate

router = APIRouter(prefix="/api/v1/attention", tags=["attention"])


@router.get("", response_model=AttentionList)
async def get_attention(
    include_inactive: bool = Query(default=False),
    session: AsyncSession = Depends(get_session),
) -> AttentionList:
    dataset = await load_dataset(session)
    settings, _ = await load_settings(session)
    now = utcnow()
    candidates = attention_candidates(
        dataset,
        settings,
        now,
        detect_patterns(dataset, settings, now),
        detect_anomalies(dataset, settings, now),
    )
    await sync_attention(session, candidates, now)
    return await list_attention(session, include_inactive=include_inactive)


@router.patch("/{event_id}", response_model=AttentionEventRecord)
async def update_attention(
    event_id: UUID,
    payload: AttentionUpdate,
    session: AsyncSession = Depends(get_session),
) -> AttentionEventRecord:
    event = await session.get(AttentionEventModel, event_id)
    if event is None:
        raise HTTPException(status_code=404, detail="Attention event not found.")
    now = datetime.now(timezone.utc)
    if payload.seen is not None:
        event.seen_at = (event.seen_at or now) if payload.seen else None
    if payload.dismissed is not None:
        event.dismissed_at = (event.dismissed_at or now) if payload.dismissed else None
        if payload.dismissed and event.seen_at is None:
            event.seen_at = now
    await session.commit()
    await session.refresh(event)
    return attention_record(event)
