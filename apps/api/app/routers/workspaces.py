"""Workspaces, members and the demo workspace."""

from __future__ import annotations

import logging
from uuid import UUID

from fastapi import APIRouter, Depends, Response
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import AuthContext, current_user, membership
from app.core.errors import ApiError, not_found
from app.core.security import normalise_email
from app.database import get_session
from app.models import DocumentModel, UserModel, WorkspaceMemberModel, WorkspaceModel
from app.routers.auth import workspace_summaries
from app.schemas import (
    MemberInvite,
    MemberRecord,
    WorkspaceCreate,
    WorkspaceDelete,
    WorkspaceSummary,
    WorkspaceUpdate,
)
from app.services import audit
from app.services.demo import create_demo_workspace, reset_demo_workspace
from app.services.storage import get_storage

logger = logging.getLogger("cermat.workspaces")
router = APIRouter(prefix="/api/v1/workspaces", tags=["workspaces"])


async def _member_workspace(
    session: AsyncSession, auth: AuthContext, workspace_id: UUID, *, owner: bool = False
) -> tuple[WorkspaceModel, str]:
    found = await membership(session, auth.user.id, workspace_id)
    if found is None:
        raise not_found("Workspace")
    if owner and found[1] != "owner":
        raise ApiError(403, "FORBIDDEN", "Only a workspace owner can do this.")
    return found


def _summary(workspace: WorkspaceModel, role: str) -> WorkspaceSummary:
    return WorkspaceSummary(
        id=workspace.id, name=workspace.name, role=role, is_demo=workspace.is_demo, is_legacy=workspace.is_legacy
    )


@router.get("", response_model=list[WorkspaceSummary], summary="Workspaces you belong to")
async def list_workspaces(
    auth: AuthContext = Depends(current_user), session: AsyncSession = Depends(get_session)
) -> list[WorkspaceSummary]:
    return await workspace_summaries(session, auth.user.id)


@router.post("", response_model=WorkspaceSummary, status_code=201, summary="Create a workspace")
async def create_workspace(
    payload: WorkspaceCreate,
    auth: AuthContext = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> WorkspaceSummary:
    owned = await session.scalar(
        select(func.count()).select_from(WorkspaceMemberModel).where(
            WorkspaceMemberModel.user_id == auth.user.id, WorkspaceMemberModel.role == "owner"
        )
    )
    if (owned or 0) >= 20:
        raise ApiError(409, "CONFLICT", "You already own the maximum of 20 workspaces.")
    workspace = WorkspaceModel(name=payload.name.strip(), created_by=auth.user.id)
    session.add(workspace)
    await session.flush()
    session.add(WorkspaceMemberModel(workspace_id=workspace.id, user_id=auth.user.id, role="owner"))
    audit.record(session, workspace_id=workspace.id, actor_user_id=auth.user.id,
                 action="workspace_created", entity_type="workspace", entity_id=workspace.id)
    await session.commit()
    return _summary(workspace, "owner")


@router.patch("/{workspace_id}", response_model=WorkspaceSummary, summary="Rename a workspace (owner)")
async def rename_workspace(
    workspace_id: UUID,
    payload: WorkspaceUpdate,
    auth: AuthContext = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> WorkspaceSummary:
    workspace, role = await _member_workspace(session, auth, workspace_id, owner=True)
    workspace.name = payload.name.strip()
    audit.record(session, workspace_id=workspace.id, actor_user_id=auth.user.id,
                 action="workspace_renamed", entity_type="workspace", entity_id=workspace.id)
    await session.commit()
    return _summary(workspace, role)


@router.delete("/{workspace_id}", status_code=204, summary="Delete a workspace and all of its data (owner)")
async def delete_workspace(
    workspace_id: UUID,
    payload: WorkspaceDelete,
    auth: AuthContext = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> Response:
    workspace, _ = await _member_workspace(session, auth, workspace_id, owner=True)
    if payload.confirm_name.strip() != workspace.name:
        raise ApiError(422, "VALIDATION_ERROR", "Type the workspace name exactly to confirm deletion.")
    keys = (
        await session.execute(
            select(DocumentModel.storage_key).where(
                DocumentModel.workspace_id == workspace.id, DocumentModel.storage_key.is_not(None)
            )
        )
    ).scalars().all()
    await session.delete(workspace)  # cascades: documents, transactions, issues, events, audit, settings
    await session.commit()
    storage = get_storage()
    for key in keys:
        try:
            await storage.delete(key)
        except Exception:  # noqa: BLE001 — rows are gone; log orphaned objects for cleanup
            logger.warning("storage_delete_failed", extra={"event": "storage_delete_failed", "key": key})
    logger.info("workspace_deleted", extra={"event": "workspace_deleted", "deleted_workspace": str(workspace_id), "files": len(keys)})
    return Response(status_code=204)


# ---------------------------------------------------------------------------
# Members
# ---------------------------------------------------------------------------


@router.get("/{workspace_id}/members", response_model=list[MemberRecord], summary="Workspace members")
async def list_members(
    workspace_id: UUID, auth: AuthContext = Depends(current_user), session: AsyncSession = Depends(get_session)
) -> list[MemberRecord]:
    await _member_workspace(session, auth, workspace_id)
    rows = await session.execute(
        select(WorkspaceMemberModel, UserModel)
        .join(UserModel, UserModel.id == WorkspaceMemberModel.user_id)
        .where(WorkspaceMemberModel.workspace_id == workspace_id)
        .order_by(WorkspaceMemberModel.created_at)
    )
    return [
        MemberRecord(user_id=u.id, email=u.email, display_name=u.display_name, role=m.role, joined_at=m.created_at)
        for m, u in rows.all()
    ]


@router.post("/{workspace_id}/members", response_model=MemberRecord, status_code=201, summary="Add a member (owner)")
async def add_member(
    workspace_id: UUID,
    payload: MemberInvite,
    auth: AuthContext = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> MemberRecord:
    workspace, _ = await _member_workspace(session, auth, workspace_id, owner=True)
    if workspace.is_demo:
        raise ApiError(409, "CONFLICT", "Demo workspaces are personal and cannot be shared.")
    user = (
        await session.execute(select(UserModel).where(UserModel.email == normalise_email(payload.email)))
    ).scalar_one_or_none()
    if user is None:
        raise ApiError(404, "NOT_FOUND", "No cermat. account uses that email. Ask them to sign up first.")
    if await membership(session, user.id, workspace_id):
        raise ApiError(409, "CONFLICT", "That person is already a member.")
    member = WorkspaceMemberModel(workspace_id=workspace_id, user_id=user.id, role=payload.role)
    session.add(member)
    await session.flush()
    audit.record(session, workspace_id=workspace_id, actor_user_id=auth.user.id, action="member_invited",
                 entity_type="member", entity_id=user.id, metadata={"role": payload.role})
    await session.commit()
    return MemberRecord(user_id=user.id, email=user.email, display_name=user.display_name,
                        role=member.role, joined_at=member.created_at)


@router.delete("/{workspace_id}/members/{user_id}", status_code=204, summary="Remove a member (owner)")
async def remove_member(
    workspace_id: UUID,
    user_id: UUID,
    auth: AuthContext = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> Response:
    await _member_workspace(session, auth, workspace_id, owner=True)
    member = (
        await session.execute(
            select(WorkspaceMemberModel).where(
                WorkspaceMemberModel.workspace_id == workspace_id, WorkspaceMemberModel.user_id == user_id
            )
        )
    ).scalar_one_or_none()
    if member is None:
        raise not_found("Member")
    if member.role == "owner":
        owners = await session.scalar(
            select(func.count()).select_from(WorkspaceMemberModel).where(
                WorkspaceMemberModel.workspace_id == workspace_id, WorkspaceMemberModel.role == "owner"
            )
        )
        if (owners or 0) <= 1:
            raise ApiError(409, "CONFLICT", "A workspace needs at least one owner.")
    await session.delete(member)
    audit.record(session, workspace_id=workspace_id, actor_user_id=auth.user.id, action="member_removed",
                 entity_type="member", entity_id=user_id)
    await session.commit()
    return Response(status_code=204)


# ---------------------------------------------------------------------------
# Demo workspace
# ---------------------------------------------------------------------------


@router.post("/demo", response_model=WorkspaceSummary, summary="Open (creating if needed) your DEMO workspace")
async def open_demo_workspace(
    auth: AuthContext = Depends(current_user), session: AsyncSession = Depends(get_session)
) -> WorkspaceSummary:
    workspace = await create_demo_workspace(session, auth.user.id)
    await session.commit()
    return _summary(workspace, "owner")


@router.post("/{workspace_id}/demo/reset", response_model=WorkspaceSummary, summary="Reset a DEMO workspace (owner)")
async def reset_demo(
    workspace_id: UUID, auth: AuthContext = Depends(current_user), session: AsyncSession = Depends(get_session)
) -> WorkspaceSummary:
    workspace, role = await _member_workspace(session, auth, workspace_id, owner=True)
    await reset_demo_workspace(session, workspace, auth.user.id)
    await session.commit()
    return _summary(workspace, role)
