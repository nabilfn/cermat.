from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID, uuid4

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.schemas import (
    DocumentRecord,
    DocumentType,
    ExtractionResult,
    ReconciliationDemo,
    ReconciliationIssue,
)
from app.services.extraction import extract_document as run_ai_extraction

app = FastAPI(
    title="cermat. API",
    version="0.2.0",
    description="Evidence-backed document intelligence and reconciliation API for cermat.",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

ALLOWED_SUFFIXES = {".pdf", ".png", ".jpg", ".jpeg", ".webp"}
ALLOWED_MIME_TYPES = {
    "application/pdf",
    "image/png",
    "image/jpeg",
    "image/webp",
}

DOCUMENTS: dict[UUID, DocumentRecord] = {}
DOCUMENT_PATHS: dict[UUID, Path] = {}


@app.get("/health")
async def health() -> dict[str, str | bool]:
    return {
        "status": "ok",
        "service": "cermat-api",
        "model": settings.openai_model,
        "ai_configured": bool(settings.openai_api_key),
    }


@app.post("/api/v1/documents", response_model=DocumentRecord)
async def create_document(
    file: UploadFile = File(...),
    document_type: DocumentType = Form(...),
) -> DocumentRecord:
    filename = file.filename or "uploaded-document"
    suffix = Path(filename).suffix.lower()
    mime_type = file.content_type or "application/octet-stream"

    if suffix not in ALLOWED_SUFFIXES or mime_type not in ALLOWED_MIME_TYPES:
        raise HTTPException(
            status_code=400,
            detail="Supported file types: PDF, PNG, JPG/JPEG, and WEBP.",
        )

    contents = await file.read(settings.max_upload_bytes + 1)
    if len(contents) > settings.max_upload_bytes:
        raise HTTPException(status_code=413, detail="File is larger than 15 MB.")
    if not contents:
        raise HTTPException(status_code=400, detail="The uploaded file is empty.")

    document_id = uuid4()
    stored_path = settings.data_dir / f"{document_id}{suffix}"
    stored_path.write_bytes(contents)

    record = DocumentRecord(
        id=document_id,
        filename=filename,
        document_type=document_type,
        mime_type=mime_type,
        size_bytes=len(contents),
        status="uploaded",
        created_at=datetime.now(timezone.utc),
    )

    DOCUMENTS[document_id] = record
    DOCUMENT_PATHS[document_id] = stored_path
    return record


@app.get("/api/v1/documents/{document_id}", response_model=DocumentRecord)
async def get_document(document_id: UUID) -> DocumentRecord:
    record = DOCUMENTS.get(document_id)
    if not record:
        raise HTTPException(status_code=404, detail="Document not found.")
    return record


@app.post("/api/v1/documents/{document_id}/extract", response_model=ExtractionResult)
async def extract_document(document_id: UUID) -> ExtractionResult:
    record = DOCUMENTS.get(document_id)
    path = DOCUMENT_PATHS.get(document_id)

    if not record or not path or not path.exists():
        raise HTTPException(status_code=404, detail="Document not found.")

    DOCUMENTS[document_id] = record.model_copy(update={"status": "extracting"})

    try:
        extracted = await run_in_threadpool(
            run_ai_extraction,
            path=path,
            mime_type=record.mime_type,
            filename=record.filename,
            document_type=record.document_type,
        )
    except RuntimeError as exc:
        DOCUMENTS[document_id] = record.model_copy(update={"status": "failed"})
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        DOCUMENTS[document_id] = record.model_copy(update={"status": "failed"})
        raise HTTPException(
            status_code=502,
            detail="AI extraction failed. Check the API logs and model configuration.",
        ) from exc

    final_status = "needs_review" if extracted.review_reasons else "extracted"
    DOCUMENTS[document_id] = record.model_copy(update={"status": final_status})

    return ExtractionResult(
        document_id=document_id,
        filename=record.filename,
        model=settings.openai_model,
        **extracted.model_dump(),
    )


@app.get("/api/v1/reconciliation/demo", response_model=ReconciliationDemo)
async def reconciliation_demo() -> ReconciliationDemo:
    return ReconciliationDemo(
        status="review_required",
        issues=[
            ReconciliationIssue(
                field="quantity",
                expected="10",
                actual="8",
                severity="high",
                explanation="Invoice quantity is 2 units lower than the purchase order.",
            ),
            ReconciliationIssue(
                field="unit_price",
                expected="RM 42.00",
                actual="RM 44.00",
                severity="medium",
                explanation="Invoice unit price is RM 2.00 higher than the purchase order.",
            ),
        ],
    )
