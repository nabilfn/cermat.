from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID, uuid4

from fastapi import Depends, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_session, init_db
from app.models import DocumentModel, TransactionDocumentModel, TransactionSetModel
from app.schemas import (
    AIExtraction,
    DocumentRecord,
    DocumentType,
    ExtractionResult,
    ReconciliationResult,
    TransactionCreate,
    TransactionDetail,
    TransactionDocumentSummary,
    TransactionRecord,
)
from app.services.extraction import extract_document as run_ai_extraction
from app.services.reconciliation import ReconciliationDocument, reconcile_three_way

ALLOWED_SUFFIXES = {".pdf", ".png", ".jpg", ".jpeg", ".webp"}
ALLOWED_MIME_TYPES = {
    "application/pdf",
    "image/png",
    "image/jpeg",
    "image/webp",
}
REQUIRED_THREE_WAY_TYPES = {
    DocumentType.purchase_order.value,
    DocumentType.delivery_order.value,
    DocumentType.invoice.value,
}


@asynccontextmanager
async def lifespan(_: FastAPI):
    await init_db()
    yield


app = FastAPI(
    title="cermat. API",
    version="0.3.0",
    description="Evidence-backed document intelligence and deterministic three-way matching.",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def _document_record(document: DocumentModel) -> DocumentRecord:
    return DocumentRecord(
        id=document.id,
        filename=document.filename,
        document_type=DocumentType(document.document_type),
        mime_type=document.mime_type,
        size_bytes=document.size_bytes,
        status=document.status,
        created_at=document.created_at,
    )


def _document_summary(document: DocumentModel) -> TransactionDocumentSummary:
    extraction = document.extraction_data or {}
    return TransactionDocumentSummary(
        id=document.id,
        filename=document.filename,
        document_type=DocumentType(document.document_type),
        status=document.status,
        document_number=extraction.get("document_number"),
        supplier_name=extraction.get("supplier_name"),
        currency=extraction.get("currency"),
        total=extraction.get("total"),
        overall_confidence=extraction.get("overall_confidence"),
    )


def _transaction_record(transaction: TransactionSetModel) -> TransactionRecord:
    return TransactionRecord(
        id=transaction.id,
        name=transaction.name,
        status=transaction.status,
        created_at=transaction.created_at,
        updated_at=transaction.updated_at,
    )


async def _get_transaction_documents(
    session: AsyncSession, transaction_id: UUID
) -> list[DocumentModel]:
    result = await session.execute(
        select(DocumentModel)
        .join(
            TransactionDocumentModel,
            TransactionDocumentModel.document_id == DocumentModel.id,
        )
        .where(TransactionDocumentModel.transaction_id == transaction_id)
    )
    documents = list(result.scalars().all())
    order = {
        DocumentType.purchase_order.value: 0,
        DocumentType.delivery_order.value: 1,
        DocumentType.invoice.value: 2,
        DocumentType.receipt.value: 3,
    }
    return sorted(documents, key=lambda item: order.get(item.document_type, 99))


async def _transaction_detail(
    session: AsyncSession, transaction: TransactionSetModel
) -> TransactionDetail:
    documents = await _get_transaction_documents(session, transaction.id)
    return TransactionDetail(
        **_transaction_record(transaction).model_dump(),
        documents=[_document_summary(document) for document in documents],
    )


@app.get("/health")
async def health() -> dict[str, str | bool]:
    return {
        "status": "ok",
        "service": "cermat-api",
        "phase": "3",
        "model": settings.openai_model,
        "ai_configured": bool(settings.openai_api_key),
        "database": "postgresql",
    }


@app.post("/api/v1/documents", response_model=DocumentRecord)
async def create_document(
    file: UploadFile = File(...),
    document_type: DocumentType = Form(...),
    session: AsyncSession = Depends(get_session),
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

    document = DocumentModel(
        id=document_id,
        filename=filename,
        document_type=document_type.value,
        mime_type=mime_type,
        size_bytes=len(contents),
        status="uploaded",
        storage_path=str(stored_path),
    )
    session.add(document)
    await session.commit()
    await session.refresh(document)
    return _document_record(document)


@app.get("/api/v1/documents/{document_id}", response_model=DocumentRecord)
async def get_document(
    document_id: UUID,
    session: AsyncSession = Depends(get_session),
) -> DocumentRecord:
    document = await session.get(DocumentModel, document_id)
    if not document:
        raise HTTPException(status_code=404, detail="Document not found.")
    return _document_record(document)


@app.post("/api/v1/documents/{document_id}/extract", response_model=ExtractionResult)
async def extract_document(
    document_id: UUID,
    session: AsyncSession = Depends(get_session),
) -> ExtractionResult:
    document = await session.get(DocumentModel, document_id)
    if not document:
        raise HTTPException(status_code=404, detail="Document not found.")

    path = Path(document.storage_path)
    if not path.exists():
        raise HTTPException(status_code=404, detail="Stored document file not found.")

    document.status = "extracting"
    await session.commit()

    try:
        extracted = await run_in_threadpool(
            run_ai_extraction,
            path=path,
            mime_type=document.mime_type,
            filename=document.filename,
            document_type=DocumentType(document.document_type),
        )
    except RuntimeError as exc:
        document.status = "failed"
        await session.commit()
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        document.status = "failed"
        await session.commit()
        raise HTTPException(
            status_code=502,
            detail="AI extraction failed. Check the API logs and model configuration.",
        ) from exc

    document.extraction_data = extracted.model_dump(mode="json")
    document.extraction_model = settings.openai_model
    document.status = "needs_review" if extracted.review_reasons else "extracted"
    await session.commit()
    await session.refresh(document)

    return ExtractionResult(
        document_id=document.id,
        filename=document.filename,
        model=settings.openai_model,
        **extracted.model_dump(),
    )


@app.post("/api/v1/transactions", response_model=TransactionRecord)
async def create_transaction(
    payload: TransactionCreate,
    session: AsyncSession = Depends(get_session),
) -> TransactionRecord:
    name = (payload.name or "").strip()
    if not name:
        now = datetime.now(timezone.utc)
        name = f"Review {now.strftime('%Y-%m-%d %H:%M UTC')}"

    transaction = TransactionSetModel(name=name, status="collecting")
    session.add(transaction)
    await session.commit()
    await session.refresh(transaction)
    return _transaction_record(transaction)


@app.get("/api/v1/transactions", response_model=list[TransactionRecord])
async def list_transactions(
    session: AsyncSession = Depends(get_session),
) -> list[TransactionRecord]:
    result = await session.execute(
        select(TransactionSetModel).order_by(TransactionSetModel.created_at.desc())
    )
    return [_transaction_record(item) for item in result.scalars().all()]


@app.get("/api/v1/transactions/{transaction_id}", response_model=TransactionDetail)
async def get_transaction(
    transaction_id: UUID,
    session: AsyncSession = Depends(get_session),
) -> TransactionDetail:
    transaction = await session.get(TransactionSetModel, transaction_id)
    if not transaction:
        raise HTTPException(status_code=404, detail="Transaction not found.")
    return await _transaction_detail(session, transaction)


@app.post(
    "/api/v1/transactions/{transaction_id}/documents/{document_id}",
    response_model=TransactionDetail,
)
async def attach_document(
    transaction_id: UUID,
    document_id: UUID,
    session: AsyncSession = Depends(get_session),
) -> TransactionDetail:
    transaction = await session.get(TransactionSetModel, transaction_id)
    document = await session.get(DocumentModel, document_id)
    if not transaction:
        raise HTTPException(status_code=404, detail="Transaction not found.")
    if not document:
        raise HTTPException(status_code=404, detail="Document not found.")

    existing = await session.execute(
        select(TransactionDocumentModel).where(
            TransactionDocumentModel.transaction_id == transaction_id,
            TransactionDocumentModel.document_type == document.document_type,
        )
    )
    if existing.scalar_one_or_none():
        raise HTTPException(
            status_code=409,
            detail=f"This transaction already has a {document.document_type} document.",
        )

    link = TransactionDocumentModel(
        transaction_id=transaction_id,
        document_id=document_id,
        document_type=document.document_type,
    )
    session.add(link)
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(
            status_code=409,
            detail="This document is already attached to a transaction.",
        ) from exc

    documents = await _get_transaction_documents(session, transaction_id)
    types = {item.document_type for item in documents}
    transaction.status = "ready" if REQUIRED_THREE_WAY_TYPES.issubset(types) else "collecting"
    await session.commit()
    await session.refresh(transaction)
    return await _transaction_detail(session, transaction)


@app.post(
    "/api/v1/transactions/{transaction_id}/reconcile",
    response_model=ReconciliationResult,
)
async def reconcile_transaction(
    transaction_id: UUID,
    session: AsyncSession = Depends(get_session),
) -> ReconciliationResult:
    transaction = await session.get(TransactionSetModel, transaction_id)
    if not transaction:
        raise HTTPException(status_code=404, detail="Transaction not found.")

    documents = await _get_transaction_documents(session, transaction_id)
    by_type = {document.document_type: document for document in documents}
    missing = REQUIRED_THREE_WAY_TYPES - set(by_type)
    if missing:
        readable = ", ".join(sorted(missing))
        raise HTTPException(
            status_code=400,
            detail=f"Three-way matching still needs: {readable}.",
        )

    for document_type in REQUIRED_THREE_WAY_TYPES:
        document = by_type[document_type]
        if not document.extraction_data:
            raise HTTPException(
                status_code=409,
                detail=f"{document_type} has not been extracted yet.",
            )

    summaries = [_document_summary(document) for document in documents]

    def reconciliation_document(document_type: DocumentType) -> ReconciliationDocument:
        document = by_type[document_type.value]
        return ReconciliationDocument(
            id=document.id,
            filename=document.filename,
            document_type=document_type,
            extraction=AIExtraction.model_validate(document.extraction_data),
        )

    result = reconcile_three_way(
        transaction_id=transaction.id,
        po=reconciliation_document(DocumentType.purchase_order),
        delivery=reconciliation_document(DocumentType.delivery_order),
        invoice=reconciliation_document(DocumentType.invoice),
        documents=summaries,
    )

    transaction.status = result.status
    transaction.last_reconciliation = result.model_dump(mode="json")
    await session.commit()
    return result


@app.get(
    "/api/v1/transactions/{transaction_id}/reconciliation",
    response_model=ReconciliationResult,
)
async def get_last_reconciliation(
    transaction_id: UUID,
    session: AsyncSession = Depends(get_session),
) -> ReconciliationResult:
    transaction = await session.get(TransactionSetModel, transaction_id)
    if not transaction:
        raise HTTPException(status_code=404, detail="Transaction not found.")
    if not transaction.last_reconciliation:
        raise HTTPException(status_code=404, detail="No reconciliation has been run yet.")
    return ReconciliationResult.model_validate(transaction.last_reconciliation)
