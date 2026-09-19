"""cermat. API — application assembly only. Endpoints live in app/routers/."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import update

from app.config import settings
from app.core.errors import error_body, install_error_handlers
from app.core.logging import configure_logging
from app.core.middleware import RequestContextMiddleware
from app.database import SessionLocal, database_ready
from app.models import DocumentModel
from app.routers import (
    ask,
    attention,
    audit,
    auth,
    documents,
    exports,
    intelligence,
    intelligence_settings,
    transactions,
    workspaces,
)

VERSION = "1.0.0"
logger = logging.getLogger("cermat.app")
configure_logging(settings.log_level)


async def recover_interrupted_extractions() -> None:
    """A restart mid-extraction must not leave documents 'processing' forever."""
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=15)
    try:
        async with SessionLocal() as session:
            result = await session.execute(
                update(DocumentModel)
                .where(DocumentModel.status == "processing", DocumentModel.updated_at < cutoff)
                .values(status="failed", error_code="EXTRACTION_FAILED")
            )
            await session.commit()
            if result.rowcount:
                logger.warning("recovered_interrupted_extractions", extra={"event": "recovered", "count": result.rowcount})
    except Exception:  # noqa: BLE001 — never block startup (e.g. before migrations ran)
        logger.warning("recovery_skipped", extra={"event": "recovery_skipped"})


@asynccontextmanager
async def lifespan(_: FastAPI):
    logger.info("startup", extra={"event": "startup", "env": settings.app_env, "version": VERSION,
                                  "storage": settings.storage_provider, "ai_configured": bool(settings.openai_api_key)})
    await recover_interrupted_extractions()
    yield


app = FastAPI(
    title="cermat. API",
    version=VERSION,
    summary="AI reads. Rules verify. Evidence proves. cermat. explains. Humans decide.",
    description=(
        "Evidence-backed document extraction, deterministic three-way reconciliation, "
        "a persistent review workflow, grounded Ask cermat. answers and operations intelligence. "
        "All business endpoints require a session cookie; unsafe methods also require the "
        "`X-CSRF-Token` header. `X-Workspace-Id` selects a workspace you belong to."
    ),
    lifespan=lifespan,
    docs_url=None if settings.is_production else "/docs",
    redoc_url=None,
    openapi_url=None if settings.is_production else "/openapi.json",
)

install_error_handlers(app)
app.add_middleware(RequestContextMiddleware)
if settings.cors_origin_list:
    # Only needed when the browser calls the API from another origin. The default
    # deployment proxies /api/* through the Next.js origin, so no CORS is required.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PATCH", "DELETE"],
        allow_headers=["Content-Type", "X-CSRF-Token", "X-Workspace-Id", "X-Request-ID"],
        expose_headers=["X-Request-ID"],
    )

for module in (auth, workspaces, documents, transactions, ask, intelligence, attention, intelligence_settings, exports, audit):
    app.include_router(module.router)


@app.get("/health", tags=["operations"], summary="Liveness: the process is up")
async def health() -> dict[str, str | bool]:
    return {"status": "ok", "service": "cermat-api", "version": VERSION}


@app.get("/ready", tags=["operations"], summary="Readiness: PostgreSQL reachable and migrated", response_model=None)
async def ready() -> JSONResponse:
    if await database_ready():
        return JSONResponse({"status": "ready", "database": "ok"})
    return JSONResponse(
        {**error_body("INTERNAL_ERROR", "Database unavailable or not migrated."), "status": "not_ready"},
        status_code=503,
    )
