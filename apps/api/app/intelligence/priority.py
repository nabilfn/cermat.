"""Deterministic priority queue for unresolved issues.

    priority_score = severity
                   + high-value variance      (|billed variance| ≥ high_value_variance_amount)
                   + high variance percentage (|variance %| ≥ high_variance_percentage)
                   + age                      (min(age_days × age_per_day, age_cap))
                   + overdue                  (age ≥ overdue_review_days)
                   + related issues           (min(other open issues on txn × related_issue, related_cap))
                   + recurring supplier       (supplier has a recurring pattern)
                   + issue type               (per-family weight)

Bands
    critical  score ≥ critical_score AND severity is high AND
              (high-value variance OR overdue) — a clear, checkable condition
    high      score ≥ high_score
    normal    everything else

This is a transparent points system, not a machine-learning risk score.
Every point awarded is returned as a reason.
"""

from __future__ import annotations

from datetime import datetime

from app.intelligence.dataset import IntelDataset, IssueFact, TxnFact
from app.intelligence.variance import billed_percentage, financial_variance
from app.schemas import (
    IntelligenceSettings,
    PatternSignal,
    PriorityComponent,
    PriorityItem,
)
from app.services.ask_queries import FAMILY_LABELS


def age_days(issue: IssueFact, now: datetime) -> float:
    return max((now - issue.created_at).total_seconds() / 86400, 0.0)


def _money(currency: str | None, value: float) -> str:
    return f"{currency + ' ' if currency else ''}{value:,.2f}"


def score_issue(
    txn: TxnFact,
    issue: IssueFact,
    *,
    settings: IntelligenceSettings,
    now: datetime,
    recurring: dict[str, PatternSignal],
) -> PriorityItem:
    weights = settings.priority_weights
    components: list[PriorityComponent] = []

    def add(factor: str, points: float, detail: str) -> None:
        if points > 0:
            components.append(PriorityComponent(factor=factor, points=round(points, 1), detail=detail))

    severity_points = {
        "high": weights.severity_high,
        "medium": weights.severity_medium,
        "low": weights.severity_low,
    }.get(issue.severity, 0)
    add("severity", severity_points, f"{issue.severity.capitalize()} severity")

    variance = financial_variance(txn, issue)
    high_value = bool(variance and variance.absolute_variance >= settings.high_value_variance_amount)
    if high_value and variance:
        add(
            "variance_amount",
            weights.high_value_variance,
            f"Billed variance {_money(variance.currency, variance.absolute_variance)} is at or above "
            f"the {settings.high_value_variance_amount:,.2f} review threshold",
        )

    percentage = billed_percentage(issue)
    if percentage is not None and abs(percentage) >= settings.high_variance_percentage:
        add(
            "variance_percentage",
            weights.high_variance_percentage,
            f"Variance of {'+' if percentage > 0 else '−'}{abs(percentage):.2f}% is at or above the "
            f"{settings.high_variance_percentage:g}% threshold",
        )

    age = age_days(issue, now)
    add("age", min(age * weights.age_per_day, weights.age_cap), f"Open for {age:.1f} days")

    overdue = age >= settings.overdue_review_days
    if overdue:
        add(
            "overdue",
            weights.overdue,
            f"Past the {settings.overdue_review_days}-day review target",
        )

    others = len(txn.open_issues) - 1
    if others > 0:
        add(
            "related_issues",
            min(others * weights.related_issue, weights.related_cap),
            f"{others} other open issue{'s' if others != 1 else ''} on {txn.name}",
        )

    pattern = recurring.get(txn.supplier_key or "")
    if pattern is not None:
        add("recurring_supplier", weights.recurring_supplier, f"Recurring pattern: {pattern.title}")

    type_points = weights.issue_type.get(issue.family, 0)
    add("issue_type", type_points, f"Issue type: {FAMILY_LABELS.get(issue.family, issue.family)}")

    score = round(sum(component.points for component in components), 1)
    if (
        score >= settings.critical_score
        and issue.severity == "high"
        and (high_value or overdue)
    ):
        band = "critical"
    elif score >= settings.high_score:
        band = "high"
    else:
        band = "normal"

    ordered = sorted(components, key=lambda component: -component.points)
    return PriorityItem(
        issue_id=issue.id,
        transaction_id=txn.id,
        transaction_name=txn.name,
        supplier=txn.supplier,
        supplier_key=txn.supplier_key,
        code=issue.code,
        family=issue.family,
        title=issue.title,
        item_description=issue.item_description,
        severity=issue.severity,
        age_days=round(age, 1),
        variance=variance,
        priority_score=score,
        priority_band=band,
        priority_reasons=[f"{c.detail} (+{c.points:g})" for c in ordered],
        components=ordered,
    )


def recurring_by_supplier(patterns: list[PatternSignal]) -> dict[str, PatternSignal]:
    """The strongest recurring pattern per supplier (patterns arrive pre-sorted)."""
    result: dict[str, PatternSignal] = {}
    for pattern in patterns:
        if pattern.supplier_key and pattern.supplier_key not in result:
            result[pattern.supplier_key] = pattern
    return result


def priority_queue(
    dataset: IntelDataset,
    settings: IntelligenceSettings,
    now: datetime,
    patterns: list[PatternSignal],
    *,
    limit: int | None = None,
    supplier_key: str | None = None,
) -> list[PriorityItem]:
    recurring = recurring_by_supplier(patterns)
    items = [
        score_issue(txn, issue, settings=settings, now=now, recurring=recurring)
        for txn, issue in dataset.open_pairs()
        if supplier_key is None or txn.supplier_key == supplier_key
    ]
    items.sort(key=lambda item: (-item.priority_score, -item.age_days))
    return items[:limit] if limit else items
