from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field


class DocumentType(str, Enum):
    purchase_order = "purchase_order"
    delivery_order = "delivery_order"
    invoice = "invoice"
    receipt = "receipt"


class DocumentRecord(BaseModel):
    id: UUID
    filename: str
    document_type: DocumentType
    mime_type: str
    size_bytes: int
    status: Literal["uploaded", "extracting", "extracted", "needs_review", "failed"]
    created_at: datetime


class Evidence(BaseModel):
    field_path: str = Field(
        description="Dot-path to the supported field, e.g. total or line_items.0.quantity"
    )
    source_text: str = Field(
        description="Short verbatim snippet visible in the source document"
    )
    page: int | None = Field(
        description="1-indexed page number when known; image uploads are page 1"
    )
    confidence: float = Field(ge=0, le=1)


class ExtractedLineItem(BaseModel):
    description: str
    sku: str | None
    quantity: float | None
    unit_price: float | None
    line_total: float | None


class AIExtraction(BaseModel):
    supplier_name: str | None
    supplier_registration_no: str | None
    document_number: str | None
    document_date: str | None = Field(
        description="ISO-8601 YYYY-MM-DD when confidently inferable, otherwise null"
    )
    currency: str | None = Field(
        description="ISO 4217 currency code such as MYR, USD, SGD, otherwise null"
    )
    subtotal: float | None
    tax: float | None
    total: float | None
    line_items: list[ExtractedLineItem]
    evidence: list[Evidence]
    overall_confidence: float = Field(ge=0, le=1)
    review_reasons: list[str]


class ExtractionResult(AIExtraction):
    document_id: UUID
    filename: str
    model: str


class TransactionCreate(BaseModel):
    name: str | None = Field(default=None, max_length=120)


class TransactionRecord(BaseModel):
    id: UUID
    name: str
    status: Literal["collecting", "ready", "matched", "review_required", "insufficient_data"]
    created_at: datetime
    updated_at: datetime


class TransactionDocumentSummary(BaseModel):
    id: UUID
    filename: str
    document_type: DocumentType
    status: str
    document_number: str | None
    supplier_name: str | None
    currency: str | None
    total: float | None
    overall_confidence: float | None


class TransactionDetail(TransactionRecord):
    documents: list[TransactionDocumentSummary]


class EvidenceReference(BaseModel):
    document_id: UUID
    filename: str
    document_type: DocumentType
    field_path: str
    source_text: str
    page: int | None
    value: str | None = None


class MatchedLine(BaseModel):
    key: str
    description: str
    sku: str | None
    po_quantity: float | None
    delivered_quantity: float | None
    invoice_quantity: float | None
    po_unit_price: float | None
    invoice_unit_price: float | None
    status: Literal["matched", "review_required", "unmatched"]


class ReconciliationIssue(BaseModel):
    code: str
    title: str
    severity: Literal["low", "medium", "high"]
    item_description: str | None = None
    expected: str | None = None
    actual: str | None = None
    delta: str | None = None
    explanation: str
    sources: list[EvidenceReference]


class ReconciliationSummary(BaseModel):
    issue_count: int
    high: int
    medium: int
    low: int
    matched_lines: int
    review_lines: int


class ReconciliationResult(BaseModel):
    transaction_id: UUID
    status: Literal["matched", "review_required", "insufficient_data"]
    documents: list[TransactionDocumentSummary]
    summary: ReconciliationSummary
    lines: list[MatchedLine]
    issues: list[ReconciliationIssue]
    generated_at: datetime
