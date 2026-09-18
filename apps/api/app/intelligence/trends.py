"""Exception trend series. Buckets are calendar days/weeks/months in UTC.

Granularity: 7d and 30d → daily, 90d → weekly (Monday start), all time →
weekly up to 26 weeks of history, monthly beyond.

``open_end_of_period`` counts issues created before the bucket ended and not
resolved by then. Reopening clears ``resolved_at``, so a reopened issue counts
as open throughout — the series reflects current review state, not an audit log.

Direction compares issues created in this period with the previous period of
the same length and is only reported when the change is material
(≥ 25% and ≥ 2 issues) and there are at least 3 issues across both periods.
"""

from __future__ import annotations

from datetime import datetime, time, timedelta, timezone

from app.intelligence.dataset import IntelDataset, IssueFact, Window, window_for
from app.schemas import TrendPoint, TrendResponse

CATEGORY_FAMILIES: dict[str, set[str] | None] = {
    "all": None,
    "price": {"price"},
    "quantity": {"quantity"},
    "supplier": {"supplier"},
    "currency": {"currency"},
    # Missing documents surface as missing or unreadable line items at reconciliation.
    "missing_documents": {"missing_item", "missing_line_items"},
}
MIN_ISSUES_FOR_TREND = 3


def _floor(moment: datetime, granularity: str) -> datetime:
    day = datetime.combine(moment.astimezone(timezone.utc).date(), time.min, tzinfo=timezone.utc)
    if granularity == "week":
        return day - timedelta(days=day.weekday())
    if granularity == "month":
        return day.replace(day=1)
    return day


def _next(bucket: datetime, granularity: str) -> datetime:
    if granularity == "day":
        return bucket + timedelta(days=1)
    if granularity == "week":
        return bucket + timedelta(days=7)
    year, month = (bucket.year + 1, 1) if bucket.month == 12 else (bucket.year, bucket.month + 1)
    return bucket.replace(year=year, month=month)


def category_issues(dataset: IntelDataset, category: str) -> list[IssueFact]:
    families = CATEGORY_FAMILIES[category]
    return [
        issue
        for _, issue in dataset.issue_pairs()
        if families is None or issue.family in families
    ]


def change_direction(current: int, previous: int | None) -> str | None:
    if previous is None or current + previous < MIN_ISSUES_FOR_TREND:
        return None
    if current - previous >= 2 and current >= previous * 1.25:
        return "increasing"
    if previous - current >= 2 and current <= previous * 0.75:
        return "decreasing"
    return "stable"


def exception_trend(
    dataset: IntelDataset,
    period: str,
    now: datetime,
    category: str = "all",
) -> TrendResponse:
    window: Window = window_for(period, now)
    issues = category_issues(dataset, category)

    start = window.start
    if start is None:
        earliest = min((issue.created_at for issue in issues), default=now)
        span_days = (now - earliest).days
        granularity = "week" if span_days <= 182 else "month"
        start = earliest
    else:
        granularity = "day" if period in {"7d", "30d"} else "week"

    series: list[TrendPoint] = []
    bucket = _floor(start, granularity)
    while bucket <= now:
        bucket_end = min(_next(bucket, granularity), now + timedelta(microseconds=1))
        lower = max(bucket, start)
        created = sum(1 for i in issues if lower <= i.created_at < bucket_end)
        resolved = sum(
            1 for i in issues if i.resolved_at is not None and lower <= i.resolved_at < bucket_end
        )
        open_end = sum(
            1
            for i in issues
            if i.created_at < bucket_end and (i.resolved_at is None or i.resolved_at >= bucket_end)
        )
        series.append(
            TrendPoint(
                date=bucket.date().isoformat(),
                created=created,
                resolved=resolved,
                open_end_of_period=open_end,
            )
        )
        bucket = _next(bucket, granularity)

    total_created = sum(1 for i in issues if window.contains(i.created_at))
    total_resolved = sum(1 for i in issues if window.contains(i.resolved_at))
    previous = (
        sum(1 for i in issues if window.in_previous(i.created_at))
        if window.start is not None
        else None
    )
    active_buckets = sum(1 for point in series if point.created or point.resolved)
    sufficient = len(issues) >= MIN_ISSUES_FOR_TREND and active_buckets >= 2
    return TrendResponse(
        period=period,
        category=category,
        granularity=granularity,
        start=window.start,
        end=now,
        series=series,
        total_created=total_created,
        total_resolved=total_resolved,
        previous_period_created=previous,
        direction=change_direction(total_created, previous) if sufficient else None,
        sufficient_history=sufficient,
        message=None
        if sufficient
        else "Not enough exception history yet for a trend. It will appear once "
        "exceptions have been recorded on at least two different dates.",
    )
