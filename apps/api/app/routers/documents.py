"""Document upload, extraction, source file access and deletion."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from urllib.parse import quote
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, Response, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import WorkspaceContext, workspace_context
from app.config import settings
from app.core.errors import ApiError
from app.core.ratelimit import limiter
from app.database import get_session
from app.models import DocumentModel, TransactionDocumentModel
from app.schemas import DocumentRecord, DocumentType, ExtractionResult
from app.services import audit
from app.services.extraction import ExtractionError
from app.services.extraction import extract_document as run_ai_extraction
from app.services.storage import StorageError, get_storage, new_object_key
from app.services.transactions import document_record, get_document
from app.services.uploads import validate_upload

logger = logging.getLogger("cermat.documents")
router = APIRouter(prefix="/api/v1/documents", tags=["documents"])

# A document stuck in "processing" longer than this (e.g. the API restarted
# mid-extraction) is treated as failed and can be retried.
STALE_PROCESSING = timedelta(seconds=settings.ai_timeout_seconds * (settings.ai_max_retries + 1) + 60)


def is_stale(document: DocumentModel) -> bool:
    return document.status == "processing" and datetime.now(timezone.utc) - document.updated_at > STALE_PROCESSING


@router.post("", response_model=DocumentRecord, status_code=201, summary="Upload a document")
async def upload_document(
    file: UploadFile = File(...),
    document_type: DocumentType = Form(...),
    ctx: WorkspaceContext = Depends(workspace_context),
    session: AsyncSession = Depends(get_session),
) -> DocumentRecord:
    data = await file.read(settings.max_upload_bytes + 1)
    upload = validate_upload(file.filename, data)
    key = new_object_key(ctx.workspace_id, upload.extension)
    await get_storage().store(key, data, upload.mime_type)

    document = DocumentModel(
        workspace_id=ctx.workspace_id,
        filename=upload.filename,
        document_type=document_type.value,
        mime_type=upload.mime_type,
        size_bytes=upload.size_bytes,
        page_count=upload.page_count,
        status="uploaded",
        storage_key=key,
        uploaded_by=ctx.user.id,
    )
    session.add(document)
    await session.flush()
    audit.record(
        session, workspace_id=ctx.workspace_id, actor_user_id=ctx.user.id, action="document_uploaded",
        entity_type="document", entity_id=document.id,
        metadata={"document_type": document_type.value, "size_bytes": upload.size_bytes, "pages": upload.page_count},
    )
    await session.commit()
    return document_record(document)


@router.get("/{document_id}", response_model=DocumentRecord, summary="Document metadata and status")
async def get_document_record(
    document_id: UUID,
    ctx: WorkspaceContext = Depends(workspace_context),
    session: AsyncSession = Depends(get_session),
) -> DocumentRecord:
    document = await get_document(session, ctx.workspace_id, document_id)
    if is_stale(document):
        document.status, document.error_code = "failed", "EXTRACTION_FAILED"
        await session.commit()
    return document_record(document)


@router.post("/{document_id}/extract", response_model=ExtractionResult, summary="Run AI extraction")
async def extract_document(
    document_id: UUID,
    ctx: WorkspaceContext = Depends(workspace_context),
    session: AsyncSession = Depends(get_session),
) -> ExtractionResult:
    limiter.check(f"ai:{ctx.user.id}", limit=settings.rate_limit_ai_per_minute)
    document = await get_document(session, ctx.workspace_id, document_id)
    if document.status == "processing" and not is_stale(document):
        raise ApiError(409, "CONFLICT", "This document is already being processed.")
    if document.storage_key is None:
        raise ApiError(409, "CONFLICT", "Demo documents have no source file to extract.")

    try:
        data = await get_storage().get(document.storage_key)
    except StorageError as exc:
        document.status, document.error_code = "failed", "EXTRACTION_FAILED"
        await session.commit()
        raise ApiError(410, "EXTRACTION_FAILED", "The stored source file is missing. Upload it again.") from exc

    document.status, document.error_code = "processing", None
    await session.commit()

    try:
        extracted = await run_in_threadpool(
            run_ai_extraction,
            data=data,
            mime_type=document.mime_type,
            filename=document.filename,
            document_type=DocumentType(document.document_type),
        )
    except ExtractionError as exc:
        # The source file is kept; the document can be retried from the UI.
        document.status, document.error_code = "failed", exc.code
        audit.record(session, workspace_id=ctx.workspace_id, actor_user_id=ctx.user.id,
                     action="document_extraction_failed", entity_type="document", entity_id=document.id,
                     metadata={"error_code": exc.code})
        await session.commit()
        logger.warning("extraction_failed", extra={"event": "extraction_failed", "error_code": exc.code, "document": str(document.id)})
        raise ApiError(502 if exc.code == "AI_PROVIDER_ERROR" else 422, exc.code, exc.message) from exc
    except Exception as exc:
        document.status, document.error_code = "failed", "EXTRACTION_FAILED"
        await session.commit()
        logger.exception("extraction_crashed", extra={"event": "extraction_crashed", "document": str(document.id)})
        raise ApiError(500, "EXTRACTION_FAILED", "The document could not be processed.") from exc

    document.extraction_data = extracted.model_dump(mode="json")
    document.extraction_model = settings.openai_model
    document.status = "needs_review" if extracted.review_reasons else "extracted"
    audit.record(session, workspace_id=ctx.workspace_id, actor_user_id=ctx.user.id, action="document_extracted",
                 entity_type="document", entity_id=document.id,
                 metadata={"status": document.status, "confidence": extracted.overall_confidence,
                           "line_items": len(extracted.line_items)})
    await session.commit()
    return ExtractionResult(
        document_id=document.id, filename=document.filename, model=settings.openai_model, **extracted.model_dump()
    )


@router.get("/{document_id}/file", summary="Open the original source file", response_model=None)
async def document_file(
    document_id: UUID,
    ctx: WorkspaceContext = Depends(workspace_context),
    session: AsyncSession = Depends(get_session),
) -> Response:
    document = await get_document(session, ctx.workspace_id, document_id)
    if document.storage_key is None:
        raise ApiError(404, "NOT_FOUND", "This is demo data; there is no source file.")
    storage = get_storage()
    ascii_name = document.filename.encode("ascii", "ignore").decode().replace('"', "") or "document"
    signed = await storage.get_signed_url(
        document.storage_key, filename=ascii_name, content_type=document.mime_type
    )
    if signed:
        return RedirectResponse(signed, status_code=302)
    try:
        data = await storage.get(document.storage_key)
    except StorageError as exc:
        raise ApiError(404, "NOT_FOUND", "The stored source file is missing.") from exc
    return Response(
        data,
        media_type=document.mime_type,
        headers={
            "Content-Disposition": f"inline; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(document.filename)}",
            "Cache-Control": "private, no-store",
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "default-src 'none'; img-src 'self' data:; object-src 'self'; sandbox",
        },
    )


@router.delete("/{document_id}", status_code=204, summary="Delete an unattached document and its file")
async def delete_document(
    document_id: UUID,
    ctx: WorkspaceContext = Depends(workspace_context),
    session: AsyncSession = Depends(get_session),
) -> Response:
    document = await get_document(session, ctx.workspace_id, document_id)
    attached = await session.scalar(
        select(TransactionDocumentModel.transaction_id).where(TransactionDocumentModel.document_id == document.id)
    )
    if attached:
        raise ApiError(409, "CONFLICT", "This document belongs to a transaction. Delete the transaction instead.")
    key = document.storage_key
    await session.delete(document)
    audit.record(session, workspace_id=ctx.workspace_id, actor_user_id=ctx.user.id, action="document_deleted",
                 entity_type="document", entity_id=document_id)
    await session.commit()
    if key:
        await get_storage().delete(key)
    return Response(status_code=204)
