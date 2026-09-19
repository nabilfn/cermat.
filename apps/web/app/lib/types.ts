// Shared API types. Shapes mirror the FastAPI schemas in apps/api/app/schemas.py.

export type Period = "7d" | "30d" | "90d" | "all";
export type Severity = "low" | "medium" | "high";
export type TransactionStatus =
  | "collecting"
  | "ready"
  | "matched"
  | "review_required"
  | "insufficient_data"
  | "resolved";

export type FinancialVariance = {
  basis: "billed_price_difference" | "billed_quantity_difference" | "line_arithmetic";
  currency: string | null;
  signed_variance: number;
  absolute_variance: number;
  variance_percentage: number | null;
};

export type CurrencyTotal = {
  currency: string | null;
  signed_total: number;
  absolute_total: number;
  issue_count: number;
};

export type PriorityItem = {
  issue_id: string;
  transaction_id: string;
  transaction_name: string;
  supplier: string | null;
  supplier_key: string | null;
  code: string;
  family: string;
  title: string;
  item_description: string | null;
  severity: Severity;
  age_days: number;
  variance: FinancialVariance | null;
  priority_score: number;
  priority_band: "critical" | "high" | "normal";
  priority_reasons: string[];
};

export type SupplierIntel = {
  supplier_key: string;
  supplier_name: string;
  name_variants: string[];
  transaction_count: number;
  reconciled_transaction_count: number;
  transactions_with_issues: number;
  open_issue_count: number;
  resolved_issue_count: number;
  issue_rate: number | null;
  price_discrepancy_count: number;
  quantity_discrepancy_count: number;
  supplier_mismatch_count: number;
  total_variance_amount: CurrencyTotal[];
  average_variance_percentage: number | null;
  average_resolution_time_hours: number | null;
  last_transaction_at: string | null;
};

export type RelatedTransaction = { id: string; name: string };

export type PatternSignal = {
  key: string;
  pattern_type: string;
  title: string;
  supplier: string | null;
  supplier_key: string | null;
  count: number;
  threshold: number;
  period_days: number;
  related_transactions: RelatedTransaction[];
  first_seen: string;
  last_seen: string;
  severity: Severity;
};

export type AnomalySignal = {
  key: string;
  signal: string;
  title: string;
  severity: Severity;
  observed_value: number;
  baseline: number | null;
  baseline_label: string;
  threshold: number;
  unit: "percent" | "amount" | "days" | "count";
  currency: string | null;
  reason: string;
  supplier: string | null;
  supplier_key: string | null;
  related_transactions: RelatedTransaction[];
};

export type TrendPoint = {
  date: string;
  created: number;
  resolved: number;
  open_end_of_period: number;
};

export type TrendResponse = {
  period: Period;
  granularity: "day" | "week" | "month";
  series: TrendPoint[];
  total_created: number;
  total_resolved: number;
  previous_period_created: number | null;
  direction: "increasing" | "decreasing" | "stable" | null;
  sufficient_history: boolean;
  message: string | null;
};

export type OverviewResponse = {
  as_of: string;
  period: Period;
  has_data: boolean;
  has_reconciled_data: boolean;
  metrics: {
    total_transactions: number;
    transactions_needing_review: number;
    open_issue_count: number;
    resolved_issue_count: number;
    high_severity_issue_count: number;
    medium_severity_issue_count: number;
    low_severity_issue_count: number;
    total_active_variance_amount: CurrencyTotal[];
    average_variance_percentage: number | null;
    missing_document_count: number;
    overdue_issue_count: number;
    resolution_rate: number | null;
    average_resolution_time_hours: number | null;
  };
  activity: {
    issues_created: number;
    issues_resolved: number;
    previous_issues_created: number | null;
    transactions_created: number;
  };
  resolution: {
    issues_created: number;
    issues_resolved: number;
    resolution_rate: number | null;
    median_resolution_hours: number | null;
    average_resolution_hours: number | null;
    oldest_open_issue_age_days: number | null;
    overdue_issue_count: number;
  };
  issue_mix: Array<{ family: string; label: string; count: number; share: number }>;
  priority: PriorityItem[];
  suppliers: SupplierIntel[];
  patterns: PatternSignal[];
  anomalies: AnomalySignal[];
  data_quality: {
    documents_checked: number;
    affected_document_count: number;
    unmatched_line_count: number;
    checks: Array<{ check: string; label: string; count: number }>;
  };
  trend: TrendResponse;
};

export type BriefResponse = {
  period: Period;
  mode: "model" | "records";
  lines: string[];
  notices: string[];
  suggested_question: string | null;
};

export type SupplierDetail = {
  supplier: SupplierIntel;
  open_by_family: Record<string, number>;
  missing_document_count: number;
  recent_exceptions: Array<{
    issue_id: string;
    transaction_id: string;
    transaction_name: string;
    family: string;
    title: string;
    item_description: string | null;
    severity: Severity;
    status: "open" | "resolved";
    created_at: string;
    variance: FinancialVariance | null;
  }>;
  pattern_summary: { sentence: string; window: number } | null;
  patterns: PatternSignal[];
  anomalies: AnomalySignal[];
  transactions: Array<{
    id: string;
    name: string;
    status: TransactionStatus;
    created_at: string;
    open_issue_count: number;
    currency: string | null;
    total: number | null;
    missing_document_types: string[];
  }>;
};

export type AttentionEvent = {
  id: string;
  event_type: string;
  title: string;
  message: string;
  severity: Severity;
  entity_type: "transaction" | "supplier" | "workspace";
  entity_id: string | null;
  entity_label: string | null;
  transaction_id: string | null;
  created_at: string;
  seen_at: string | null;
};

export type AttentionList = {
  active_count: number;
  unseen_count: number;
  events: AttentionEvent[];
};

export type IntelligenceSettings = {
  high_value_variance_amount: number;
  high_variance_percentage: number;
  recurring_issue_min_count: number;
  recurring_issue_period_days: number;
  overdue_review_days: number;
  low_confidence_threshold: number;
  is_default: boolean;
};

export const FAMILY_LABEL: Record<string, string> = {
  price: "Price",
  quantity: "Quantity",
  supplier: "Supplier name",
  currency: "Currency",
  arithmetic: "Line arithmetic",
  missing_item: "Missing item",
  unexpected_item: "Unexpected item",
  missing_line_items: "Line items",
  other: "Other",
};

export const PERIOD_LABEL: Record<Period, string> = {
  "7d": "7 days",
  "30d": "30 days",
  "90d": "90 days",
  all: "All time",
};

// ---------------------------------------------------------------------------
// Phase 7 — identity, workspaces, history, documents, audit
// ---------------------------------------------------------------------------

export type DocumentType = "purchase_order" | "delivery_order" | "invoice" | "receipt";
export type DocumentStatus = "uploaded" | "processing" | "extracted" | "needs_review" | "failed";

export type UserRecord = { id: string; email: string; display_name: string };

export type WorkspaceSummary = {
  id: string;
  name: string;
  role: "owner" | "member";
  is_demo: boolean;
  is_legacy: boolean;
};

export type SessionInfo = {
  user: UserRecord;
  workspaces: WorkspaceSummary[];
  csrf_token: string;
};

export type MemberRecord = {
  user_id: string;
  email: string;
  display_name: string;
  role: "owner" | "member";
  joined_at: string;
};

export type DocumentRecord = {
  id: string;
  filename: string;
  document_type: DocumentType;
  mime_type: string;
  size_bytes: number;
  page_count: number | null;
  status: DocumentStatus;
  error_code: string | null;
  has_source_file: boolean;
  created_at: string;
};

export type EvidenceItem = {
  field_path: string;
  source_text: string;
  page: number | null;
  confidence: number;
};

export type ExtractionResult = {
  document_id: string;
  filename: string;
  model: string;
  supplier_name: string | null;
  supplier_registration_no: string | null;
  document_number: string | null;
  document_date: string | null;
  currency: string | null;
  subtotal: number | null;
  tax: number | null;
  total: number | null;
  overall_confidence: number;
  line_items: Array<{
    description: string;
    sku: string | null;
    quantity: number | null;
    unit_price: number | null;
    line_total: number | null;
  }>;
  evidence: EvidenceItem[];
  review_reasons: string[];
};

export type TransactionDocumentSummary = {
  id: string;
  filename: string;
  document_type: DocumentType;
  status: string;
  document_number: string | null;
  supplier_name: string | null;
  currency: string | null;
  total: number | null;
  overall_confidence: number | null;
};

export type EvidenceReference = {
  document_id: string;
  filename: string;
  document_type: DocumentType;
  field_path: string;
  source_text: string;
  page: number | null;
  value: string | null;
};

export type ReconciliationIssue = {
  code: string;
  title: string;
  severity: Severity;
  item_description: string | null;
  expected: string | null;
  actual: string | null;
  delta: string | null;
  explanation: string;
  sources: EvidenceReference[];
};

export type ReconciliationResult = {
  transaction_id: string;
  status: "matched" | "review_required" | "insufficient_data";
  documents: TransactionDocumentSummary[];
  summary: {
    issue_count: number;
    high: number;
    medium: number;
    low: number;
    matched_lines: number;
    review_lines: number;
  };
  lines: Array<{
    key: string;
    description: string;
    sku: string | null;
    po_quantity: number | null;
    delivered_quantity: number | null;
    invoice_quantity: number | null;
    po_unit_price: number | null;
    invoice_unit_price: number | null;
    status: "matched" | "review_required" | "unmatched";
  }>;
  issues: ReconciliationIssue[];
  generated_at: string;
};

export type HistoryItem = {
  id: string;
  name: string;
  status: TransactionStatus;
  created_at: string;
  updated_at: string;
  document_count: number;
  issue_count: number;
  open_issue_count: number;
  resolved_issue_count: number;
  high_count: number;
  supplier_name: string | null;
  currency: string | null;
  total: number | null;
};

export type TransactionPage = { items: HistoryItem[]; total: number; limit: number; offset: number };

export type TransactionDetail = {
  id: string;
  name: string;
  status: TransactionStatus;
  created_at: string;
  updated_at: string;
  documents: TransactionDocumentSummary[];
  review: {
    issue_count: number;
    open_issue_count: number;
    resolved_issue_count: number;
    high_count: number;
  };
};

export type ReviewIssue = ReconciliationIssue & {
  id: string;
  transaction_id: string;
  issue_key: string;
  status: "open" | "resolved";
  resolution_note: string | null;
  resolved_by_name: string | null;
  active: boolean;
  resolved_at: string | null;
  created_at: string;
  updated_at: string;
};

/** Evidence with confidence, from GET /transactions/{id}/context. */
export type ContextSource = {
  id: string;
  label: string;
  document_id: string;
  filename: string;
  document_type: DocumentType;
  document_number: string | null;
  page: number | null;
  field_path: string;
  value: string | null;
  source_text: string;
  confidence: number | null;
  snippet_available: boolean;
  has_source_file: boolean;
};

export type TransactionContext = {
  issues: Array<{
    issue_id: string;
    source_ids: string[];
    variance: {
      kind: "money" | "quantity";
      currency: string | null;
      delta: number;
      percentage: number | null;
      billed_impact: number | null;
    } | null;
  }>;
  sources: ContextSource[];
};

export type AuditEvent = {
  id: string;
  action: string;
  entity_type: string;
  entity_id: string | null;
  actor_name: string | null;
  metadata: Record<string, unknown>;
  created_at: string;
};

export type AuditPage = { items: AuditEvent[]; next_before: string | null };
export type SupplierPage = { items: SupplierIntel[]; total: number; limit: number; offset: number };

export type TransactionRecordLite = {
  id: string;
  name: string;
  status: TransactionStatus;
  created_at: string;
  updated_at: string;
};
