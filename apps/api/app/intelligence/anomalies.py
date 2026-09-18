"""Explainable, rule-based anomaly signals. No opaque models.

Every signal reports what was observed, the baseline it was compared with
(or why no baseline exists), the configured threshold, and a plain reason.

high_variance_percentage   open issue whose |variance %| ≥ high_variance_percentage;
                           baseline = supplier's median |variance %| for that issue type
high_value_variance        open issue whose |billed variance| ≥ high_value_variance_amount;
                           baseline = supplier's median |billed variance| (same currency)
amount_above_supplier_median
                           invoice total > supplier's median prior invoice total ×
                           amount_median_multiplier (needs min_history_for_baseline
                           prior invoices in the same currency)
issue_frequency_spike      exceptions created in the last 30 days ≥ issue_spike_ratio ×
                           the previous 30 days, and ≥ recurring_issue_min_count;
                           only when there are 60+ days of history
new_supplier_high_severity supplier first seen in the last 30 days (≤ 2 transactions)
                           with an open high-severity exception
long_unresolved_issue      open issue older than 2 × overdue_review_days;
                           baseline = median resolution time
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta
from statistics import median

from app.intelligence.dataset import IntelDataset, IssueFact, TxnFact
from app.intelligence.priority import age_days
from app.intelligence.variance import billed_percentage, financial_variance
from app.schemas import AnomalySignal, IntelligenceSettings, RelatedTransaction
from app.services.ask_queries import FAMILY_LABELS, SEVERITY_RANK

RECENT_DAYS = 30
FAMILY_PHRASES = {
    "price": "invoice unit price differs from the purchase order by",
    "quantity": "quantity differs by",
    "arithmetic": "invoice line total differs from quantity × unit price by",
}


def _related(*txns: TxnFact) -> list[RelatedTransaction]:
    seen: dict = {}
    for txn in txns:
        seen.setdefault(txn.id, RelatedTransaction(id=txn.id, name=txn.name))
    return list(seen.values())


def _signed(value: float, suffix: str = "") -> str:
    return f"{'+' if value > 0 else '−' if value < 0 else ''}{abs(value):,.2f}{suffix}"


def detect_anomalies(
    dataset: IntelDataset,
    settings: IntelligenceSettings,
    now: datetime,
    *,
    supplier_key: str | None = None,
) -> list[AnomalySignal]:
    minimum_history = settings.min_history_for_baseline
    signals: list[AnomalySignal] = []

    def scoped(txn: TxnFact) -> bool:
        return supplier_key is None or txn.supplier_key == supplier_key

    pairs = dataset.issue_pairs()

    # ---- variance percentage and value ------------------------------------
    pct_history: dict[tuple[str | None, str], list[tuple]] = defaultdict(list)
    value_history: dict[tuple[str | None, str | None], list[tuple]] = defaultdict(list)
    for txn, issue in pairs:
        pct = billed_percentage(issue)
        if pct is not None:
            pct_history[(txn.supplier_key, issue.family)].append((issue.id, abs(pct)))
        variance = financial_variance(txn, issue)
        if variance is not None:
            value_history[(txn.supplier_key, variance.currency)].append(
                (issue.id, variance.absolute_variance)
            )

    for txn, issue in pairs:
        if not issue.is_open or not scoped(txn):
            continue
        supplier = txn.supplier or "this supplier"

        pct = billed_percentage(issue)
        if pct is not None and abs(pct) >= settings.high_variance_percentage:
            others = [v for iid, v in pct_history[(txn.supplier_key, issue.family)] if iid != issue.id]
            baseline = round(median(others), 2) if len(others) >= minimum_history else None
            label = FAMILY_LABELS.get(issue.family, issue.family)
            baseline_text = (
                f"{supplier}'s median {label} variance: {baseline:.2f}%."
                if baseline is not None
                else f"Not enough {supplier} history for a baseline "
                f"({len(others)} of {minimum_history} prior exceptions)."
            )
            signals.append(
                AnomalySignal(
                    key=f"high_variance_percentage:{issue.id}",
                    signal="high_variance_percentage",
                    title=f"{_signed(pct, '%')} {label} on {txn.name}",
                    severity="high" if abs(pct) >= 2 * settings.high_variance_percentage else "medium",
                    observed_value=pct,
                    baseline=baseline,
                    baseline_label=f"{supplier} median |variance %|" if baseline is not None else "No baseline yet",
                    threshold=settings.high_variance_percentage,
                    unit="percent",
                    currency=None,
                    reason=(
                        f"The {FAMILY_PHRASES.get(issue.family, 'value differs by')} "
                        f"{_signed(pct, '%')}. {baseline_text} "
                        f"Configured review threshold: {settings.high_variance_percentage:g}%."
                    ),
                    supplier=txn.supplier,
                    supplier_key=txn.supplier_key,
                    related_transactions=_related(txn),
                    related_issue_ids=[issue.id],
                )
            )

        variance = financial_variance(txn, issue)
        if variance is not None and variance.absolute_variance >= settings.high_value_variance_amount:
            others = [
                v
                for iid, v in value_history[(txn.supplier_key, variance.currency)]
                if iid != issue.id
            ]
            baseline = round(median(others), 2) if len(others) >= minimum_history else None
            money = f"{variance.currency + ' ' if variance.currency else ''}"
            signals.append(
                AnomalySignal(
                    key=f"high_value_variance:{issue.id}",
                    signal="high_value_variance",
                    title=f"{money}{_signed(variance.signed_variance)} billed variance on {txn.name}",
                    severity="high"
                    if variance.absolute_variance >= 2 * settings.high_value_variance_amount
                    else "medium",
                    observed_value=variance.absolute_variance,
                    baseline=baseline,
                    baseline_label=f"{supplier} median billed variance" if baseline is not None else "No baseline yet",
                    threshold=settings.high_value_variance_amount,
                    unit="amount",
                    currency=variance.currency,
                    reason=(
                        f"Billed variance is {money}{_signed(variance.signed_variance)}. "
                        + (
                            f"{supplier}'s median billed variance: {money}{baseline:,.2f}. "
                            if baseline is not None
                            else "Not enough supplier history for a baseline. "
                        )
                        + f"Configured high-value threshold: {money}{settings.high_value_variance_amount:,.2f}."
                    ),
                    supplier=txn.supplier,
                    supplier_key=txn.supplier_key,
                    related_transactions=_related(txn),
                    related_issue_ids=[issue.id],
                )
            )

    # ---- invoice total vs supplier median ---------------------------------
    since = now - timedelta(days=settings.recurring_issue_period_days)
    invoices: dict[tuple[str, str | None], list[tuple[TxnFact, float]]] = defaultdict(list)
    for txn in dataset.transactions:
        invoice = txn.invoice
        if txn.supplier_key and invoice and invoice.total is not None:
            invoices[(txn.supplier_key, (invoice.currency or "").upper() or None)].append(
                (txn, invoice.total)
            )
    for (key, currency), rows in invoices.items():
        rows.sort(key=lambda row: row[0].created_at)
        for index, (txn, total) in enumerate(rows):
            if not scoped(txn) or not (since <= txn.created_at <= now):
                continue
            prior = [value for _, value in rows[:index]]
            if len(prior) < minimum_history:
                continue
            baseline = round(median(prior), 2)
            threshold = round(baseline * settings.amount_median_multiplier, 2)
            if baseline <= 0 or total <= threshold:
                continue
            money = f"{currency + ' ' if currency else ''}"
            signals.append(
                AnomalySignal(
                    key=f"amount_above_supplier_median:{txn.id}",
                    signal="amount_above_supplier_median",
                    title=f"Invoice total on {txn.name} is {total / baseline:.1f}× the supplier median",
                    severity="medium",
                    observed_value=round(total, 2),
                    baseline=baseline,
                    baseline_label=f"Median of {len(prior)} prior {txn.supplier} invoices",
                    threshold=threshold,
                    unit="amount",
                    currency=currency,
                    reason=(
                        f"Invoice total is {money}{total:,.2f}. Median of the previous "
                        f"{len(prior)} {txn.supplier} invoices: {money}{baseline:,.2f}. "
                        f"Threshold ({settings.amount_median_multiplier:g}× median): {money}{threshold:,.2f}."
                    ),
                    supplier=txn.supplier,
                    supplier_key=key,
                    related_transactions=_related(txn),
                    related_issue_ids=[],
                )
            )

    # ---- sudden increase in exception frequency ---------------------------
    recent_start = now - timedelta(days=RECENT_DAYS)
    previous_start = recent_start - timedelta(days=RECENT_DAYS)

    def spike(scope_pairs: list[tuple[TxnFact, IssueFact]], earliest: datetime | None, label: str,
              key: str, supplier: str | None, skey: str | None) -> None:
        if earliest is None or earliest > previous_start:
            return  # not enough history to call anything a change
        current = [(t, i) for t, i in scope_pairs if recent_start <= i.created_at <= now]
        previous = [(t, i) for t, i in scope_pairs if previous_start <= i.created_at < recent_start]
        threshold = max(len(previous), 1) * settings.issue_spike_ratio
        if len(current) < settings.recurring_issue_min_count or len(current) < threshold:
            return
        week = now.isocalendar()
        signals.append(
            AnomalySignal(
                key=f"issue_frequency_spike:{key}:{week.year}-W{week.week:02d}",
                signal="issue_frequency_spike",
                title=f"Exceptions for {label} rose from {len(previous)} to {len(current)}",
                severity="medium",
                observed_value=len(current),
                baseline=len(previous),
                baseline_label=f"Exceptions in the previous {RECENT_DAYS} days",
                threshold=round(threshold, 2),
                unit="count",
                currency=None,
                reason=(
                    f"{len(current)} exceptions were created in the last {RECENT_DAYS} days "
                    f"against {len(previous)} in the {RECENT_DAYS} days before. "
                    f"Configured threshold: {settings.issue_spike_ratio:g}× the previous period "
                    f"and at least {settings.recurring_issue_min_count} exceptions."
                ),
                supplier=supplier,
                supplier_key=skey,
                related_transactions=_related(*(t for t, _ in current)),
                related_issue_ids=[i.id for _, i in current],
            )
        )

    first_seen: dict[str, datetime] = {}
    supplier_names: dict[str, str] = {}
    supplier_txns: dict[str, list[TxnFact]] = defaultdict(list)
    for txn in dataset.transactions:
        if txn.supplier_key:
            supplier_txns[txn.supplier_key].append(txn)
            supplier_names.setdefault(txn.supplier_key, txn.supplier or txn.supplier_key)
            if txn.supplier_key not in first_seen or txn.created_at < first_seen[txn.supplier_key]:
                first_seen[txn.supplier_key] = txn.created_at

    if supplier_key is None:
        earliest = min((t.created_at for t in dataset.transactions), default=None)
        spike(pairs, earliest, "the workspace", "workspace", None, None)
    for key, txns in supplier_txns.items():
        if supplier_key is not None and key != supplier_key:
            continue
        scope_pairs = [(t, i) for t in txns for i in t.issues]
        spike(scope_pairs, first_seen[key], supplier_names[key], key, supplier_names[key], key)

    # ---- new supplier with immediate high-severity exception --------------
    # Only meaningful once the workspace itself predates the window; in a new
    # workspace every supplier is "new".
    workspace_start = min((t.created_at for t in dataset.transactions), default=now)
    for key, txns in supplier_txns.items():
        if workspace_start >= recent_start:
            break
        if supplier_key is not None and key != supplier_key:
            continue
        if len(txns) > 2 or first_seen[key] < recent_start:
            continue
        high = [(t, i) for t in txns for i in t.open_issues if i.severity == "high"]
        if not high:
            continue
        signals.append(
            AnomalySignal(
                key=f"new_supplier_high_severity:{key}",
                signal="new_supplier_high_severity",
                title=f"New supplier {supplier_names[key]} has {len(high)} open high-severity exception"
                + ("s" if len(high) != 1 else ""),
                severity="high",
                observed_value=len(high),
                baseline=None,
                baseline_label=f"First transaction {first_seen[key].date().isoformat()}",
                threshold=1,
                unit="count",
                currency=None,
                reason=(
                    f"{supplier_names[key]} first appeared on {first_seen[key].date().isoformat()} "
                    f"({len(txns)} transaction{'s' if len(txns) != 1 else ''}) and already has "
                    f"{len(high)} open high-severity exception{'s' if len(high) != 1 else ''}. "
                    f"Rule: supplier first seen within {RECENT_DAYS} days with any open high-severity exception."
                ),
                supplier=supplier_names[key],
                supplier_key=key,
                related_transactions=_related(*(t for t, _ in high)),
                related_issue_ids=[i.id for _, i in high],
            )
        )

    # ---- unusually long unresolved ----------------------------------------
    resolution_days = [
        (issue.resolved_at - issue.created_at).total_seconds() / 86400
        for _, issue in pairs
        if issue.resolved_at is not None
    ]
    median_resolution = (
        round(median(resolution_days), 2) if len(resolution_days) >= minimum_history else None
    )
    limit_days = 2 * settings.overdue_review_days
    for txn, issue in pairs:
        if not issue.is_open or not scoped(txn):
            continue
        age = age_days(issue, now)
        if age < limit_days:
            continue
        signals.append(
            AnomalySignal(
                key=f"long_unresolved_issue:{issue.id}",
                signal="long_unresolved_issue",
                title=f"{issue.title} on {txn.name} has been open {age:.0f} days",
                severity="medium",
                observed_value=round(age, 1),
                baseline=median_resolution,
                baseline_label="Median resolution time (days)"
                if median_resolution is not None
                else "Not enough resolved issues for a baseline",
                threshold=limit_days,
                unit="days",
                currency=None,
                reason=(
                    f"Open for {age:.1f} days. "
                    + (
                        f"Median resolution time: {median_resolution:.1f} days. "
                        if median_resolution is not None
                        else ""
                    )
                    + f"Threshold: 2 × the {settings.overdue_review_days}-day review target "
                    f"({limit_days} days)."
                ),
                supplier=txn.supplier,
                supplier_key=txn.supplier_key,
                related_transactions=_related(txn),
                related_issue_ids=[issue.id],
            )
        )

    signals.sort(
        key=lambda s: (
            SEVERITY_RANK.get(s.severity, 9),
            -(abs(s.observed_value) / s.threshold if s.threshold else 0),
        )
    )
    return signals
