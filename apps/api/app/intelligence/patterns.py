"""Deterministic recurring-pattern detection.

A pattern is the same kind of exception recurring at least
``recurring_issue_min_count`` times within ``recurring_issue_period_days``
**across at least two transactions**. Several exceptions on one transaction are
one event, not a pattern.

Rules
-----
repeated_<family>        same supplier + same issue family (price, supplier
                         name, currency, arithmetic, missing/unexpected items)
repeated_invoice_over_delivery
                         same supplier, invoiced quantity > delivered quantity
repeated_short_delivery  same supplier, delivered quantity < ordered quantity
repeated_item_price_mismatch
                         same line item (SKU or description) price-mismatched
repeated_missing_document
                         same required document type missing from reconciliation
                         sets older than one day
repeated_unresolved_supplier_issues
                         same supplier with open issues on several transactions
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta

from app.intelligence.dataset import IntelDataset, IssueFact, TxnFact
from app.schemas import IntelligenceSettings, PatternSignal, RelatedTransaction
from app.services.ask_queries import FAMILY_LABELS, SEVERITY_RANK, parse_amount
from app.services.reconciliation import _normalise_text

FAMILY_PATTERN_TYPES = {
    "price": ("repeated_price_discrepancy", "price discrepancies"),
    "supplier": ("repeated_supplier_mismatch", "supplier name mismatches"),
    "currency": ("repeated_currency_mismatch", "currency mismatches"),
    "arithmetic": ("repeated_line_arithmetic_error", "invoice line arithmetic errors"),
    "missing_item": ("repeated_missing_item", "missing line items"),
    "unexpected_item": ("repeated_unexpected_item", "unexpected line items"),
    "missing_line_items": ("repeated_unreadable_line_items", "unreadable line-item sets"),
}
DOCUMENT_LABELS = {
    "purchase_order": "purchase order",
    "delivery_order": "delivery order",
    "invoice": "invoice",
}


def _highest(severities: list[str]) -> str:
    return min(severities, key=lambda value: SEVERITY_RANK.get(value, 9)) if severities else "medium"


def quantity_direction(issue: IssueFact) -> str | None:
    expected = parse_amount(issue.expected)
    actual = parse_amount(issue.actual)
    if expected is None or actual is None:
        return None
    if issue.code == "invoice_delivery_quantity_variance" and actual[1] > expected[1]:
        return "invoice_over_delivery"
    if issue.code == "delivery_quantity_variance" and actual[1] < expected[1]:
        return "short_delivery"
    return None


def _item_key(txn: TxnFact, issue: IssueFact) -> tuple[str, str] | None:
    if not issue.item_description:
        return None
    line = next((l for l in txn.lines if l.get("description") == issue.item_description), None)
    sku = (line or {}).get("sku")
    if sku:
        return f"sku-{_normalise_text(sku).replace(' ', '-')}", f"{issue.item_description} ({sku})"
    key = _normalise_text(issue.item_description).replace(" ", "-")
    return (f"item-{key}", issue.item_description) if key else None


def _build(
    *,
    pattern_type: str,
    key_suffix: str,
    title: str,
    pairs: list[tuple[TxnFact, IssueFact]],
    settings: IntelligenceSettings,
    supplier: str | None,
    supplier_key: str | None,
    family: str | None,
    item: str | None,
    severity: str | None = None,
) -> PatternSignal:
    transactions: dict = {}
    for txn, _ in sorted(pairs, key=lambda pair: pair[1].created_at):
        transactions.setdefault(txn.id, txn)
    moments = [issue.created_at for _, issue in pairs]
    return PatternSignal(
        key=f"{pattern_type}:{key_suffix}",
        pattern_type=pattern_type,
        title=title,
        supplier=supplier,
        supplier_key=supplier_key,
        issue_family=family,
        item=item,
        count=len(pairs),
        threshold=settings.recurring_issue_min_count,
        period_days=settings.recurring_issue_period_days,
        transaction_ids=list(transactions),
        related_transactions=[RelatedTransaction(id=t.id, name=t.name) for t in transactions.values()],
        issue_ids=[issue.id for _, issue in pairs],
        first_seen=min(moments),
        last_seen=max(moments),
        severity=severity or _highest([issue.severity for _, issue in pairs]),
    )


def detect_patterns(
    dataset: IntelDataset,
    settings: IntelligenceSettings,
    now: datetime,
    *,
    supplier_key: str | None = None,
) -> list[PatternSignal]:
    minimum = settings.recurring_issue_min_count
    days = settings.recurring_issue_period_days
    since = now - timedelta(days=days)
    in_window = [
        (txn, issue)
        for txn, issue in dataset.issue_pairs()
        if since <= issue.created_at <= now
        and (supplier_key is None or txn.supplier_key == supplier_key)
    ]

    def recurring(pairs: list[tuple[TxnFact, IssueFact]]) -> bool:
        return len(pairs) >= minimum and len({txn.id for txn, _ in pairs}) >= 2

    patterns: list[PatternSignal] = []

    by_supplier_family: dict[tuple[str, str], list] = defaultdict(list)
    by_supplier_direction: dict[tuple[str, str], list] = defaultdict(list)
    by_item: dict[str, list] = defaultdict(list)
    item_labels: dict[str, str] = {}
    for txn, issue in in_window:
        key = txn.supplier_key
        if key and issue.family in FAMILY_PATTERN_TYPES:
            by_supplier_family[(key, issue.family)].append((txn, issue))
        if key and issue.family == "quantity":
            direction = quantity_direction(issue)
            if direction:
                by_supplier_direction[(key, direction)].append((txn, issue))
        if issue.family == "price":
            item = _item_key(txn, issue)
            if item:
                by_item[item[0]].append((txn, issue))
                item_labels.setdefault(item[0], item[1])

    for (key, family), pairs in by_supplier_family.items():
        if not recurring(pairs):
            continue
        pattern_type, noun = FAMILY_PATTERN_TYPES[family]
        supplier = pairs[0][0].supplier
        patterns.append(
            _build(
                pattern_type=pattern_type,
                key_suffix=key,
                title=f"{supplier} generated {len(pairs)} {noun} in the last {days} days.",
                pairs=pairs,
                settings=settings,
                supplier=supplier,
                supplier_key=key,
                family=family,
                item=None,
            )
        )

    for (key, direction), pairs in by_supplier_direction.items():
        if not recurring(pairs):
            continue
        supplier = pairs[0][0].supplier
        if direction == "invoice_over_delivery":
            pattern_type = "repeated_invoice_over_delivery"
            title = (
                f"Invoiced quantity exceeded delivered quantity {len(pairs)} times for "
                f"{supplier} in the last {days} days."
            )
        else:
            pattern_type = "repeated_short_delivery"
            title = (
                f"Delivered quantity was below the ordered quantity {len(pairs)} times for "
                f"{supplier} in the last {days} days."
            )
        patterns.append(
            _build(
                pattern_type=pattern_type,
                key_suffix=key,
                title=title,
                pairs=pairs,
                settings=settings,
                supplier=supplier,
                supplier_key=key,
                family="quantity",
                item=None,
            )
        )

    for item_key, pairs in by_item.items():
        if not recurring(pairs):
            continue
        suppliers = {txn.supplier_key for txn, _ in pairs}
        single = pairs[0][0] if len(suppliers) == 1 else None
        label = item_labels[item_key]
        patterns.append(
            _build(
                pattern_type="repeated_item_price_mismatch",
                key_suffix=item_key,
                title=(
                    f"“{label}” was price-mismatched on {len({t.id for t, _ in pairs})} "
                    f"transactions in the last {days} days."
                ),
                pairs=pairs,
                settings=settings,
                supplier=single.supplier if single else None,
                supplier_key=single.supplier_key if single else None,
                family="price",
                item=label,
            )
        )

    # Missing documents are transaction-level, not issue-level.
    stale_before = now - timedelta(days=1)
    missing: dict[str, list[TxnFact]] = defaultdict(list)
    for txn in dataset.transactions:
        if supplier_key is not None and txn.supplier_key != supplier_key:
            continue
        if not (since <= txn.created_at <= stale_before) or txn.reconciled:
            continue
        for document_type in txn.missing_document_types:
            missing[document_type].append(txn)
    for document_type, txns in missing.items():
        if len(txns) < minimum:
            continue
        label = DOCUMENT_LABELS.get(document_type, document_type)
        moments = [txn.created_at for txn in txns]
        patterns.append(
            PatternSignal(
                key=f"repeated_missing_document:{document_type}"
                + (f":{supplier_key}" if supplier_key else ""),
                pattern_type="repeated_missing_document",
                title=(
                    f"{len(txns)} transactions created in the last {days} days are still "
                    f"missing {'an' if label[0] in 'aeiou' else 'a'} {label}."
                ),
                supplier=None,
                supplier_key=supplier_key,
                issue_family=None,
                item=label,
                count=len(txns),
                threshold=minimum,
                period_days=days,
                transaction_ids=[txn.id for txn in txns],
                related_transactions=[RelatedTransaction(id=t.id, name=t.name) for t in txns],
                issue_ids=[],
                first_seen=min(moments),
                last_seen=max(moments),
                severity="medium",
            )
        )

    # Supplier with unresolved issues spread across several transactions.
    backlog: dict[str, list] = defaultdict(list)
    for txn, issue in in_window:
        if issue.is_open and txn.supplier_key:
            backlog[txn.supplier_key].append((txn, issue))
    for key, pairs in backlog.items():
        if not recurring(pairs):
            continue
        supplier = pairs[0][0].supplier
        txn_count = len({txn.id for txn, _ in pairs})
        patterns.append(
            _build(
                pattern_type="repeated_unresolved_supplier_issues",
                key_suffix=key,
                title=(
                    f"{supplier} has {len(pairs)} unresolved exceptions across "
                    f"{txn_count} transactions from the last {days} days."
                ),
                pairs=pairs,
                settings=settings,
                supplier=supplier,
                supplier_key=key,
                family=None,
                item=None,
            )
        )

    patterns.sort(
        key=lambda p: (SEVERITY_RANK.get(p.severity, 9), -p.count, -p.last_seen.timestamp())
    )
    return patterns


def family_label(family: str | None) -> str:
    return FAMILY_LABELS.get(family or "", family or "exception")
