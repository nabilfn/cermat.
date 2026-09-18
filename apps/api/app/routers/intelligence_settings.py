"""Operational thresholds used by the intelligence layer."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_session
from app.intelligence.config import load_settings, merge_settings, save_settings, settings_record
from app.schemas import IntelligenceSettingsRecord, IntelligenceSettingsUpdate

router = APIRouter(prefix="/api/v1/settings", tags=["settings"])


@router.get("/intelligence", response_model=IntelligenceSettingsRecord)
async def get_intelligence_settings(
    session: AsyncSession = Depends(get_session),
) -> IntelligenceSettingsRecord:
    settings, updated_at = await load_settings(session)
    return settings_record(settings, updated_at)


@router.patch("/intelligence", response_model=IntelligenceSettingsRecord)
async def update_intelligence_settings(
    payload: IntelligenceSettingsUpdate,
    session: AsyncSession = Depends(get_session),
) -> IntelligenceSettingsRecord:
    current, _ = await load_settings(session)
    try:
        merged = merge_settings(current, payload)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc).splitlines()[0][:300]) from exc
    return await save_settings(session, merged)
