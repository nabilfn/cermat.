"""Ask cermat. endpoints. Read-only: no route here writes to the database."""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

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


@router.post("/ask", response_model=AskResponse)
async def ask_question(
    payload: AskRequest,
    session: AsyncSession = Depends(get_session),
    model: AskModel | None = Depends(get_ask_model),
) -> AskResponse:
    try:
        return await ask(session, payload, model)
    except AskError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
    except Exception as exc:  # noqa: BLE001 — never leak internals
        logger.exception("Ask cermat. request failed.")
        raise HTTPException(status_code=500, detail=GENERIC_FAILURE) from exc


@router.post("/ask/stream")
async def ask_question_stream(
    payload: AskRequest,
    model: AskModel | None = Depends(get_ask_model),
) -> StreamingResponse:
    """Same as ``POST /ask`` but emits NDJSON progress stages before the result.

    Events: ``{"stage": "interpreting" | "searching" | "composing"}``, then
    ``{"stage": "done", "response": AskResponse}`` or ``{"stage": "error", "detail": str}``.
    """

    async def events() -> AsyncIterator[str]:
        async with SessionLocal() as session:
            try:
                async for event in run_ask(session, payload, model):
                    if event["stage"] == "done":
                        body = {
                            "stage": "done",
                            "response": event["response"].model_dump(mode="json"),
                        }
                    else:
                        body = event
                    yield json.dumps(body) + "\n"
            except AskError as exc:
                yield json.dumps({"stage": "error", "status": exc.status_code, "detail": exc.detail}) + "\n"
            except Exception:  # noqa: BLE001
                logger.exception("Ask cermat. stream failed.")
                yield json.dumps({"stage": "error", "status": 500, "detail": GENERIC_FAILURE}) + "\n"

    return StreamingResponse(
        events(),
        media_type="application/x-ndjson",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
    )


@router.get("/ask/suggestions", response_model=AskSuggestions)
async def ask_suggestions(
    transaction_id: UUID | None = Query(default=None),
    session: AsyncSession = Depends(get_session),
) -> AskSuggestions:
    snapshot = await load_snapshot(session, transaction_id)
    scope = snapshot.transaction(transaction_id)
    if transaction_id is not None and scope is None:
        raise HTTPException(status_code=404, detail="Transaction not found.")
    return build_suggestions(snapshot, scope)


@router.get(
    "/transactions/{transaction_id}/context",
    response_model=TransactionContext,
)
async def get_transaction_context(
    transaction_id: UUID,
    session: AsyncSession = Depends(get_session),
) -> TransactionContext:
    snapshot = await load_snapshot(session, transaction_id)
    txn = snapshot.transaction(transaction_id)
    if txn is None:
        raise HTTPException(status_code=404, detail="Transaction not found.")
    return transaction_context(txn)
