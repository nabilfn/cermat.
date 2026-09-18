// Shared types and formatting for the Overview, supplier detail and attention queue.

export const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

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

export async function getJson<T>(path: string): Promise<T> {
  const response = await fetch(`${API_URL}${path}`);
  const payload = await response.json().catch(() => null);
  if (!response.ok) {
    throw new Error(payload?.detail ?? "The cermat. API returned an error.");
  }
  return payload as T;
}

export function money(currency: string | null, value: number, signed = false) {
  const sign = signed ? (value > 0 ? "+" : value < 0 ? "−" : "") : "";
  const amount = Math.abs(value).toLocaleString(undefined, {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  });
  return `${sign}${currency ? `${currency} ` : ""}${amount}`;
}

export function percent(value: number | null, signed = false) {
  if (value === null) return "—";
  const sign = signed ? (value > 0 ? "+" : value < 0 ? "−" : "") : "";
  return `${sign}${Math.abs(value).toFixed(1)}%`;
}

export function duration(hours: number | null) {
  if (hours === null) return "—";
  if (hours < 1) return `${Math.round(hours * 60)} min`;
  if (hours < 48) return `${hours.toFixed(1)} h`;
  return `${(hours / 24).toFixed(1)} d`;
}

export function dateLabel(value: string) {
  return new Intl.DateTimeFormat(undefined, { day: "2-digit", month: "short" }).format(
    new Date(value)
  );
}

export function relativeTime(value: string, now = Date.now()) {
  const minutes = Math.round((now - new Date(value).getTime()) / 60000);
  if (minutes < 1) return "just now";
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.round(hours / 24);
  return days === 1 ? "yesterday" : `${days}d ago`;
}

export function observed(signal: AnomalySignal, value: number | null) {
  if (value === null) return "—";
  switch (signal.unit) {
    case "percent":
      return percent(value, true);
    case "amount":
      return money(signal.currency, value);
    case "days":
      return `${value.toFixed(1)} d`;
    default:
      return String(Math.round(value * 100) / 100);
  }
}
