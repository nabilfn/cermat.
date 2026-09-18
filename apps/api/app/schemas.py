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


TransactionStatus = Literal[
    "collecting",
    "ready",
    "matched",
    "review_required",
    "insufficient_data",
    "resolved",
]


class TransactionRecord(BaseModel):
    id: UUID
    name: str
    status: TransactionStatus
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


class ReviewSummary(BaseModel):
    issue_count: int = 0
    open_issue_count: int = 0
    resolved_issue_count: int = 0
    high_count: int = 0


class TransactionDetail(TransactionRecord):
    documents: list[TransactionDocumentSummary]
    review: ReviewSummary = Field(default_factory=ReviewSummary)


class TransactionHistoryRecord(TransactionRecord):
    document_count: int
    issue_count: int
    open_issue_count: int
    resolved_issue_count: int
    high_count: int
    supplier_name: str | None = None
    currency: str | None = None
    total: float | None = None


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


class ReviewIssueRecord(ReconciliationIssue):
    id: UUID
    transaction_id: UUID
    issue_key: str
    status: Literal["open", "resolved"]
    resolution_note: str | None = None
    active: bool
    resolved_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class ReviewIssueUpdate(BaseModel):
    status: Literal["open", "resolved"]
    resolution_note: str | None = Field(default=None, max_length=1000)


# ---------------------------------------------------------------------------
# Phase 5 — Ask cermat.
# ---------------------------------------------------------------------------


class AskIntent(str, Enum):
    list_open_issues = "list_open_issues"
    list_high_severity_issues = "list_high_severity_issues"
    list_resolved_issues = "list_resolved_issues"
    transaction_details = "transaction_details"
    transaction_explanation = "transaction_explanation"
    supplier_issues = "supplier_issues"
    supplier_issue_summary = "supplier_issue_summary"
    price_discrepancies = "price_discrepancies"
    quantity_discrepancies = "quantity_discrepancies"
    supplier_mismatches = "supplier_mismatches"
    currency_mismatches = "currency_mismatches"
    arithmetic_mismatches = "arithmetic_mismatches"
    missing_documents = "missing_documents"
    recent_transactions = "recent_transactions"
    resolved_transactions = "resolved_transactions"
    transaction_search = "transaction_search"
    general_summary = "general_summary"
    unsupported = "unsupported"


IssueFamily = Literal[
    "price",
    "quantity",
    "supplier",
    "currency",
    "arithmetic",
    "missing_item",
    "unexpected_item",
    "missing_line_items",
]
IssueStatusFilter = Literal["open", "resolved", "any"]
QuantityDirection = Literal["invoice_over_delivery", "delivery_short_of_order"]
Severity = Literal["low", "medium", "high"]


class AskFilters(BaseModel):
    supplier: str | None = Field(default=None, max_length=160)
    severity: Severity | None = None
    status: IssueStatusFilter | None = None
    transaction_id: UUID | None = None
    transaction_ref: str | None = Field(default=None, max_length=160)
    issue_type: IssueFamily | None = None
    quantity_direction: QuantityDirection | None = None
    search: str | None = Field(default=None, max_length=160)


class AskPlan(BaseModel):
    intent: AskIntent
    filters: AskFilters = Field(default_factory=AskFilters)
    limit: int = Field(default=20, ge=1, le=50)


class AskEntity(BaseModel):
    """An entity from a previous answer, resolved to a database identity."""

    kind: Literal["transaction", "supplier"]
    id: str = Field(max_length=160)
    label: str = Field(max_length=160)


class AskConversationContext(BaseModel):
    """Minimum follow-up state. Never trusted as fact: IDs are re-resolved."""

    previous_question: str | None = Field(default=None, max_length=500)
    previous_intent: AskIntent | None = None
    entities: list[AskEntity] = Field(default_factory=list, max_length=12)


class AskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=500)
    transaction_id: UUID | None = None
    context: AskConversationContext | None = None


class AskSource(BaseModel):
    """One traceable piece of evidence, rendered as a source chip."""

    id: str
    label: str
    document_id: UUID
    filename: str
    document_type: DocumentType
    document_number: str | None
    transaction_id: UUID
    transaction_name: str
    page: int | None
    field_path: str
    value: str | None
    source_text: str
    confidence: float | None
    snippet_available: bool
    # Interface contract for a future page viewer. Always null in Phase 5.
    preview_url: str | None = None


class AskVariance(BaseModel):
    """Variance calculated in Python from the persisted expected/actual values."""

    kind: Literal["money", "quantity"]
    currency: str | None
    expected: float
    actual: float
    delta: float
    percentage: float | None
    billed_impact: float | None = None


class AskIssueRow(BaseModel):
    issue_id: UUID
    transaction_id: UUID
    transaction_name: str
    supplier: str | None
    code: str
    family: str
    title: str
    severity: Severity
    status: Literal["open", "resolved"]
    item_description: str | None
    expected: str | None
    actual: str | None
    delta: str | None
    variance: AskVariance | None
    explanation: str
    resolution_note: str | None
    resolved_at: datetime | None
    source_ids: list[str]


class AskTransactionRow(BaseModel):
    transaction_id: UUID
    name: str
    supplier: str | None
    status: TransactionStatus
    currency: str | None
    total: float | None
    document_types: list[DocumentType]
    missing_document_types: list[DocumentType]
    open_issue_count: int
    resolved_issue_count: int
    highest_open_severity: Severity | None
    updated_at: datetime


class AskSupplierRow(BaseModel):
    supplier: str
    transaction_count: int
    open_issue_count: int
    resolved_issue_count: int
    high_open_count: int
    transaction_ids: list[UUID]
    open_by_family: dict[str, int]


class AskMetrics(BaseModel):
    """Counts computed by backend code. The model may only restate them."""

    transaction_count: int = 0
    issue_count: int = 0
    open_issue_count: int = 0
    resolved_issue_count: int = 0
    by_severity: dict[str, int] = Field(default_factory=dict)
    by_family: dict[str, int] = Field(default_factory=dict)
    by_status: dict[str, int] = Field(default_factory=dict)
    supplier_count: int = 0


class AskAnswerPoint(BaseModel):
    text: str
    source_ids: list[str] = Field(default_factory=list)


class AskAnswer(BaseModel):
    headline: str
    points: list[AskAnswerPoint] = Field(default_factory=list)


class AskResponse(BaseModel):
    question: str
    intent: AskIntent
    filters: AskFilters
    scope: Literal["workspace", "transaction"]
    scope_transaction_id: UUID | None
    scope_transaction_name: str | None
    outcome: Literal["answered", "no_results", "unsupported", "not_found"]
    answer: AskAnswer
    answer_mode: Literal["model", "records"]
    metrics: AskMetrics
    result_kind: Literal["issues", "transactions", "suppliers", "none"]
    issues: list[AskIssueRow]
    transactions: list[AskTransactionRow]
    suppliers: list[AskSupplierRow]
    sources: list[AskSource]
    notices: list[str]
    follow_up_suggestions: list[str]
    context: AskConversationContext


class AskSuggestions(BaseModel):
    scope: Literal["workspace", "transaction"]
    suggestions: list[str]


class TransactionContext(BaseModel):
    """The compact, grounded record set Ask cermat. uses for one transaction."""

    transaction: AskTransactionRow
    documents: list[TransactionDocumentSummary]
    issues: list[AskIssueRow]
    sources: list[AskSource]
    reconciled_at: datetime | None
