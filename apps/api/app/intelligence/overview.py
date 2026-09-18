"""Operations overview.

Two kinds of numbers, kept distinct:

* **Current state** (period-independent): open exceptions, severities, active
  variance, transactions needing review. Resolved and inactive issues are
  never counted as current exceptions.
* **Period activity** (7d / 30d / 90d / all): exceptions created and resolved,
  resolution rate and time, compared with the previous period of equal length.

Resolution rate = issues created in the period that are now resolved ÷ issues
created in the period. Resolution time = resolved_at − created_at.
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime
from statistics import median

from app.intelligence.anomalies import detect_anomalies
from app.intelligence.dataset import IntelDataset, Window, window_for
from app.intelligence.patterns import detect_patterns
from app.intelligence.priority import age_days, priority_queue
from app.intelligence.suppliers import supplier_intelligence
from app.intelligence.trends import exception_trend
from app.intelligence.variance import (
    average_abs_percentage,
    currency_totals,
    billed_percentage,
    financial_variance,
)
from app.schemas import (
    DataQualityCheck,
    DataQualitySummary,
    IntelligenceSettings,
    IssueMixRow,
    OverviewMetrics,
    OverviewResponse,
    PeriodActivity,
    ResolutionPerformance,
)
from app.services.ask_queries import FAMILY_LABELS

DATA_QUALITY_LABELS = {
    "low_confidence": "Low-confidence extraction",
    "missing_supplier": "Missing supplier name",
    "missing_document_number": "Missing document number",
    "missing_date": "Missing document date",
    "missing_currency": "Missing currency",
    "missing_evidence": "No source evidence captured",
}
CURRENCY_BEARING = {"purchase_order", "invoice", "receipt"}


def _hours(issue) -> float:
    return (issue.resolved_at - issue.created_at).total_seconds() / 3600


def resolution_performance(
    dataset: IntelDataset, settings: IntelligenceSettings, window: Window, now: datetime
) -> ResolutionPerformance:
    issues = [issue for _, issue in dataset.issue_pairs()]
    created = [i for i in issues if window.contains(i.created_at)]
    resolved_in_period = [i for i in issues if window.contains(i.resolved_at)]
    hours = [_hours(i) for i in resolved_in_period]
    open_ages = [age_days(i, now) for i in issues if i.is_open]
    return ResolutionPerformance(
        period=window.period,
        issues_created=len(created),
        issues_resolved=len(resolved_in_period),
        resolution_rate=round(sum(1 for i in created if i.resolved_at) / len(created) * 100, 1)
        if created
        else None,
        median_resolution_hours=round(median(hours), 1) if hours else None,
        average_resolution_hours=round(sum(hours) / len(hours), 1) if hours else None,
        oldest_open_issue_age_days=round(max(open_ages), 1) if open_ages else None,
        overdue_issue_count=sum(1 for age in open_ages if age >= settings.overdue_review_days),
    )


def data_quality(dataset: IntelDataset, settings: IntelligenceSettings) -> DataQualitySummary:
    """Extraction completeness — separate from business reconciliation issues."""
    flagged: dict[str, list] = {check: [] for check in DATA_QUALITY_LABELS}
    checked = 0
    for txn in dataset.transactions:
        for doc in txn.documents:
            if not doc.extracted:
                continue
            checked += 1
            if doc.overall_confidence is not None and doc.overall_confidence < settings.low_confidence_threshold:
                flagged["low_confidence"].append(doc.id)
            if not doc.supplier_name:
                flagged["missing_supplier"].append(doc.id)
            if not doc.document_number:
                flagged["missing_document_number"].append(doc.id)
            if not doc.document_date:
                flagged["missing_date"].append(doc.id)
            if doc.document_type in CURRENCY_BEARING and not doc.currency:
                flagged["missing_currency"].append(doc.id)
            if not doc.evidence_count:
                flagged["missing_evidence"].append(doc.id)
    affected = {doc_id for ids in flagged.values() for doc_id in ids}
    return DataQualitySummary(
        documents_checked=checked,
        affected_document_count=len(affected),
        unmatched_line_count=sum(
            1 for txn in dataset.transactions for line in txn.lines if line.get("status") == "unmatched"
        ),
        checks=[
            DataQualityCheck(check=check, label=DATA_QUALITY_LABELS[check], count=len(ids), document_ids=ids[:20])
            for check, ids in flagged.items()
            if ids
        ],
    )


def diverse(signals: list, limit: int) -> list:
    """Keep ranking order but show one of each signal type before repeats."""
    seen: set[str] = set()
    first, rest = [], []
    for signal in signals:
        (rest if signal.signal in seen else first).append(signal)
        seen.add(signal.signal)
    return (first + rest)[:limit]


def compute_overview(
    dataset: IntelDataset,
    settings: IntelligenceSettings,
    period: str,
    now: datetime,
) -> OverviewResponse:
    window = window_for(period, now)
    pairs = dataset.issue_pairs()
    open_pairs = [(t, i) for t, i in pairs if i.is_open]
    open_families = Counter(i.family for _, i in open_pairs)
    severities = Counter(i.severity for _, i in open_pairs)
    variances = [v for t, i in open_pairs if (v := financial_variance(t, i)) is not None]
    resolution = resolution_performance(dataset, settings, window, now)

    created = [(t, i) for t, i in pairs if window.contains(i.created_at)]
    previous = (
        [(t, i) for t, i in pairs if window.in_previous(i.created_at)]
        if window.start is not None
        else None
    )

    metrics = OverviewMetrics(
        total_transactions=len(dataset.transactions),
        transactions_needing_review=sum(1 for t in dataset.transactions if t.open_issues),
        open_issue_count=len(open_pairs),
        resolved_issue_count=sum(1 for _, i in pairs if i.status == "resolved"),
        high_severity_issue_count=severities.get("high", 0),
        medium_severity_issue_count=severities.get("medium", 0),
        low_severity_issue_count=severities.get("low", 0),
        total_active_variance_amount=currency_totals(variances),
        average_variance_percentage=average_abs_percentage(
            [billed_percentage(i) for _, i in open_pairs]
        ),
        price_discrepancy_count=open_families.get("price", 0),
        quantity_discrepancy_count=open_families.get("quantity", 0),
        supplier_mismatch_count=open_families.get("supplier", 0),
        currency_mismatch_count=open_families.get("currency", 0),
        missing_document_count=sum(1 for t in dataset.transactions if t.missing_document_types),
        overdue_issue_count=resolution.overdue_issue_count,
        resolution_rate=resolution.resolution_rate,
        average_resolution_time_hours=resolution.average_resolution_hours,
    )

    activity = PeriodActivity(
        period=period,
        start=window.start,
        end=window.end,
        previous_start=window.previous_start,
        transactions_created=sum(1 for t in dataset.transactions if window.contains(t.created_at)),
        issues_created=len(created),
        issues_resolved=resolution.issues_resolved,
        created_by_family=dict(Counter(i.family for _, i in created)),
        previous_issues_created=len(previous) if previous is not None else None,
        previous_created_by_family=dict(Counter(i.family for _, i in previous))
        if previous is not None
        else None,
    )

    total_open = len(open_pairs)
    issue_mix = [
        IssueMixRow(
            family=family,
            label=FAMILY_LABELS.get(family, family),
            count=count,
            share=round(count / total_open * 100, 1),
        )
        for family, count in sorted(open_families.items(), key=lambda item: (-item[1], item[0]))
    ]

    patterns = detect_patterns(dataset, settings, now)
    return OverviewResponse(
        as_of=now,
        period=period,
        has_data=not dataset.is_empty,
        has_reconciled_data=any(t.reconciled for t in dataset.transactions),
        metrics=metrics,
        activity=activity,
        resolution=resolution,
        issue_mix=issue_mix,
        priority=priority_queue(dataset, settings, now, patterns, limit=8),
        suppliers=supplier_intelligence(dataset)[:6],
        patterns=patterns[:8],
        anomalies=diverse(detect_anomalies(dataset, settings, now), 6),
        data_quality=data_quality(dataset, settings),
        trend=exception_trend(dataset, period, now),
    )
