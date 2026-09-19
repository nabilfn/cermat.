"""Append-only audit trail. Callers commit; the API never updates or deletes rows.

Metadata must be small and non-sensitive: ids, counts, statuses, changed
setting names. Never passwords, tokens, headers or document contents.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.context import request_id_var
from app.models import AuditEventModel

ACTIONS = {
    "workspace_created",
    "workspace_renamed",
    "document_uploaded",
    "document_extracted",
    "document_extraction_failed",
    "document_deleted",
    "transaction_created",
    "document_attached",
    "reconciliation_run",
    "transaction_deleted",
    "issue_resolved",
    "issue_reopened",
    "settings_changed",
    "attention_dismissed",
    "member_invited",
    "member_removed",
    "demo_seeded",
    "demo_reset",
    "export_downloaded",
}
_FORBIDDEN_KEYS = {"password", "token", "authorization", "cookie", "secret", "api_key", "extraction_data"}


def record(
    session: AsyncSession,
    *,
    workspace_id: UUID,
    actor_user_id: UUID | None,
    action: str,
    entity_type: str,
    entity_id: UUID | str | None = None,
    metadata: dict[str, Any] | None = None,
) -> AuditEventModel:
    assert action in ACTIONS, action
    clean = {
        key: value
        for key, value in (metadata or {}).items()
        if key.lower() not in _FORBIDDEN_KEYS and not isinstance(value, (bytes, bytearray))
    }
    event = AuditEventModel(
        workspace_id=workspace_id,
        actor_user_id=actor_user_id,
        action=action,
        entity_type=entity_type,
        entity_id=str(entity_id) if entity_id is not None else None,
        metadata_json=clean,
        request_id=request_id_var.get(),
    )
    session.add(event)
    return event
