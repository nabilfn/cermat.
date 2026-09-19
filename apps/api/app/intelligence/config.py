"""Operational thresholds, stored as one validated row in PostgreSQL."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import IntelligenceSettingsModel
from app.schemas import (
    IntelligenceSettings,
    IntelligenceSettingsRecord,
    IntelligenceSettingsUpdate,
    PriorityWeights,
)


def merge_settings(
    current: IntelligenceSettings, update: IntelligenceSettingsUpdate
) -> IntelligenceSettings:
    """Apply a partial update and re-validate the whole object."""
    if update.reset:
        return IntelligenceSettings()
    data = current.model_dump()
    for key, value in update.model_dump(exclude={"reset", "priority_weights"}, exclude_none=True).items():
        data[key] = value
    if update.priority_weights:
        weights = dict(data["priority_weights"])
        for key, value in update.priority_weights.items():
            if key == "issue_type" and isinstance(value, dict):
                weights["issue_type"] = {**weights["issue_type"], **value}
            elif key in PriorityWeights.model_fields and key != "issue_type":
                weights[key] = value
            else:
                raise ValueError(f"Unknown priority weight: {key}")
        data["priority_weights"] = weights
    merged = IntelligenceSettings.model_validate(data)
    if merged.high_score >= merged.critical_score:
        raise ValueError("high_score must be lower than critical_score.")
    return merged


async def load_settings(
    session: AsyncSession, workspace_id: UUID
) -> tuple[IntelligenceSettings, datetime | None]:
    """Read-only. A missing or invalid row falls back to defaults."""
    row = await session.get(IntelligenceSettingsModel, workspace_id)
    if row is None:
        return IntelligenceSettings(), None
    try:
        return IntelligenceSettings.model_validate(row.config), row.updated_at
    except ValueError:
        return IntelligenceSettings(), row.updated_at


def settings_record(settings: IntelligenceSettings, updated_at: datetime | None) -> IntelligenceSettingsRecord:
    return IntelligenceSettingsRecord(
        **settings.model_dump(),
        is_default=settings == IntelligenceSettings(),
        updated_at=updated_at,
    )


async def save_settings(
    session: AsyncSession, workspace_id: UUID, settings: IntelligenceSettings
) -> IntelligenceSettingsRecord:
    row = await session.get(IntelligenceSettingsModel, workspace_id)
    if row is None:
        row = IntelligenceSettingsModel(workspace_id=workspace_id, config=settings.model_dump())
        session.add(row)
    else:
        row.config = settings.model_dump()
    await session.commit()
    await session.refresh(row)
    return settings_record(settings, row.updated_at)
