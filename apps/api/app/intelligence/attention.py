"""In-app attention queue.

Each qualifying condition has a stable ``event_key``; the queue is synced on
read, so a condition produces one event no matter how often the page loads.

event_type            key                                   condition
high_severity_issue   high_severity_issue:<issue id>        open high-severity exception
large_variance        large_variance:<issue id>             |billed variance| ≥ high_value_variance_amount
overdue_review        overdue_review:<issue id>             open ≥ overdue_review_days
recurring_pattern     recurring_pattern:<pattern key>       recurring pattern threshold reached
anomaly               anomaly:<signal key>                  anomaly signal (except high-value
                                                            variance, already a large_variance event)

When a condition stops holding (issue resolved, pattern below threshold) the
event is *cleared*, not deleted. If it holds again, the same row is restored —
never duplicated. Dismissed events stay dismissed.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.intelligence.dataset import IntelDataset
from app.intelligence.priority import age_days
from app.intelligence.variance import financial_variance
from app.models import AttentionEventModel
from app.schemas import (
    AnomalySignal,
    AttentionEventRecord,
    AttentionList,
    IntelligenceSettings,
    PatternSignal,
)
from app.services.ask_queries import SEVERITY_RANK


@dataclass(frozen=True)
class Candidate:
    event_key: str
    event_type: str
    title: str
    message: str
    severity: str
    entity_type: str
    entity_id: str | None
    entity_label: str | None
    transaction_id: UUID | None


@dataclass(frozen=True)
class ExistingEvent:
    event_key: str
    cleared: bool
    dismissed: bool


def _money(currency: str | None, value: float) -> str:
    return f"{currency + ' ' if currency else ''}{value:,.2f}"


def attention_candidates(
    dataset: IntelDataset,
    settings: IntelligenceSettings,
    now: datetime,
    patterns: list[PatternSignal],
    anomalies: list[AnomalySignal],
) -> list[Candidate]:
    candidates: dict[str, Candidate] = {}

    def add(candidate: Candidate) -> None:
        candidates.setdefault(candidate.event_key, candidate)

    for txn, issue in dataset.open_pairs():
        label = txn.name
        detail = issue.title + (f" — {issue.item_description}" if issue.item_description else "")
        if issue.severity == "high":
            add(Candidate(
                event_key=f"high_severity_issue:{issue.id}",
                event_type="high_severity_issue",
                title=issue.title,
                message=f"New high-severity exception on {txn.name}: {detail}.",
                severity="high",
                entity_type="transaction",
                entity_id=str(txn.id),
                entity_label=label,
                transaction_id=txn.id,
            ))
        variance = financial_variance(txn, issue)
        if variance and variance.absolute_variance >= settings.high_value_variance_amount:
            add(Candidate(
                event_key=f"large_variance:{issue.id}",
                event_type="large_variance",
                title=f"Billed variance {_money(variance.currency, variance.signed_variance)}",
                message=(
                    f"{detail} on {txn.name} has a billed variance of "
                    f"{_money(variance.currency, variance.absolute_variance)}, at or above the "
                    f"{settings.high_value_variance_amount:,.2f} threshold."
                ),
                severity="high" if issue.severity == "high" else "medium",
                entity_type="transaction",
                entity_id=str(txn.id),
                entity_label=label,
                transaction_id=txn.id,
            ))
        age = age_days(issue, now)
        if age >= settings.overdue_review_days:
            add(Candidate(
                event_key=f"overdue_review:{issue.id}",
                event_type="overdue_review",
                title=f"Review open for {int(age)} days",
                message=(
                    f"{detail} on {txn.name} has been open for {age:.0f} days "
                    f"(review target {settings.overdue_review_days} days)."
                ),
                severity="medium",
                entity_type="transaction",
                entity_id=str(txn.id),
                entity_label=label,
                transaction_id=txn.id,
            ))

    for pattern in patterns:
        add(Candidate(
            event_key=f"recurring_pattern:{pattern.key}",
            event_type="recurring_pattern",
            title="Recurring pattern reached threshold",
            message=pattern.title,
            severity=pattern.severity if pattern.severity != "low" else "medium",
            entity_type="supplier" if pattern.supplier_key else "workspace",
            entity_id=pattern.supplier_key,
            entity_label=pattern.supplier,
            transaction_id=None,
        ))

    # Issue-level events already cover these issues; don't notify twice.
    notified_issues = {
        c.event_key.split(":", 1)[1] for c in candidates.values() if c.entity_type == "transaction"
    }
    for anomaly in anomalies:
        if anomaly.signal == "high_value_variance":
            continue
        if anomaly.related_issue_ids and all(
            str(issue_id) in notified_issues for issue_id in anomaly.related_issue_ids
        ):
            continue
        related = anomaly.related_transactions[0] if len(anomaly.related_transactions) == 1 else None
        add(Candidate(
            event_key=f"anomaly:{anomaly.key}",
            event_type="anomaly",
            title=anomaly.title,
            message=anomaly.reason,
            severity=anomaly.severity,
            entity_type="transaction" if related else ("supplier" if anomaly.supplier_key else "workspace"),
            entity_id=str(related.id) if related else anomaly.supplier_key,
            entity_label=related.name if related else anomaly.supplier,
            transaction_id=related.id if related else None,
        ))
    return list(candidates.values())


def plan_attention_changes(
    existing: dict[str, ExistingEvent], candidates: list[Candidate]
) -> tuple[list[Candidate], list[str], list[str]]:
    """Return (to_insert, keys_to_clear, keys_to_restore). Pure and idempotent."""
    wanted = {candidate.event_key for candidate in candidates}
    to_insert = [c for c in candidates if c.event_key not in existing]
    to_restore = [
        key for key, event in existing.items() if key in wanted and event.cleared and not event.dismissed
    ]
    to_clear = [key for key, event in existing.items() if key not in wanted and not event.cleared]
    return to_insert, to_clear, to_restore


async def sync_attention(
    session: AsyncSession, workspace_id: UUID, candidates: list[Candidate], now: datetime
) -> None:
    rows = (
        await session.execute(select(AttentionEventModel).where(AttentionEventModel.workspace_id == workspace_id))
    ).scalars().all()
    existing = {
        row.event_key: ExistingEvent(
            event_key=row.event_key,
            cleared=row.cleared_at is not None,
            dismissed=row.dismissed_at is not None,
        )
        for row in rows
    }
    to_insert, to_clear, to_restore = plan_attention_changes(existing, candidates)
    by_key = {row.event_key: row for row in rows}
    for key in to_clear:
        by_key[key].cleared_at = now
    for key in to_restore:
        by_key[key].cleared_at = None
    if to_insert:
        await session.execute(
            insert(AttentionEventModel)
            .values(
                [
                    {
                        "workspace_id": workspace_id,
                        "event_key": c.event_key,
                        "event_type": c.event_type,
                        "title": c.title[:200],
                        "message": c.message[:600],
                        "severity": c.severity,
                        "entity_type": c.entity_type,
                        "entity_id": c.entity_id,
                        "entity_label": (c.entity_label or "")[:200] or None,
                        "transaction_id": c.transaction_id,
                        "created_at": now,
                    }
                    for c in to_insert
                ]
            )
            .on_conflict_do_nothing(index_elements=["workspace_id", "event_key"])
        )
    await session.commit()


def attention_record(row: AttentionEventModel) -> AttentionEventRecord:
    return AttentionEventRecord(
        id=row.id,
        event_key=row.event_key,
        event_type=row.event_type,
        title=row.title,
        message=row.message,
        severity=row.severity,
        entity_type=row.entity_type,
        entity_id=row.entity_id,
        entity_label=row.entity_label,
        transaction_id=row.transaction_id,
        created_at=row.created_at,
        seen_at=row.seen_at,
        dismissed_at=row.dismissed_at,
        cleared_at=row.cleared_at,
    )


async def list_attention(
    session: AsyncSession, workspace_id: UUID, *, include_inactive: bool = False, limit: int = 100
) -> AttentionList:
    query = select(AttentionEventModel).where(AttentionEventModel.workspace_id == workspace_id)
    if not include_inactive:
        query = query.where(
            AttentionEventModel.dismissed_at.is_(None),
            AttentionEventModel.cleared_at.is_(None),
        )
    rows = (await session.execute(query)).scalars().all()
    rows = sorted(
        rows,
        key=lambda row: (SEVERITY_RANK.get(row.severity, 9), -row.created_at.timestamp()),
    )
    active = [row for row in rows if row.dismissed_at is None and row.cleared_at is None]
    return AttentionList(
        active_count=len(active),
        unseen_count=sum(1 for row in active if row.seen_at is None),
        events=[attention_record(row) for row in rows[:limit]],
    )
