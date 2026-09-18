from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Literal
from uuid import UUID, uuid4

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

app = FastAPI(
    title="cermat. API",
    version="0.1.0",
    description="Document intelligence and reconciliation API for cermat.",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class DocumentType(str, Enum):
    purchase_order = "purchase_order"
    delivery_order = "delivery_order"
    invoice = "invoice"
    receipt = "receipt"


class DocumentRecord(BaseModel):
    id: UUID
    filename: str
    document_type: DocumentType
    status: Literal["uploaded", "extracted", "needs_review"]
    created_at: datetime


class Evidence(BaseModel):
    field: str
    source_text: str
    page: int | None = None
    confidence: float = Field(ge=0, le=1)


class ExtractedLineItem(BaseModel):
    description: str
    quantity: float
    unit_price: float
    line_total: float


class ExtractionResult(BaseModel):
    document_id: UUID
    supplier_name: str | None
    document_number: str | None
    currency: str
    subtotal: float | None
    tax: float | None
    total: float | None
    confidence: float = Field(ge=0, le=1)
    line_items: list[ExtractedLineItem]
    evidence: list[Evidence]
    note: str


class ReconciliationIssue(BaseModel):
    field: str
    expected: str
    actual: str
    severity: Literal["low", "medium", "high"]
    explanation: str


class ReconciliationDemo(BaseModel):
    status: Literal["matched", "review_required"]
    issues: list[ReconciliationIssue]


DOCUMENTS: dict[UUID, DocumentRecord] = {}


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "service": "cermat-api"}


@app.post("/api/v1/documents", response_model=DocumentRecord)
async def create_document(
    file: UploadFile = File(...),
    document_type: DocumentType = Form(...),
) -> DocumentRecord:
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in {".pdf", ".png", ".jpg", ".jpeg", ".webp"}:
        raise HTTPException(
            status_code=400,
            detail="Supported file types: PDF, PNG, JPG, JPEG, WEBP.",
        )

    record = DocumentRecord(
        id=uuid4(),
        filename=file.filename or "uploaded-document",
        document_type=document_type,
        status="uploaded",
        created_at=datetime.now(timezone.utc),
    )
    DOCUMENTS[record.id] = record
    await file.read()
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
    if not record:
        raise HTTPException(status_code=404, detail="Document not found.")

    # Phase 1 placeholder:
    # This contract will be backed by actual PDF/image parsing + multimodal
    # structured extraction in the next build.
    extracted = ExtractionResult(
        document_id=document_id,
        supplier_name="Demo Supplier Sdn. Bhd.",
        document_number="INV-DEMO-001",
        currency="MYR",
        subtotal=480.00,
        tax=28.80,
        total=508.80,
        confidence=0.94,
        line_items=[
            ExtractedLineItem(
                description="Office supply item",
                quantity=4,
                unit_price=120.00,
                line_total=480.00,
            )
        ],
        evidence=[
            Evidence(
                field="total",
                source_text="Total RM 508.80",
                page=1,
                confidence=0.98,
            )
        ],
        note="Demo extraction contract. Replace with real multimodal extraction next.",
    )

    DOCUMENTS[document_id] = record.model_copy(update={"status": "extracted"})
    return extracted


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
