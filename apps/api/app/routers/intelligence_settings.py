"""Operational thresholds used by the intelligence layer (per workspace)."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import WorkspaceContext, owner_context, workspace_context
from app.core.errors import ApiError
from app.database import get_session
from app.intelligence.config import load_settings, merge_settings, save_settings, settings_record
from app.schemas import IntelligenceSettingsRecord, IntelligenceSettingsUpdate
from app.services import audit

router = APIRouter(prefix="/api/v1/settings", tags=["settings"])


@router.get("/intelligence", response_model=IntelligenceSettingsRecord, summary="Current thresholds")
async def get_intelligence_settings(
    ctx: WorkspaceContext = Depends(workspace_context),
    session: AsyncSession = Depends(get_session),
) -> IntelligenceSettingsRecord:
    settings, updated_at = await load_settings(session, ctx.workspace_id)
    return settings_record(settings, updated_at)


@router.patch("/intelligence", response_model=IntelligenceSettingsRecord, summary="Change thresholds (owner)")
async def update_intelligence_settings(
    payload: IntelligenceSettingsUpdate,
    ctx: WorkspaceContext = Depends(owner_context),
    session: AsyncSession = Depends(get_session),
) -> IntelligenceSettingsRecord:
    current, _ = await load_settings(session, ctx.workspace_id)
    try:
        merged = merge_settings(current, payload)
    except ValueError as exc:
        raise ApiError(422, "VALIDATION_ERROR", str(exc).splitlines()[0][:300]) from exc
    before, after = current.model_dump(), merged.model_dump()
    changed = sorted(key for key in after if before.get(key) != after.get(key))
    audit.record(session, workspace_id=ctx.workspace_id, actor_user_id=ctx.user.id, action="settings_changed",
                 entity_type="intelligence_settings", entity_id=ctx.workspace_id,
                 metadata={"changed": changed, "reset": payload.reset})
    return await save_settings(session, ctx.workspace_id, merged)
