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
from app.models import (
    DocumentModel,
    ReviewIssueModel,
    TransactionDocumentModel,
    TransactionSetModel,
)
from app.schemas import (
    AIExtraction,
    DocumentRecord,
    DocumentType,
    ExtractionResult,
    ReconciliationIssue,
    ReconciliationResult,
    ReviewIssueRecord,
    ReviewIssueUpdate,
    ReviewSummary,
    TransactionCreate,
    TransactionDetail,
    TransactionDocumentSummary,
    TransactionHistoryRecord,
    TransactionRecord,
)
from app.routers.ask import router as ask_router
from app.routers.attention import router as attention_router
from app.routers.intelligence import router as intelligence_router
from app.routers.intelligence_settings import router as intelligence_settings_router
from app.services.extraction import extract_document as run_ai_extraction
from app.services.reconciliation import ReconciliationDocument, reconcile_three_way
from app.services.review import make_issue_key

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
    version="0.6.0",
    description=(
        "Evidence-backed document intelligence, deterministic three-way matching, "
        "a persistent human review workflow, Ask cermat. — read-only, "
        "evidence-grounded questions over persisted records — and operations intelligence."
    ),
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(ask_router)
app.include_router(intelligence_router)
app.include_router(attention_router)
app.include_router(intelligence_settings_router)


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


def _review_issue_record(issue: ReviewIssueModel) -> ReviewIssueRecord:
    payload = ReconciliationIssue.model_validate(issue.payload)
    return ReviewIssueRecord(
        id=issue.id,
        transaction_id=issue.transaction_id,
        issue_key=issue.issue_key,
        status=issue.status,
        resolution_note=issue.resolution_note,
        active=issue.active,
        resolved_at=issue.resolved_at,
        created_at=issue.created_at,
        updated_at=issue.updated_at,
        **payload.model_dump(),
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


async def _get_review_issues(
    session: AsyncSession,
    transaction_id: UUID,
    *,
    active_only: bool = True,
) -> list[ReviewIssueModel]:
    query = select(ReviewIssueModel).where(
        ReviewIssueModel.transaction_id == transaction_id
    )
    if active_only:
        query = query.where(ReviewIssueModel.active.is_(True))
    query = query.order_by(ReviewIssueModel.created_at.asc())
    result = await session.execute(query)
    issues = list(result.scalars().all())
    status_rank = {"open": 0, "resolved": 1}
    severity_rank = {"high": 0, "medium": 1, "low": 2}
    return sorted(
        issues,
        key=lambda issue: (
            status_rank.get(issue.status, 9),
            severity_rank.get(issue.severity, 9),
            issue.created_at,
        ),
    )


async def _review_summary(
    session: AsyncSession, transaction_id: UUID
) -> ReviewSummary:
    issues = await _get_review_issues(session, transaction_id)
    return ReviewSummary(
        issue_count=len(issues),
        open_issue_count=sum(issue.status == "open" for issue in issues),
        resolved_issue_count=sum(issue.status == "resolved" for issue in issues),
        high_count=sum(issue.severity == "high" for issue in issues),
    )


async def _refresh_transaction_status(
    session: AsyncSession, transaction: TransactionSetModel
) -> None:
    issues = await _get_review_issues(session, transaction.id)
    if issues:
        transaction.status = (
            "review_required"
            if any(issue.status == "open" for issue in issues)
            else "resolved"
        )
    elif transaction.last_reconciliation:
        reconciliation = ReconciliationResult.model_validate(
            transaction.last_reconciliation
        )
        transaction.status = (
            "matched"
            if reconciliation.status == "matched"
            else reconciliation.status
        )


async def _transaction_detail(
    session: AsyncSession, transaction: TransactionSetModel
) -> TransactionDetail:
    documents = await _get_transaction_documents(session, transaction.id)
    return TransactionDetail(
        **_transaction_record(transaction).model_dump(),
        documents=[_document_summary(document) for document in documents],
        review=await _review_summary(session, transaction.id),
    )


async def _history_record(
    session: AsyncSession, transaction: TransactionSetModel
) -> TransactionHistoryRecord:
    documents = await _get_transaction_documents(session, transaction.id)
    review = await _review_summary(session, transaction.id)
    preferred = next(
        (
            document
            for document in documents
            if document.document_type == DocumentType.invoice.value
        ),
        documents[0] if documents else None,
    )
    extraction = preferred.extraction_data if preferred and preferred.extraction_data else {}
    return TransactionHistoryRecord(
        **_transaction_record(transaction).model_dump(),
        document_count=len(documents),
        issue_count=review.issue_count,
        open_issue_count=review.open_issue_count,
        resolved_issue_count=review.resolved_issue_count,
        high_count=review.high_count,
        supplier_name=extraction.get("supplier_name"),
        currency=extraction.get("currency"),
        total=extraction.get("total"),
    )


async def _sync_review_issues(
    session: AsyncSession,
    transaction: TransactionSetModel,
    reconciliation: ReconciliationResult,
) -> list[ReviewIssueModel]:
    existing_result = await session.execute(
        select(ReviewIssueModel).where(
            ReviewIssueModel.transaction_id == transaction.id
        )
    )
    existing = list(existing_result.scalars().all())
    by_key = {issue.issue_key: issue for issue in existing}

    for stored in existing:
        stored.active = False

    for issue in reconciliation.issues:
        key = make_issue_key(issue)
        stored = by_key.get(key)
        if stored is None:
            stored = ReviewIssueModel(
                transaction_id=transaction.id,
                issue_key=key,
                code=issue.code,
                title=issue.title,
                severity=issue.severity,
                status="open",
                payload=issue.model_dump(mode="json"),
                active=True,
            )
            session.add(stored)
            by_key[key] = stored
        else:
            stored.code = issue.code
            stored.title = issue.title
            stored.severity = issue.severity
            stored.payload = issue.model_dump(mode="json")
            stored.active = True

    await session.flush()
    return await _get_review_issues(session, transaction.id)


@app.get("/health")
async def health() -> dict[str, str | bool]:
    return {
        "status": "ok",
        "service": "cermat-api",
        "phase": "6",
        "model": settings.openai_model,
        "ai_configured": bool(settings.openai_api_key),
        "database": "postgresql",
        "review_workflow": True,
        "ask": True,
        "intelligence": True,
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


@app.get("/api/v1/transactions", response_model=list[TransactionHistoryRecord])
async def list_transactions(
    session: AsyncSession = Depends(get_session),
) -> list[TransactionHistoryRecord]:
    result = await session.execute(
        select(TransactionSetModel).order_by(TransactionSetModel.updated_at.desc())
    )
    records: list[TransactionHistoryRecord] = []
    for transaction in result.scalars().all():
        records.append(await _history_record(session, transaction))
    return records


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
    transaction.status = (
        "ready" if REQUIRED_THREE_WAY_TYPES.issubset(types) else "collecting"
    )
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

    transaction.last_reconciliation = result.model_dump(mode="json")
    await _sync_review_issues(session, transaction, result)
    await _refresh_transaction_status(session, transaction)
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


@app.get(
    "/api/v1/transactions/{transaction_id}/issues",
    response_model=list[ReviewIssueRecord],
)
async def list_review_issues(
    transaction_id: UUID,
    session: AsyncSession = Depends(get_session),
) -> list[ReviewIssueRecord]:
    transaction = await session.get(TransactionSetModel, transaction_id)
    if not transaction:
        raise HTTPException(status_code=404, detail="Transaction not found.")
    issues = await _get_review_issues(session, transaction_id)
    return [_review_issue_record(issue) for issue in issues]


@app.patch(
    "/api/v1/transactions/{transaction_id}/issues/{issue_id}",
    response_model=ReviewIssueRecord,
)
async def update_review_issue(
    transaction_id: UUID,
    issue_id: UUID,
    payload: ReviewIssueUpdate,
    session: AsyncSession = Depends(get_session),
) -> ReviewIssueRecord:
    transaction = await session.get(TransactionSetModel, transaction_id)
    if not transaction:
        raise HTTPException(status_code=404, detail="Transaction not found.")

    issue = await session.get(ReviewIssueModel, issue_id)
    if not issue or issue.transaction_id != transaction_id or not issue.active:
        raise HTTPException(status_code=404, detail="Review issue not found.")

    issue.status = payload.status
    note = (payload.resolution_note or "").strip()
    issue.resolution_note = note or None
    issue.resolved_at = (
        datetime.now(timezone.utc) if payload.status == "resolved" else None
    )

    await _refresh_transaction_status(session, transaction)
    await session.commit()
    await session.refresh(issue)
    return _review_issue_record(issue)
