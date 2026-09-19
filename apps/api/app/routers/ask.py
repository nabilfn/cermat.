"""Ask cermat. endpoints. Read-only: no route here writes business data."""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import WorkspaceContext, workspace_context
from app.config import settings
from app.core.context import request_id_var
from app.core.errors import ApiError, not_found
from app.core.ratelimit import limiter
from app.database import SessionLocal, get_session
from app.schemas import AskRequest, AskResponse, AskSuggestions, TransactionContext
from app.services.ask import AskError, ask, build_suggestions, run_ask
from app.services.ask_context import transaction_context
from app.services.ask_llm import AskModel, default_model
from app.services.ask_queries import load_snapshot

logger = logging.getLogger("cermat.ask")
router = APIRouter(prefix="/api/v1", tags=["ask"])

GENERIC_FAILURE = "Ask cermat. could not complete this request. Try again shortly."


def get_ask_model() -> AskModel | None:
    return default_model()


@router.post("/ask", response_model=AskResponse, summary="Ask a question about this workspace")
async def ask_question(
    payload: AskRequest,
    ctx: WorkspaceContext = Depends(workspace_context),
    session: AsyncSession = Depends(get_session),
    model: AskModel | None = Depends(get_ask_model),
) -> AskResponse:
    limiter.check(f"ai:{ctx.user.id}", limit=settings.rate_limit_ai_per_minute)
    try:
        return await ask(session, payload, model, ctx.workspace_id)
    except AskError as exc:
        raise ApiError(exc.status_code, "NOT_FOUND" if exc.status_code == 404 else "VALIDATION_ERROR", exc.detail) from exc


@router.post("/ask/stream", summary="Ask, streaming progress stages as NDJSON")
async def ask_question_stream(
    payload: AskRequest,
    ctx: WorkspaceContext = Depends(workspace_context),
    model: AskModel | None = Depends(get_ask_model),
) -> StreamingResponse:
    """Events: ``{"stage": "interpreting" | "searching" | "composing"}``, then
    ``{"stage": "done", "response": AskResponse}`` or ``{"stage": "error", "error": {...}}``."""
    limiter.check(f"ai:{ctx.user.id}", limit=settings.rate_limit_ai_per_minute)
    workspace_id = ctx.workspace_id
    request_id = request_id_var.get()

    def error_event(code: str, message: str) -> str:
        return json.dumps({"stage": "error", "error": {"code": code, "message": message, "request_id": request_id}}) + "\n"

    async def events() -> AsyncIterator[str]:
        async with SessionLocal() as session:
            try:
                async for event in run_ask(session, payload, model, workspace_id):
                    if event["stage"] == "done":
                        yield json.dumps({"stage": "done", "response": event["response"].model_dump(mode="json")}) + "\n"
                    else:
                        yield json.dumps(event) + "\n"
            except AskError as exc:
                yield error_event("NOT_FOUND" if exc.status_code == 404 else "VALIDATION_ERROR", exc.detail)
            except Exception:  # noqa: BLE001
                logger.exception("ask_stream_failed", extra={"event": "ask_stream_failed", "request_id": request_id})
                yield error_event("INTERNAL_ERROR", GENERIC_FAILURE)

    return StreamingResponse(
        events(),
        media_type="application/x-ndjson",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
    )


@router.get("/ask/suggestions", response_model=AskSuggestions, summary="Suggested questions")
async def ask_suggestions(
    transaction_id: UUID | None = Query(default=None),
    ctx: WorkspaceContext = Depends(workspace_context),
    session: AsyncSession = Depends(get_session),
) -> AskSuggestions:
    snapshot = await load_snapshot(session, ctx.workspace_id, transaction_id)
    scope = snapshot.transaction(transaction_id)
    if transaction_id is not None and scope is None:
        raise not_found("Transaction")
    return build_suggestions(snapshot, scope)


@router.get(
    "/transactions/{transaction_id}/context",
    response_model=TransactionContext,
    summary="Grounded record set for one transaction, with evidence",
)
async def get_transaction_context(
    transaction_id: UUID,
    ctx: WorkspaceContext = Depends(workspace_context),
    session: AsyncSession = Depends(get_session),
) -> TransactionContext:
    snapshot = await load_snapshot(session, ctx.workspace_id, transaction_id)
    txn = snapshot.transaction(transaction_id)
    if txn is None:
        raise not_found("Transaction")
    return transaction_context(txn)
