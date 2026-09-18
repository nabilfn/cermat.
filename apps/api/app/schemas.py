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
    # Phase 6 — intelligence intents
    overview_summary = "overview_summary"
    supplier_ranking_by_issue_count = "supplier_ranking_by_issue_count"
    supplier_summary = "supplier_summary"
    recurring_patterns = "recurring_patterns"
    anomaly_signals = "anomaly_signals"
    exception_trend = "exception_trend"
    priority_queue = "priority_queue"
    variance_summary = "variance_summary"
    resolution_performance = "resolution_performance"
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
    period: Literal["7d", "30d", "90d", "all"] | None = None


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
    result_kind: Literal["issues", "transactions", "suppliers", "insights", "none"]
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


# ---------------------------------------------------------------------------
# Phase 6 — Intelligence & Operations
# ---------------------------------------------------------------------------

Period = Literal["7d", "30d", "90d", "all"]
TrendCategory = Literal["all", "price", "quantity", "supplier", "currency", "missing_documents"]
PriorityBand = Literal["critical", "high", "normal"]


class PriorityWeights(BaseModel):
    """Points added to an open issue's priority score. See docs/architecture.md."""

    severity_high: float = Field(default=40, ge=0, le=200)
    severity_medium: float = Field(default=20, ge=0, le=200)
    severity_low: float = Field(default=8, ge=0, le=200)
    high_value_variance: float = Field(default=20, ge=0, le=200)
    high_variance_percentage: float = Field(default=15, ge=0, le=200)
    age_per_day: float = Field(default=1.5, ge=0, le=50)
    age_cap: float = Field(default=15, ge=0, le=200)
    overdue: float = Field(default=10, ge=0, le=200)
    related_issue: float = Field(default=4, ge=0, le=50)
    related_cap: float = Field(default=12, ge=0, le=200)
    recurring_supplier: float = Field(default=12, ge=0, le=200)
    issue_type: dict[str, float] = Field(
        default_factory=lambda: {
            "currency": 8,
            "arithmetic": 6,
            "quantity": 4,
            "price": 4,
            "unexpected_item": 4,
            "supplier": 3,
            "missing_item": 2,
            "missing_line_items": 2,
        }
    )


class IntelligenceSettings(BaseModel):
    high_value_variance_amount: float = Field(default=500, gt=0)
    high_variance_percentage: float = Field(default=10, gt=0, le=1000)
    recurring_issue_min_count: int = Field(default=3, ge=2, le=50)
    recurring_issue_period_days: int = Field(default=90, ge=7, le=730)
    overdue_review_days: int = Field(default=7, ge=1, le=365)
    low_confidence_threshold: float = Field(default=0.75, gt=0, lt=1)
    amount_median_multiplier: float = Field(default=3, ge=1.5, le=20)
    min_history_for_baseline: int = Field(default=4, ge=2, le=50)
    issue_spike_ratio: float = Field(default=2, ge=1.2, le=10)
    critical_score: float = Field(default=85, ge=1, le=500)
    high_score: float = Field(default=55, ge=1, le=500)
    priority_weights: PriorityWeights = Field(default_factory=PriorityWeights)


class IntelligenceSettingsRecord(IntelligenceSettings):
    is_default: bool
    updated_at: datetime | None


class IntelligenceSettingsUpdate(BaseModel):
    high_value_variance_amount: float | None = Field(default=None, gt=0)
    high_variance_percentage: float | None = Field(default=None, gt=0, le=1000)
    recurring_issue_min_count: int | None = Field(default=None, ge=2, le=50)
    recurring_issue_period_days: int | None = Field(default=None, ge=7, le=730)
    overdue_review_days: int | None = Field(default=None, ge=1, le=365)
    low_confidence_threshold: float | None = Field(default=None, gt=0, lt=1)
    amount_median_multiplier: float | None = Field(default=None, ge=1.5, le=20)
    min_history_for_baseline: int | None = Field(default=None, ge=2, le=50)
    issue_spike_ratio: float | None = Field(default=None, ge=1.2, le=10)
    critical_score: float | None = Field(default=None, ge=1, le=500)
    high_score: float | None = Field(default=None, ge=1, le=500)
    priority_weights: dict[str, float | dict[str, float]] | None = None
    reset: bool = False


class FinancialVariance(BaseModel):
    """Standardised monetary variance for one issue. Never converted across currencies."""

    basis: Literal["billed_price_difference", "billed_quantity_difference", "line_arithmetic"]
    currency: str | None
    signed_variance: float
    absolute_variance: float
    variance_percentage: float | None


class CurrencyTotal(BaseModel):
    currency: str | None
    signed_total: float
    absolute_total: float
    issue_count: int


class OverviewMetrics(BaseModel):
    total_transactions: int
    transactions_needing_review: int
    open_issue_count: int
    resolved_issue_count: int
    high_severity_issue_count: int
    medium_severity_issue_count: int
    low_severity_issue_count: int
    total_active_variance_amount: list[CurrencyTotal]
    average_variance_percentage: float | None
    price_discrepancy_count: int
    quantity_discrepancy_count: int
    supplier_mismatch_count: int
    currency_mismatch_count: int
    missing_document_count: int
    overdue_issue_count: int
    resolution_rate: float | None
    average_resolution_time_hours: float | None


class PeriodActivity(BaseModel):
    period: Period
    start: datetime | None
    end: datetime
    previous_start: datetime | None
    transactions_created: int
    issues_created: int
    issues_resolved: int
    created_by_family: dict[str, int]
    previous_issues_created: int | None
    previous_created_by_family: dict[str, int] | None


class ResolutionPerformance(BaseModel):
    period: Period
    issues_created: int
    issues_resolved: int
    resolution_rate: float | None
    median_resolution_hours: float | None
    average_resolution_hours: float | None
    oldest_open_issue_age_days: float | None
    overdue_issue_count: int


class IssueMixRow(BaseModel):
    family: str
    label: str
    count: int
    share: float


class DataQualityCheck(BaseModel):
    check: str
    label: str
    count: int
    document_ids: list[UUID]


class DataQualitySummary(BaseModel):
    documents_checked: int
    affected_document_count: int
    unmatched_line_count: int
    checks: list[DataQualityCheck]


class PriorityComponent(BaseModel):
    factor: str
    points: float
    detail: str


class PriorityItem(BaseModel):
    issue_id: UUID
    transaction_id: UUID
    transaction_name: str
    supplier: str | None
    supplier_key: str | None
    code: str
    family: str
    title: str
    item_description: str | None
    severity: Severity
    age_days: float
    variance: FinancialVariance | None
    priority_score: float
    priority_band: PriorityBand
    priority_reasons: list[str]
    components: list[PriorityComponent]


class SupplierIntel(BaseModel):
    supplier_key: str
    supplier_name: str
    name_variants: list[str]
    transaction_count: int
    reconciled_transaction_count: int
    transactions_with_issues: int
    open_issue_count: int
    resolved_issue_count: int
    issue_rate: float | None
    price_discrepancy_count: int
    quantity_discrepancy_count: int
    supplier_mismatch_count: int
    total_variance_amount: list[CurrencyTotal]
    average_variance_percentage: float | None
    average_resolution_time_hours: float | None
    last_transaction_at: datetime | None


class RelatedTransaction(BaseModel):
    id: UUID
    name: str


class PatternSignal(BaseModel):
    key: str
    pattern_type: str
    title: str
    supplier: str | None
    supplier_key: str | None
    issue_family: str | None
    item: str | None
    count: int
    threshold: int
    period_days: int
    transaction_ids: list[UUID]
    related_transactions: list[RelatedTransaction]
    issue_ids: list[UUID]
    first_seen: datetime
    last_seen: datetime
    severity: Severity


class AnomalySignal(BaseModel):
    key: str
    signal: str
    title: str
    severity: Severity
    observed_value: float
    baseline: float | None
    baseline_label: str
    threshold: float
    unit: Literal["percent", "amount", "days", "count"]
    currency: str | None
    reason: str
    supplier: str | None
    supplier_key: str | None
    related_transactions: list[RelatedTransaction]
    related_issue_ids: list[UUID]


class TrendPoint(BaseModel):
    date: str
    created: int
    resolved: int
    open_end_of_period: int


class TrendResponse(BaseModel):
    period: Period
    category: TrendCategory
    granularity: Literal["day", "week", "month"]
    start: datetime | None
    end: datetime
    series: list[TrendPoint]
    total_created: int
    total_resolved: int
    previous_period_created: int | None
    direction: Literal["increasing", "decreasing", "stable"] | None
    sufficient_history: bool
    message: str | None


class SupplierException(BaseModel):
    issue_id: UUID
    transaction_id: UUID
    transaction_name: str
    family: str
    title: str
    item_description: str | None
    severity: Severity
    status: Literal["open", "resolved"]
    created_at: datetime
    variance: FinancialVariance | None


class SupplierTransaction(BaseModel):
    id: UUID
    name: str
    status: TransactionStatus
    created_at: datetime
    open_issue_count: int
    currency: str | None
    total: float | None
    missing_document_types: list[DocumentType]


class SupplierPatternSummary(BaseModel):
    window: int
    exceptions_considered: int
    most_common_family: str | None
    most_common_count: int
    price_above_po_count: int
    sentence: str


class SupplierDetail(BaseModel):
    supplier: SupplierIntel
    open_by_family: dict[str, int]
    missing_document_count: int
    recent_exceptions: list[SupplierException]
    pattern_summary: SupplierPatternSummary | None
    patterns: list[PatternSignal]
    anomalies: list[AnomalySignal]
    transactions: list[SupplierTransaction]


class OverviewResponse(BaseModel):
    as_of: datetime
    period: Period
    has_data: bool
    has_reconciled_data: bool
    metrics: OverviewMetrics
    activity: PeriodActivity
    resolution: ResolutionPerformance
    issue_mix: list[IssueMixRow]
    priority: list[PriorityItem]
    suppliers: list[SupplierIntel]
    patterns: list[PatternSignal]
    anomalies: list[AnomalySignal]
    data_quality: DataQualitySummary
    trend: TrendResponse


class BriefResponse(BaseModel):
    period: Period
    generated_at: datetime
    mode: Literal["model", "records"]
    lines: list[str]
    facts: dict
    notices: list[str]
    suggested_question: str | None


AttentionEventType = Literal[
    "high_severity_issue",
    "large_variance",
    "recurring_pattern",
    "overdue_review",
    "anomaly",
]


class AttentionEventRecord(BaseModel):
    id: UUID
    event_key: str
    event_type: AttentionEventType
    title: str
    message: str
    severity: Severity
    entity_type: Literal["transaction", "supplier", "workspace"]
    entity_id: str | None
    entity_label: str | None
    transaction_id: UUID | None
    created_at: datetime
    seen_at: datetime | None
    dismissed_at: datetime | None
    cleared_at: datetime | None


class AttentionList(BaseModel):
    active_count: int
    unseen_count: int
    events: list[AttentionEventRecord]


class AttentionUpdate(BaseModel):
    seen: bool | None = None
    dismissed: bool | None = None
