"""Supplier-level operational intelligence.

Suppliers are stored only as extracted names, so identity is the normalised
name (see ``dataset.supplier_key``): punctuation and company suffixes such as
"Sdn. Bhd." are ignored, nothing else is merged. A transaction belongs to the
supplier named on its purchase order.

issue_rate = transactions with at least one active exception
             ÷ reconciled transactions × 100
"""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime

from app.intelligence.anomalies import detect_anomalies
from app.intelligence.dataset import IntelDataset, TxnFact, Window, supplier_key
from app.intelligence.patterns import detect_patterns
from app.intelligence.variance import (
    average_abs_percentage,
    currency_totals,
    financial_variance,
    issue_percentage,
)
from app.schemas import (
    DocumentType,
    IntelligenceSettings,
    SupplierDetail,
    SupplierException,
    SupplierIntel,
    SupplierPatternSummary,
    SupplierTransaction,
)
from app.services.ask_queries import FAMILY_LABELS

PATTERN_SUMMARY_WINDOW = 5
PATTERN_SUMMARY_MINIMUM = 3


def _group(dataset: IntelDataset, window: Window | None) -> dict[str, list[TxnFact]]:
    groups: dict[str, list[TxnFact]] = defaultdict(list)
    for txn in dataset.transactions:
        if txn.supplier_key is None:
            continue
        if window is not None and not window.contains(txn.created_at):
            continue
        groups[txn.supplier_key].append(txn)
    return groups


def _intel(key: str, txns: list[TxnFact]) -> SupplierIntel:
    names = Counter(txn.supplier for txn in txns if txn.supplier)
    variants = sorted(
        {
            doc.supplier_name
            for txn in txns
            for doc in txn.documents
            if doc.supplier_name and supplier_key(doc.supplier_name) == key
        }
    )
    issues = [(txn, issue) for txn in txns for issue in txn.issues]
    open_pairs = [(t, i) for t, i in issues if i.is_open]
    reconciled = [txn for txn in txns if txn.reconciled]
    with_issues = [txn for txn in reconciled if txn.issues]
    variances = [v for t, i in open_pairs if (v := financial_variance(t, i)) is not None]
    resolution_hours = [
        (i.resolved_at - i.created_at).total_seconds() / 3600
        for _, i in issues
        if i.resolved_at is not None
    ]
    return SupplierIntel(
        supplier_key=key,
        supplier_name=names.most_common(1)[0][0] if names else key,
        name_variants=variants,
        transaction_count=len(txns),
        reconciled_transaction_count=len(reconciled),
        transactions_with_issues=len(with_issues),
        open_issue_count=len(open_pairs),
        resolved_issue_count=sum(1 for _, i in issues if i.status == "resolved"),
        issue_rate=round(len(with_issues) / len(reconciled) * 100, 1) if reconciled else None,
        price_discrepancy_count=sum(1 for _, i in open_pairs if i.family == "price"),
        quantity_discrepancy_count=sum(1 for _, i in open_pairs if i.family == "quantity"),
        supplier_mismatch_count=sum(1 for _, i in open_pairs if i.family == "supplier"),
        total_variance_amount=currency_totals(variances),
        average_variance_percentage=average_abs_percentage(
            [issue_percentage(i) for _, i in issues if i.family == "price"]
        ),
        average_resolution_time_hours=round(sum(resolution_hours) / len(resolution_hours), 1)
        if resolution_hours
        else None,
        last_transaction_at=max((txn.created_at for txn in txns), default=None),
    )


def supplier_intelligence(
    dataset: IntelDataset, window: Window | None = None
) -> list[SupplierIntel]:
    rows = [_intel(key, txns) for key, txns in _group(dataset, window).items()]
    rows.sort(
        key=lambda row: (
            -row.open_issue_count,
            -(row.issue_rate or 0),
            -row.transaction_count,
            row.supplier_name.lower(),
        )
    )
    return rows


def _pattern_summary(exceptions: list[SupplierException]) -> SupplierPatternSummary | None:
    recent = exceptions[:PATTERN_SUMMARY_WINDOW]
    if len(recent) < PATTERN_SUMMARY_MINIMUM:
        return None
    families = Counter(item.family for item in recent)
    family, count = families.most_common(1)[0]
    price_above = sum(
        1
        for item in recent
        if item.family == "price"
        and item.variance is not None
        and item.variance.signed_variance > 0
    )
    k = len(recent)
    if price_above >= 2:
        sentence = (
            f"{price_above} of the last {k} exceptions involved invoice unit prices higher "
            "than the corresponding purchase order."
        )
    elif count >= 2:
        sentence = f"{count} of the last {k} exceptions were {FAMILY_LABELS.get(family, family)}es." \
            if FAMILY_LABELS.get(family, family).endswith("mismatch") \
            else f"{count} of the last {k} exceptions were {FAMILY_LABELS.get(family, family)}s."
    else:
        sentence = f"No exception type repeats among the last {k} exceptions."
    return SupplierPatternSummary(
        window=k,
        exceptions_considered=k,
        most_common_family=family if count >= 2 else None,
        most_common_count=count,
        price_above_po_count=price_above,
        sentence=sentence,
    )


def supplier_detail(
    dataset: IntelDataset,
    settings: IntelligenceSettings,
    now: datetime,
    key_or_name: str,
) -> SupplierDetail | None:
    key = key_or_name if key_or_name in {t.supplier_key for t in dataset.transactions} else supplier_key(key_or_name)
    txns = [txn for txn in dataset.transactions if key and txn.supplier_key == key]
    if not txns:
        return None

    intel = _intel(key, txns)
    exceptions = sorted(
        (
            SupplierException(
                issue_id=issue.id,
                transaction_id=txn.id,
                transaction_name=txn.name,
                family=issue.family,
                title=issue.title,
                item_description=issue.item_description,
                severity=issue.severity,
                status=issue.status,
                created_at=issue.created_at,
                variance=financial_variance(txn, issue),
            )
            for txn in txns
            for issue in txn.issues
        ),
        key=lambda item: item.created_at,
        reverse=True,
    )
    open_by_family = Counter(i.family for txn in txns for i in txn.open_issues)
    return SupplierDetail(
        supplier=intel,
        open_by_family=dict(open_by_family),
        missing_document_count=sum(1 for txn in txns if txn.missing_document_types),
        recent_exceptions=exceptions[:10],
        pattern_summary=_pattern_summary(exceptions),
        patterns=detect_patterns(dataset, settings, now, supplier_key=key),
        anomalies=detect_anomalies(dataset, settings, now, supplier_key=key),
        transactions=[
            SupplierTransaction(
                id=txn.id,
                name=txn.name,
                status=txn.status,
                created_at=txn.created_at,
                open_issue_count=len(txn.open_issues),
                currency=txn.currency,
                total=txn.invoice.total if txn.invoice else None,
                missing_document_types=[DocumentType(t) for t in txn.missing_document_types],
            )
            for txn in sorted(txns, key=lambda t: t.created_at, reverse=True)
        ],
    )
