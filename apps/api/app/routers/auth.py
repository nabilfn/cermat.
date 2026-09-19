"""Sign up, sign in, sign out and session info.

Sessions are opaque random tokens in an httpOnly, SameSite=Lax cookie (Secure in
production). Only an HMAC of the token is stored. Unsafe requests must also send
the per-session CSRF token (``X-CSRF-Token``) returned by these endpoints.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import SESSION_COOKIE, AuthContext, authenticate, current_user, unauthorized
from app.config import settings
from app.core.errors import ApiError
from app.core.ratelimit import limiter
from app.core.security import (
    EMAIL_RE,
    csrf_for,
    digest,
    hash_password,
    needs_rehash,
    new_token,
    normalise_email,
    password_problems,
    verify_password,
)
from app.database import get_session
from app.models import SessionModel, UserModel, WorkspaceMemberModel, WorkspaceModel
from app.schemas import SessionInfo, SignInRequest, SignUpRequest, UserRecord, WorkspaceSummary
from app.services import audit

logger = logging.getLogger("cermat.auth")
router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


async def workspace_summaries(session: AsyncSession, user_id) -> list[WorkspaceSummary]:
    rows = await session.execute(
        select(WorkspaceModel, WorkspaceMemberModel.role)
        .join(WorkspaceMemberModel, WorkspaceMemberModel.workspace_id == WorkspaceModel.id)
        .where(WorkspaceMemberModel.user_id == user_id)
        .order_by(WorkspaceModel.is_demo.asc(), WorkspaceMemberModel.created_at.asc())
    )
    return [
        WorkspaceSummary(id=w.id, name=w.name, role=role, is_demo=w.is_demo, is_legacy=w.is_legacy)
        for w, role in rows.all()
    ]


async def session_info(session: AsyncSession, user: UserModel, token: str) -> SessionInfo:
    return SessionInfo(
        user=UserRecord(id=user.id, email=user.email, display_name=user.display_name),
        workspaces=await workspace_summaries(session, user.id),
        csrf_token=csrf_for(token),
    )


async def start_session(session: AsyncSession, response: Response, user: UserModel) -> str:
    token = new_token()
    now = datetime.now(timezone.utc)
    session.add(
        SessionModel(
            token_hash=digest(token),
            csrf_hash=digest(csrf_for(token), "csrf"),
            user_id=user.id,
            created_at=now,
            last_seen_at=now,
            expires_at=now + timedelta(hours=settings.session_ttl_hours),
        )
    )
    user.last_login_at = now
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=settings.session_ttl_hours * 3600,
        httponly=True,
        secure=settings.secure_cookies,
        samesite="lax",
        path="/",
    )
    return token


def client_key(request: Request) -> str:
    return request.client.host if request.client else "unknown"


@router.post("/signup", response_model=SessionInfo, summary="Create an account and a workspace")
async def sign_up(
    payload: SignUpRequest,
    request: Request,
    response: Response,
    session: AsyncSession = Depends(get_session),
) -> SessionInfo:
    limiter.check(f"signup:{client_key(request)}", limit=10, window_seconds=3600)
    email = normalise_email(payload.email)
    if not EMAIL_RE.match(email):
        raise ApiError(422, "VALIDATION_ERROR", "Enter a valid email address.", details=[{"field": "email", "message": "Invalid email."}])
    problems = password_problems(payload.password, email)
    if problems:
        raise ApiError(422, "VALIDATION_ERROR", problems[0], details=[{"field": "password", "message": p} for p in problems])
    if await session.scalar(select(UserModel.id).where(UserModel.email == email)):
        raise ApiError(409, "CONFLICT", "An account with this email already exists. Sign in instead.")

    user = UserModel(email=email, display_name=payload.display_name.strip()[:80], password_hash=hash_password(payload.password))
    session.add(user)
    await session.flush()

    workspace = WorkspaceModel(
        name=(payload.workspace_name or f"{user.display_name}'s workspace").strip()[:120],
        created_by=user.id,
    )
    session.add(workspace)
    await session.flush()
    session.add(WorkspaceMemberModel(workspace_id=workspace.id, user_id=user.id, role="owner"))
    audit.record(
        session, workspace_id=workspace.id, actor_user_id=user.id,
        action="workspace_created", entity_type="workspace", entity_id=workspace.id,
    )

    # Local upgrades: data created before accounts existed sits in an unowned legacy
    # workspace. In development the first account adopts it; in production use
    # `python -m scripts.claim_legacy_workspace <email>`.
    if settings.legacy_claim_enabled:
        legacy = (
            await session.execute(
                select(WorkspaceModel)
                .where(WorkspaceModel.is_legacy.is_(True))
                .where(~WorkspaceModel.id.in_(select(WorkspaceMemberModel.workspace_id)))
            )
        ).scalars().first()
        if legacy is not None:
            session.add(WorkspaceMemberModel(workspace_id=legacy.id, user_id=user.id, role="owner"))
            logger.info("legacy_workspace_claimed", extra={"event": "legacy_workspace_claimed", "workspace": str(legacy.id)})

    token = await start_session(session, response, user)
    await session.commit()
    return await session_info(session, user, token)


@router.post("/signin", response_model=SessionInfo, summary="Sign in")
async def sign_in(
    payload: SignInRequest,
    request: Request,
    response: Response,
    session: AsyncSession = Depends(get_session),
) -> SessionInfo:
    email = normalise_email(payload.email)
    limiter.check(f"signin:{client_key(request)}:{email}", limit=10, window_seconds=300)
    user = (await session.execute(select(UserModel).where(UserModel.email == email))).scalar_one_or_none()
    # verify_password runs against a dummy hash when the user does not exist.
    if not verify_password(user.password_hash if user else None, payload.password) or user is None:
        raise ApiError(401, "UNAUTHORIZED", "Email or password is incorrect.")
    if needs_rehash(user.password_hash):
        user.password_hash = hash_password(payload.password)
    token = await start_session(session, response, user)
    await session.commit()
    return await session_info(session, user, token)


@router.post("/signout", status_code=204, summary="Sign out and end this session")
async def sign_out(
    response: Response,
    auth: AuthContext = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> Response:
    await session.delete(await session.get(SessionModel, auth.session.id))
    await session.commit()
    response.delete_cookie(SESSION_COOKIE, path="/", samesite="lax", secure=settings.secure_cookies, httponly=True)
    response.status_code = 204
    return response


@router.get("/me", response_model=SessionInfo, summary="Current user, workspaces and CSRF token")
async def me(request: Request, session: AsyncSession = Depends(get_session)) -> SessionInfo:
    auth = await authenticate(request, session)
    if auth is None:
        raise unauthorized()
    return await session_info(session, auth.user, auth.token)
