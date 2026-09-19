"""Authentication, CSRF and workspace authorization dependencies.

Every business endpoint depends on ``workspace_context``: it authenticates the
session cookie, checks the CSRF token on unsafe methods, and verifies the user
is a member of the requested workspace. A workspace id from the client
(``X-Workspace-Id``) is only ever used after that membership check.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from uuid import UUID

from fastapi import Depends, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.context import user_id_var, workspace_id_var
from app.core.errors import ApiError, not_found
from app.core.security import constant_time_equals, digest
from app.database import get_session
from app.models import SessionModel, UserModel, WorkspaceMemberModel, WorkspaceModel

SESSION_COOKIE = "cermat_session"
CSRF_HEADER = "x-csrf-token"
WORKSPACE_HEADER = "x-workspace-id"
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
TOUCH_INTERVAL = timedelta(minutes=10)


@dataclass
class AuthContext:
    user: UserModel
    session: SessionModel
    token: str


@dataclass
class WorkspaceContext:
    user: UserModel
    workspace: WorkspaceModel
    role: str

    @property
    def workspace_id(self) -> UUID:
        return self.workspace.id

    @property
    def is_owner(self) -> bool:
        return self.role == "owner"


def unauthorized() -> ApiError:
    return ApiError(401, "UNAUTHORIZED", "Sign in to continue.")


async def authenticate(request: Request, session: AsyncSession) -> AuthContext | None:
    token = request.cookies.get(SESSION_COOKIE)
    if not token or len(token) > 200:
        return None
    now = datetime.now(timezone.utc)
    row = (
        await session.execute(
            select(SessionModel, UserModel)
            .join(UserModel, UserModel.id == SessionModel.user_id)
            .where(SessionModel.token_hash == digest(token), SessionModel.expires_at > now)
        )
    ).first()
    if row is None:
        return None
    auth_session, user = row
    if now - auth_session.last_seen_at > TOUCH_INTERVAL:
        auth_session.last_seen_at = now
        await session.commit()
    return AuthContext(user=user, session=auth_session, token=token)


async def current_user(
    request: Request, session: AsyncSession = Depends(get_session)
) -> AuthContext:
    auth = await authenticate(request, session)
    if auth is None:
        raise unauthorized()
    if request.method not in SAFE_METHODS:
        supplied = request.headers.get(CSRF_HEADER, "")
        if not supplied or not constant_time_equals(digest(supplied, "csrf"), auth.session.csrf_hash):
            raise ApiError(403, "FORBIDDEN", "Security token missing or expired. Refresh the page and try again.")
    user_id_var.set(str(auth.user.id))
    return auth


async def membership(
    session: AsyncSession, user_id: UUID, workspace_id: UUID
) -> tuple[WorkspaceModel, str] | None:
    row = (
        await session.execute(
            select(WorkspaceModel, WorkspaceMemberModel.role)
            .join(WorkspaceMemberModel, WorkspaceMemberModel.workspace_id == WorkspaceModel.id)
            .where(WorkspaceModel.id == workspace_id, WorkspaceMemberModel.user_id == user_id)
        )
    ).first()
    return (row[0], row[1]) if row else None


async def default_workspace_id(session: AsyncSession, user_id: UUID) -> UUID | None:
    return (
        await session.execute(
            select(WorkspaceModel.id)
            .join(WorkspaceMemberModel, WorkspaceMemberModel.workspace_id == WorkspaceModel.id)
            .where(WorkspaceMemberModel.user_id == user_id)
            .order_by(WorkspaceModel.is_demo.asc(), WorkspaceMemberModel.created_at.asc())
            .limit(1)
        )
    ).scalar_one_or_none()


async def workspace_context(
    request: Request,
    auth: AuthContext = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> WorkspaceContext:
    # Header for API calls; query parameter for plain links (file view, CSV export).
    # Either way it is only a *request* — membership is verified below.
    raw = request.headers.get(WORKSPACE_HEADER) or request.query_params.get("workspace_id")
    if raw:
        try:
            workspace_id = UUID(raw)
        except ValueError as exc:
            raise not_found("Workspace") from exc
    else:
        workspace_id = await default_workspace_id(session, auth.user.id)
        if workspace_id is None:
            raise not_found("Workspace")
    found = await membership(session, auth.user.id, workspace_id)
    if found is None:
        raise not_found("Workspace")
    workspace, role = found
    workspace_id_var.set(str(workspace.id))
    return WorkspaceContext(user=auth.user, workspace=workspace, role=role)


async def owner_context(ctx: WorkspaceContext = Depends(workspace_context)) -> WorkspaceContext:
    if not ctx.is_owner:
        raise ApiError(403, "FORBIDDEN", "Only a workspace owner can do this.")
    return ctx
