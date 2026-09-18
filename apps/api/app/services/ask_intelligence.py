"""Phase 6 intelligence intents for Ask cermat.

Each intent calls the same deterministic functions as the Overview, over the
snapshot Ask already loaded (adapted by ``dataset_from_snapshot``). Results are
returned as *findings*: sentences written in code, each tied to the issue IDs
that support it, so the answer model only rephrases — and chips still cite
source evidence.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from app.intelligence.anomalies import detect_anomalies
from app.intelligence.brief import brief_facts, records_brief
from app.intelligence.dataset import dataset_from_snapshot, supplier_key, window_for
from app.intelligence.overview import compute_overview, resolution_performance
from app.intelligence.patterns import detect_patterns
from app.intelligence.priority import priority_queue
from app.intelligence.suppliers import supplier_detail
from app.intelligence.trends import exception_trend
from app.schemas import AskIntent, AskMetrics, AskPlan, IntelligenceSettings
from app.services.ask_queries import (
    FAMILY_LABELS,
    QueryResult,
    WorkspaceSnapshot,
    handle_supplier_summary,
)

INTELLIGENCE_INTENTS = {
    AskIntent.overview_summary,
    AskIntent.supplier_ranking_by_issue_count,
    AskIntent.supplier_summary,
    AskIntent.recurring_patterns,
    AskIntent.anomaly_signals,
    AskIntent.exception_trend,
    AskIntent.priority_queue,
    AskIntent.variance_summary,
    AskIntent.resolution_performance,
}
PERIOD_TEXT = {"7d": "last 7 days", "30d": "last 30 days", "90d": "last 90 days", "all": "all time"}
TREND_CATEGORY = {"price": "price", "quantity": "quantity", "supplier": "supplier", "currency": "currency"}


def _money(currency: str | None, value: float, signed: bool = False) -> str:
    sign = ("+" if value > 0 else "−" if value < 0 else "") if signed else ""
    return f"{sign}{currency + ' ' if currency else ''}{abs(value):,.2f}"


def _hours(value: float | None) -> str:
    if value is None:
        return "not available"
    return f"{value:.1f} hours" if value < 48 else f"{value / 24:.1f} days"


def run_intelligence_query(
    snapshot: WorkspaceSnapshot,
    plan: AskPlan,
    settings: IntelligenceSettings,
    now: datetime,
) -> QueryResult:
    if plan.intent == AskIntent.supplier_ranking_by_issue_count:
        return handle_supplier_summary(snapshot, plan)

    dataset = dataset_from_snapshot(snapshot)
    period = plan.filters.period or "30d"
    lines: list[tuple[str, list[UUID]]] = []
    facts: dict = {"period": PERIOD_TEXT[period]}
    issue_ids: list[UUID] = []
    patterns = detect_patterns(dataset, settings, now)

    if plan.intent == AskIntent.overview_summary:
        overview = compute_overview(dataset, settings, period, now)
        facts = brief_facts(overview)
        top = overview.priority[:3]
        issue_ids = [item.issue_id for item in top]
        lines = [(line, []) for line in records_brief(facts)]
        activity = overview.activity
        if activity.issues_resolved:
            resolved = activity.issues_resolved
            lines.append((
                f"{resolved} exception{' was' if resolved == 1 else 's were'} resolved in the {PERIOD_TEXT[period]}.",
                [],
            ))
        if top:
            lines.append(("Top priority items are listed below with their evidence.", issue_ids))

    elif plan.intent == AskIntent.priority_queue:
        items = priority_queue(dataset, settings, now, patterns, limit=plan.limit)
        facts = {
            "open_items_ranked": len(items),
            "items": [
                {
                    "transaction": i.transaction_name,
                    "exception": i.title,
                    "band": i.priority_band,
                    "score": i.priority_score,
                    "reasons": i.priority_reasons[:3],
                }
                for i in items[:6]
            ],
        }
        issue_ids = [item.issue_id for item in items]
        if items:
            bands = {b: sum(1 for i in items if i.priority_band == b) for b in ("critical", "high", "normal")}
            lines.append((
                f"{len(items)} open exceptions ranked: {bands['critical']} critical, "
                f"{bands['high']} high, {bands['normal']} normal priority.",
                [],
            ))
            for item in items[:5]:
                lines.append((
                    f"{item.priority_band.capitalize()} priority (score {item.priority_score:g}) · {item.transaction_name}: "
                    f"{item.title} — {'; '.join(r.rsplit(' (+', 1)[0] for r in item.priority_reasons[:2])}.",
                    [item.issue_id],
                ))
        else:
            lines.append(("There are no open exceptions to prioritise.", []))

    elif plan.intent == AskIntent.recurring_patterns:
        facts = {
            "rule": f"≥ {settings.recurring_issue_min_count} exceptions across 2+ transactions "
            f"in {settings.recurring_issue_period_days} days",
            "patterns": [p.title for p in patterns],
        }
        if patterns:
            lines.append((
                f"{len(patterns)} recurring pattern{'s' if len(patterns) != 1 else ''} reached the "
                f"threshold of {settings.recurring_issue_min_count} exceptions in "
                f"{settings.recurring_issue_period_days} days.",
                [],
            ))
            for pattern in patterns[:5]:
                lines.append((pattern.title, pattern.issue_ids[:4]))
                issue_ids.extend(pattern.issue_ids)
        else:
            lines.append((
                f"No recurring patterns: no supplier, item or issue type reached "
                f"{settings.recurring_issue_min_count} exceptions across two or more transactions "
                f"in the last {settings.recurring_issue_period_days} days.",
                [],
            ))

    elif plan.intent == AskIntent.anomaly_signals:
        signals = detect_anomalies(dataset, settings, now)
        facts = {"signals": [{"title": s.title, "reason": s.reason} for s in signals[:6]]}
        if signals:
            lines.append((f"{len(signals)} signal{'s' if len(signals) != 1 else ''} exceed configured thresholds.", []))
            for signal in signals[:5]:
                lines.append((f"{signal.title}. {signal.reason}", signal.related_issue_ids[:4]))
                issue_ids.extend(signal.related_issue_ids)
        else:
            lines.append(("No anomaly signals: no open exception or invoice exceeds the configured thresholds.", []))

    elif plan.intent == AskIntent.exception_trend:
        category = TREND_CATEGORY.get(plan.filters.issue_type or "", "all")
        trend = exception_trend(dataset, period, now, category)
        label = "exceptions" if category == "all" else f"{FAMILY_LABELS[category]}es"
        facts = trend.model_dump(mode="json", exclude={"series"})
        if not trend.sufficient_history:
            lines.append((trend.message or "Not enough history for a trend yet.", []))
        elif trend.previous_period_created is None:
            lines.append((f"{trend.total_created} {label} were created across all recorded history.", []))
        else:
            verdict = {
                "increasing": "increasing",
                "decreasing": "decreasing",
                "stable": "broadly stable",
            }.get(trend.direction or "", "not materially changed")
            lines.append((
                f"{label.capitalize()} are {verdict}: {trend.total_created} created in the "
                f"{PERIOD_TEXT[period]} against {trend.previous_period_created} in the previous period.",
                [],
            ))
        lines.append((f"{trend.total_resolved} {label} were resolved in the {PERIOD_TEXT[period]}.", []))

    elif plan.intent == AskIntent.variance_summary:
        overview = compute_overview(dataset, settings, period, now)
        totals = overview.metrics.total_active_variance_amount
        facts = {
            "active_variance_by_currency": [t.model_dump() for t in totals],
            "average_variance_percentage": overview.metrics.average_variance_percentage,
        }
        if totals:
            lines.append((
                "Active billed variance: "
                + "; ".join(
                    f"{_money(t.currency, t.absolute_total)} across {t.issue_count} exception"
                    + ("s" if t.issue_count != 1 else "")
                    for t in totals
                )
                + ". Currencies are reported separately, never converted.",
                [],
            ))
            if overview.metrics.average_variance_percentage is not None:
                lines.append((
                    f"Average absolute variance on open billed exceptions: "
                    f"{overview.metrics.average_variance_percentage:.2f}%.",
                    [],
                ))
            ranked = sorted(
                (i for i in priority_queue(dataset, settings, now, patterns) if i.variance),
                key=lambda i: -i.variance.absolute_variance,
            )
            for item in ranked[:3]:
                lines.append((
                    f"{item.transaction_name}: {item.title} — "
                    f"{_money(item.variance.currency, item.variance.signed_variance, signed=True)}.",
                    [item.issue_id],
                ))
                issue_ids.append(item.issue_id)
        else:
            lines.append(("No open exception has a billed financial variance.", []))

    elif plan.intent == AskIntent.resolution_performance:
        perf = resolution_performance(dataset, settings, window_for(period, now), now)
        facts = perf.model_dump(mode="json")
        lines.append((
            f"{perf.issues_resolved} exceptions resolved and {perf.issues_created} created in the "
            f"{PERIOD_TEXT[period]}"
            + (f"; {perf.resolution_rate:g}% of those created are now resolved." if perf.resolution_rate is not None else "."),
            [],
        ))
        lines.append((
            f"Median resolution time: {_hours(perf.median_resolution_hours)}; "
            f"average: {_hours(perf.average_resolution_hours)}.",
            [],
        ))
        if perf.oldest_open_issue_age_days is not None:
            lines.append((
                f"Oldest open exception: {perf.oldest_open_issue_age_days:.1f} days; "
                f"{perf.overdue_issue_count} past the {settings.overdue_review_days}-day review target.",
                [],
            ))

    elif plan.intent == AskIntent.supplier_summary:
        key = supplier_key(plan.filters.supplier)
        detail = supplier_detail(dataset, settings, now, key) if key else None
        if detail is None:
            return QueryResult(result_kind="none", metrics=AskMetrics())
        intel = detail.supplier
        items = priority_queue(dataset, settings, now, patterns, supplier_key=key)
        facts = {
            "supplier": intel.model_dump(mode="json"),
            "pattern_summary": detail.pattern_summary.sentence if detail.pattern_summary else None,
            "patterns": [p.title for p in detail.patterns],
            "signals": [a.title for a in detail.anomalies],
            "priority_items": [
                {"transaction": i.transaction_name, "exception": i.title, "band": i.priority_band,
                 "score": i.priority_score, "reasons": i.priority_reasons[:3]}
                for i in items[:4]
            ],
        }
        rate = f"{intel.issue_rate:g}%" if intel.issue_rate is not None else "not available"
        lines.append((
            f"{intel.supplier_name}: {intel.transaction_count} transactions, "
            f"{intel.open_issue_count} open exceptions, issue rate {rate}.",
            [],
        ))
        if items:
            top = items[0]
            lines.append((
                f"{len(items)} of its open exceptions are in the priority queue; the highest is "
                f"{top.transaction_name} ({top.priority_band} priority, score {top.priority_score:g}) because: "
                + "; ".join(r.rsplit(" (+", 1)[0].lower() for r in top.priority_reasons[:3]) + ".",
                [top.issue_id],
            ))
            issue_ids = [i.issue_id for i in items]
        for pattern in detail.patterns[:2]:
            lines.append((pattern.title, pattern.issue_ids[:4]))
        if detail.pattern_summary:
            lines.append((detail.pattern_summary.sentence, []))

    matched = {str(i) for i in issue_ids}
    pairs = [
        (txn, issue)
        for txn in snapshot.transactions
        for issue in txn.issues
        if str(issue.id) in matched
    ]
    order = {str(i): n for n, i in enumerate(dict.fromkeys(issue_ids))}
    pairs.sort(key=lambda pair: order.get(str(pair[1].id), 999))
    return QueryResult(
        result_kind="insights",
        metrics=AskMetrics(
            transaction_count=len({t.id for t, _ in pairs}),
            issue_count=len(pairs),
            open_issue_count=sum(1 for _, i in pairs if i.status == "open"),
            resolved_issue_count=sum(1 for _, i in pairs if i.status == "resolved"),
        ),
        issues=pairs[: plan.limit],
        total_matches=max(len(lines), 1),
        facts=facts,
        insight_lines=lines,
    )
