"""Ask cermat. orchestration.

    question ─► plan (model or keywords) ─► resolve entities to IDs
             ─► fixed query handler over persisted records
             ─► grounded context ─► model synthesis (guarded) ─► answer + sources

Retrieve facts deterministically. Let AI explain them clearly.
"""

from __future__ import annotations

import logging
import re
from collections import Counter
from collections.abc import AsyncIterator
from decimal import Decimal
from typing import Any
from uuid import UUID

from fastapi.concurrency import run_in_threadpool
from sqlalchemy.ext.asyncio import AsyncSession

from app.intelligence.config import load_settings
from app.intelligence.dataset import utcnow
from app.schemas import (
    AskAnswer,
    AskAnswerPoint,
    AskConversationContext,
    AskEntity,
    AskIntent,
    AskIssueRow,
    AskMetrics,
    AskRequest,
    AskResponse,
    AskSuggestions,
    AskSupplierRow,
    AskTransactionRow,
)
from app.services.ask_context import BuiltResult, build_rows, grounded_context
from app.services.ask_intelligence import INTELLIGENCE_INTENTS, run_intelligence_query
from app.services.ask_llm import AskModel, ComposedAnswer, PlannerOutput
from app.services.ask_planner import ResolvedPlan, heuristic_plan, resolve_plan
from app.services.ask_queries import (
    FAMILY_LABELS,
    QueryResult,
    TransactionSnap,
    WorkspaceSnapshot,
    load_snapshot,
    run_query,
)
from app.services.grounding import ungrounded

logger = logging.getLogger("cermat.ask")

UNSUPPORTED_MESSAGE = (
    "I can answer questions about the documents, transactions and review issues "
    "in this cermat. workspace."
)
MAX_POINTS = 6


class AskError(Exception):
    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


# ---------------------------------------------------------------------------
# Formatting helpers (deterministic)
# ---------------------------------------------------------------------------


def _plural(count: int, singular: str, plural: str | None = None) -> str:
    return f"{count} {singular if count == 1 else (plural or singular + 's')}"


def _money(currency: str | None, value: float, signed: bool = False) -> str:
    sign = "+" if signed and value > 0 else ("−" if value < 0 else "")
    prefix = f"{currency} " if currency else ""
    return f"{sign}{prefix}{abs(value):,.2f}"


def _count_noun(count: int, noun: str) -> str:
    """'1 open price mismatches' → '1 open price mismatch'."""
    if count == 1:
        for plural, singular in (("mismatches", "mismatch"), ("issues", "issue"), ("errors", "error")):
            if plural in noun:
                noun = noun.replace(plural, singular, 1)
                break
    return f"{count} {noun}"


def _pluralise(label: str) -> str:
    if label.endswith("s"):
        return label
    if label.endswith(("ch", "sh", "x")):
        return label + "es"
    return label + "s"


def _qty(value: float, signed: bool = False) -> str:
    sign = "+" if signed and value > 0 else ("−" if value < 0 else "")
    return f"{sign}{abs(value):g}"


def _variance_text(row: AskIssueRow) -> str | None:
    variance = row.variance
    if variance is None:
        return None
    if variance.kind == "money":
        text = _money(variance.currency, variance.delta, signed=True)
    else:
        text = f"{_qty(variance.delta, signed=True)} units"
    if variance.percentage is not None:
        sign = "+" if variance.percentage > 0 else ("−" if variance.percentage < 0 else "")
        text += f" ({sign}{abs(variance.percentage):.2f}%)"
    return text


def _sentence(text: str) -> str:
    """End with exactly one full stop (supplier names often end in 'Bhd.')."""
    text = text.rstrip()
    return text if text.endswith((".", "?", "!")) else text + "."


def _tidy(answer: AskAnswer) -> AskAnswer:
    fix = lambda value: re.sub(r"\.\.(?=\s|$)", ".", value)  # noqa: E731
    return AskAnswer(
        headline=fix(answer.headline),
        points=[
            AskAnswerPoint(text=fix(point.text), source_ids=point.source_ids)
            for point in answer.points
        ],
    )


def _issue_line(row: AskIssueRow, *, include_transaction: bool) -> str:
    parts: list[str] = []
    if include_transaction:
        parts.append(f"{row.transaction_name}:")
    parts.append(row.title)
    if row.item_description:
        parts[-1] += f" — {row.item_description}"
    detail: list[str] = []
    if row.expected or row.actual:
        detail.append(f"expected {row.expected or '—'}, actual {row.actual or '—'}")
    variance = _variance_text(row)
    if variance:
        detail.append(f"calculated difference {variance}")
    if row.variance and row.variance.billed_impact is not None:
        detail.append(
            "billed impact "
            + _money(row.variance.currency, row.variance.billed_impact, signed=True)
        )
    line = " ".join(parts)
    if detail:
        joined = "; ".join(detail)
        line += ". " + joined[0].upper() + joined[1:]
    line += f" [{row.severity}]"
    if row.status == "resolved":
        line += " — resolved" + (f": “{row.resolution_note}”" if row.resolution_note else "")
    return line


def _family_breakdown(metrics: AskMetrics) -> list[str]:
    ordered = sorted(metrics.by_family.items(), key=lambda item: (-item[1], item[0]))
    return [f"{count} × {FAMILY_LABELS.get(family, family)}" for family, count in ordered]


INTENT_NOUNS: dict[AskIntent, str] = {
    AskIntent.list_open_issues: "open issues",
    AskIntent.list_high_severity_issues: "high-severity issues",
    AskIntent.list_resolved_issues: "resolved issues",
    AskIntent.supplier_issues: "issues",
    AskIntent.price_discrepancies: "price mismatches",
    AskIntent.quantity_discrepancies: "quantity mismatches",
    AskIntent.supplier_mismatches: "supplier mismatches",
    AskIntent.currency_mismatches: "currency mismatches",
    AskIntent.arithmetic_mismatches: "line arithmetic errors",
    AskIntent.missing_documents: "incomplete transactions",
    AskIntent.recent_transactions: "transactions",
    AskIntent.resolved_transactions: "resolved transactions",
    AskIntent.transaction_search: "matching transactions",
    AskIntent.general_summary: "transactions",
    AskIntent.supplier_issue_summary: "suppliers with matching issues",
}


def _issue_noun(resolved: ResolvedPlan) -> str:
    plan = resolved.plan
    noun = INTENT_NOUNS.get(plan.intent, "records")
    status = plan.filters.status
    if plan.intent not in {AskIntent.list_open_issues, AskIntent.list_resolved_issues}:
        if status == "resolved":
            noun = f"resolved {noun}"
        elif status in (None, "open") and plan.intent != AskIntent.missing_documents:
            if plan.intent not in {
                AskIntent.recent_transactions,
                AskIntent.resolved_transactions,
                AskIntent.transaction_search,
                AskIntent.general_summary,
                AskIntent.supplier_issue_summary,
            }:
                noun = f"open {noun}"
    if plan.intent == AskIntent.quantity_discrepancies and plan.filters.quantity_direction:
        noun += (
            " where invoiced quantity exceeds delivered"
            if plan.filters.quantity_direction == "invoice_over_delivery"
            else " where delivery was short of the order"
        )
    if plan.filters.severity and plan.intent != AskIntent.list_high_severity_issues:
        noun = f"{plan.filters.severity}-severity {noun}"
    if plan.filters.supplier:
        noun += f" for {plan.filters.supplier}"
    return noun


def compose_from_records(
    resolved: ResolvedPlan,
    result: QueryResult,
    built: BuiltResult,
    scope_name: str | None,
) -> AskAnswer:
    """Deterministic answer. Used when no model is configured or its output fails checks."""
    plan = resolved.plan
    metrics = result.metrics
    where = f" in {scope_name}" if scope_name else ""
    points: list[AskAnswerPoint] = []

    if resolved.focus is not None:
        focus = resolved.focus
        open_rows = [row for row in built.issues if row.status == "open"]
        resolved_rows = [row for row in built.issues if row.status == "resolved"]
        if open_rows:
            open_severity = Counter(row.severity for row in open_rows)
            severities = ", ".join(
                f"{open_severity[severity]} {severity}"
                for severity in ("high", "medium", "low")
                if open_severity[severity]
            )
            headline = (
                f"{focus.name} is flagged with {_plural(len(open_rows), 'open issue')}"
                f" ({severities})."
            )
        elif resolved_rows:
            headline = (
                f"{focus.name} has no open issues; "
                f"{_plural(len(resolved_rows), 'issue')} resolved by review."
            )
        elif focus.reconciled_at is None:
            missing = ", ".join(t.replace("_", " ") for t in focus.missing_document_types)
            headline = f"{focus.name} has not been reconciled yet." + (
                f" Missing: {missing}." if missing else ""
            )
        else:
            headline = f"{focus.name} matched with no reconciliation exceptions."
        for row in built.issues[:MAX_POINTS]:
            points.append(
                AskAnswerPoint(
                    text=_issue_line(row, include_transaction=False),
                    source_ids=row.source_ids,
                )
            )
        if not built.issues and built.focus_source_ids:
            points.append(
                AskAnswerPoint(
                    text="Source documents: "
                    + ", ".join(
                        doc.document_number or doc.filename for doc in focus.documents
                    )
                    + ".",
                    source_ids=built.focus_source_ids,
                )
            )
        return _tidy(AskAnswer(headline=headline, points=points))

    if result.result_kind == "suppliers":
        rows: list[AskSupplierRow] = built.suppliers
        top = rows[0]
        if plan.filters.issue_type or plan.filters.severity or plan.filters.status:
            status_word = {"open": "unresolved ", "resolved": "resolved "}.get(
                plan.filters.status or "open", ""
            )
            kind = (
                _pluralise(FAMILY_LABELS.get(plan.filters.issue_type, plan.filters.issue_type))
                if plan.filters.issue_type
                else "issues"
            )
            if plan.filters.severity:
                kind = f"{plan.filters.severity}-severity {kind}"
            verb = "has" if result.total_matches == 1 else "have"
            headline = (
                f"{_plural(result.total_matches, 'supplier')} {verb} {status_word}{kind}{where}."
            )
        else:
            headline = (
                f"{top.supplier} has the most open issues: {top.open_issue_count}."
                if top.open_issue_count
                else f"No supplier has open issues{where}."
            )
        for row in rows[:MAX_POINTS]:
            breakdown = ", ".join(
                _plural(count, FAMILY_LABELS.get(family, family), _pluralise(FAMILY_LABELS.get(family, family)))
                for family, count in sorted(row.open_by_family.items(), key=lambda i: -i[1])
            )
            issue_label = (
                FAMILY_LABELS.get(plan.filters.issue_type, "issue")
                if plan.filters.issue_type
                else "issue"
            )
            text = (
                f"{row.supplier}: "
                f"{_plural(row.open_issue_count, 'open ' + issue_label, 'open ' + _pluralise(issue_label))}"
                + (f" ({row.high_open_count} high)" if row.high_open_count else "")
                + f" across {_plural(row.transaction_count, 'transaction')}"
            )
            if breakdown and len(row.open_by_family) > 1:
                text += f" — {breakdown}"
            members = set(row.transaction_ids)
            cited = [
                sid
                for issue in built.issues
                if issue.transaction_id in members
                for sid in issue.source_ids
            ]
            points.append(
                AskAnswerPoint(text=_sentence(text), source_ids=list(dict.fromkeys(cited))[:4])
            )
        return _tidy(AskAnswer(headline=headline, points=points))

    if result.result_kind == "issues":
        rows_i = built.issues
        txn_count = metrics.transaction_count
        if plan.intent == AskIntent.list_open_issues and not plan.filters.supplier:
            headline = (
                f"{_plural(txn_count, 'transaction')} currently "
                f"{'needs' if txn_count == 1 else 'need'} review{where}, with "
                f"{_plural(metrics.open_issue_count, 'open issue')}."
            )
        else:
            headline = (
                f"{_count_noun(result.total_matches, _issue_noun(resolved))} across "
                f"{_plural(txn_count, 'transaction')}{where}."
            )
        breakdown = _family_breakdown(metrics)
        if len(breakdown) > 1 or plan.intent in {
            AskIntent.list_open_issues,
            AskIntent.list_high_severity_issues,
            AskIntent.supplier_issues,
        }:
            points.append(AskAnswerPoint(text="By type: " + "; ".join(breakdown) + "."))
        # The exceptions table lists every row; the brief highlights the top few.
        for row in rows_i[:3]:
            points.append(
                AskAnswerPoint(
                    text=_issue_line(row, include_transaction=not scope_name),
                    source_ids=row.source_ids,
                )
            )
        return _tidy(AskAnswer(headline=headline, points=points))

    # Transaction lists.
    rows_t: list[AskTransactionRow] = built.transactions
    if plan.intent == AskIntent.general_summary:
        headline = (
            f"{_plural(metrics.transaction_count, 'transaction')}{where}; "
            f"{_plural(metrics.open_issue_count, 'open issue')} across "
            f"{_plural(result.total_matches, 'transaction')} needing review."
        )
        status_parts = ", ".join(
            f"{count} {status.replace('_', ' ')}"
            for status, count in sorted(metrics.by_status.items(), key=lambda i: -i[1])
        )
        if status_parts:
            points.append(AskAnswerPoint(text=f"By status: {status_parts}."))
        if metrics.by_family:
            points.append(
                AskAnswerPoint(text="Issue types: " + "; ".join(_family_breakdown(metrics)) + ".")
            )
    else:
        count = result.total_matches
        singular = count == 1
        supplier = f" for {plan.filters.supplier}" if plan.filters.supplier else ""
        predicate = {
            AskIntent.missing_documents: (
                "is" if singular else "are"
            ) + " incomplete or not yet reconciled",
            AskIntent.resolved_transactions: "fully resolved by review",
            AskIntent.transaction_search: (
                ("matches" if singular else "match") + f" “{plan.filters.search}”"
                if plan.filters.search
                else "on record"
            ),
        }.get(plan.intent, "on record")
        headline = f"{_plural(count, 'transaction')}{supplier} {predicate}{where}."
        if plan.intent == AskIntent.recent_transactions:
            headline = (
                f"{_plural(len(rows_t), 'most recent transaction', 'most recent transactions')}"
                f"{supplier}{where}."
            )
    for row in rows_t[: MAX_POINTS - len(points)]:
        detail = [row.supplier or "supplier not extracted", row.status.replace("_", " ")]
        if row.missing_document_types:
            detail.append(
                "missing " + ", ".join(t.value.replace("_", " ") for t in row.missing_document_types)
            )
        if row.open_issue_count:
            detail.append(
                f"{_plural(row.open_issue_count, 'open issue')}"
                + (f", highest {row.highest_open_severity}" if row.highest_open_severity else "")
            )
        if row.total is not None:
            detail.append(f"printed total {_money(row.currency, row.total)}")
        points.append(AskAnswerPoint(text=f"{row.name}: " + "; ".join(detail) + "."))
    return _tidy(AskAnswer(headline=headline, points=points))


def insight_answer(result: QueryResult, built: BuiltResult) -> AskAnswer:
    """Findings were written in code by the intelligence layer; attach evidence."""
    by_issue = {str(row.issue_id): row.source_ids for row in built.issues}
    points: list[AskAnswerPoint] = []
    for text, issue_ids in result.insight_lines[1:MAX_POINTS + 1]:
        cited = [sid for issue_id in issue_ids for sid in by_issue.get(str(issue_id), [])]
        points.append(AskAnswerPoint(text=text, source_ids=list(dict.fromkeys(cited))[:6]))
    headline = result.insight_lines[0][0] if result.insight_lines else "No findings."
    return _tidy(AskAnswer(headline=headline, points=points))


def no_results_answer(resolved: ResolvedPlan, scope_name: str | None) -> AskAnswer:
    where = f" in {scope_name}" if scope_name else " in this workspace"
    noun = _issue_noun(resolved)
    if resolved.plan.intent == AskIntent.supplier_issue_summary:
        noun = "suppliers with matching issues"
    return _tidy(AskAnswer(
        headline=f"No {noun} found{where}.",
        points=[
            AskAnswerPoint(
                text="This reflects the latest reconciliation of each persisted transaction."
            )
        ],
    ))


# ---------------------------------------------------------------------------
# Grounding guard for model output
# ---------------------------------------------------------------------------

def ungrounded_numbers(answer: ComposedAnswer, context: dict[str, Any]) -> set[Decimal]:
    """Numbers in the model's answer that do not exist in the grounded context."""
    return ungrounded([answer.headline, *(point.text for point in answer.points)], context)


def accept_model_answer(
    composed: ComposedAnswer, context: dict[str, Any], valid_source_ids: set[str]
) -> AskAnswer | None:
    headline = composed.headline.strip()
    if not headline or ungrounded_numbers(composed, context):
        return None
    points = [
        AskAnswerPoint(
            text=point.text.strip(),
            source_ids=[sid for sid in dict.fromkeys(point.source_ids) if sid in valid_source_ids],
        )
        for point in composed.points
        if point.text.strip()
    ][:MAX_POINTS]
    return _tidy(AskAnswer(headline=headline, points=points))


# ---------------------------------------------------------------------------
# Follow-ups and conversation state
# ---------------------------------------------------------------------------


INSIGHT_FOLLOW_UPS = [
    ("What changed in the last 30 days?", AskIntent.overview_summary),
    ("Show the priority queue.", AskIntent.priority_queue),
    ("Which recurring patterns should I review?", AskIntent.recurring_patterns),
    ("Are price discrepancies increasing?", AskIntent.exception_trend),
    ("Are there any anomaly signals?", AskIntent.anomaly_signals),
]


def follow_ups(
    resolved: ResolvedPlan,
    result: QueryResult,
    built: BuiltResult,
    scoped: bool,
) -> list[str]:
    suggestions: list[str] = []
    intent = resolved.plan.intent
    if result.result_kind == "insights":
        if built.issues and not scoped:
            suggestions.append(f"Why is {built.issues[0].transaction_name} flagged?")
        for text, source_intent in INSIGHT_FOLLOW_UPS:
            if source_intent != intent and len(suggestions) < 3:
                suggestions.append(text)
        return suggestions[:3]
    if resolved.focus is not None:
        supplier = resolved.focus.supplier
        if not scoped and supplier:
            suggestions.append(_sentence(f"Show open issues for {supplier}"))
        if resolved.focus.resolved_issues:
            suggestions.append("Show resolved issues for this transaction."
                               if scoped else f"Show resolved issues for {resolved.focus.name}.")
    elif built.issues:
        first = built.issues[0]
        if not scoped:
            suggestions.append(f"Why is {first.transaction_name} flagged?")
            if first.supplier and not resolved.plan.filters.supplier:
                suggestions.append(_sentence(f"Show open issues for {first.supplier}"))
    elif built.suppliers and built.suppliers[0].open_issue_count:
        suggestions.append(_sentence(f"Show open issues for {built.suppliers[0].supplier}"))
    elif built.transactions and not scoped:
        flagged = next((t for t in built.transactions if t.open_issue_count), None)
        if flagged:
            suggestions.append(f"Why is {flagged.name} flagged?")

    defaults = (
        ["Why is this transaction flagged?", "Which documents are attached?"]
        if scoped
        else [
            "What needs my attention?",
            "Show high-severity issues.",
            "Which suppliers have unresolved discrepancies?",
        ]
    )
    for item in defaults:
        if len(suggestions) >= 3:
            break
        if item not in suggestions and not (
            item == "What needs my attention?" and intent == AskIntent.list_open_issues
        ) and not (
            item == "Show high-severity issues." and intent == AskIntent.list_high_severity_issues
        ) and not (
            item == "Which suppliers have unresolved discrepancies?"
            and intent == AskIntent.supplier_issue_summary
        ):
            suggestions.append(item)
    return suggestions[:3]


def _drop_asked(suggestions: list[str], question: str) -> list[str]:
    asked = question.strip().rstrip(".?!").lower()
    return [s for s in suggestions if s.strip().rstrip(".?!").lower() != asked]


def conversation_state(
    question: str, resolved: ResolvedPlan, built: BuiltResult
) -> AskConversationContext:
    entities: list[AskEntity] = []
    seen: set[str] = set()
    for row in built.transactions[:8]:
        if str(row.transaction_id) not in seen:
            seen.add(str(row.transaction_id))
            entities.append(
                AskEntity(kind="transaction", id=str(row.transaction_id), label=row.name[:160])
            )
    suppliers = [row.supplier for row in built.suppliers] + [
        row.supplier for row in built.transactions if row.supplier
    ]
    for supplier in suppliers:
        if len([e for e in entities if e.kind == "supplier"]) >= 4:
            break
        if supplier and supplier.lower() not in seen:
            seen.add(supplier.lower())
            entities.append(AskEntity(kind="supplier", id=supplier[:160], label=supplier[:160]))
    return AskConversationContext(
        previous_question=question[:500],
        previous_intent=resolved.plan.intent,
        entities=entities[:12],
    )


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------


def _planner_conversation(
    context: AskConversationContext | None, scope: TransactionSnap | None
) -> dict[str, Any]:
    return {
        "scoped_to_transaction": scope.name if scope else None,
        "previous_question": context.previous_question if context else None,
        "previous_intent": context.previous_intent.value
        if context and context.previous_intent
        else None,
        "previous_entities": [
            {"kind": entity.kind, "label": entity.label}
            for entity in (context.entities if context else [])
        ],
    }


async def _plan(
    question: str,
    snapshot: WorkspaceSnapshot,
    scope: TransactionSnap | None,
    context: AskConversationContext | None,
    model: AskModel | None,
) -> tuple[PlannerOutput, bool]:
    """Returns (plan, used_model)."""
    if model is not None:
        try:
            planned = await run_in_threadpool(
                model.plan, question, _planner_conversation(context, scope)
            )
            return planned, True
        except Exception:  # noqa: BLE001 — never surface model errors to users
            logger.exception("Ask planner model call failed; using keyword planner.")
    return (
        heuristic_plan(
            question,
            suppliers=snapshot.suppliers(),
            context=context,
            scoped=scope is not None,
        ),
        False,
    )


async def run_ask(
    session: AsyncSession,
    request: AskRequest,
    model: AskModel | None,
    workspace_id: UUID,
) -> AsyncIterator[dict[str, Any]]:
    """Yield progress stages, then ``{"stage": "done", "response": AskResponse}``.

    Read-only: this pipeline only calls ``load_snapshot`` (fixed SELECTs), and
    the session is rolled back at the end so nothing could ever be persisted.
    """
    question = " ".join(request.question.split())
    if not question:
        raise AskError(400, "Ask a question about your transactions or documents.")

    try:
        yield {"stage": "interpreting"}
        snapshot = await load_snapshot(session, workspace_id, request.transaction_id)
        scope = snapshot.transaction(request.transaction_id)
        if request.transaction_id is not None and scope is None:
            raise AskError(404, "Transaction not found.")
        scope_name = scope.name if scope else None
        thresholds, _ = await load_settings(session, workspace_id)

        raw_plan, used_model = await _plan(question, snapshot, scope, request.context, model)
        notices: list[str] = []
        if model is None:
            notices.append(
                "Model not configured — question interpreted by keyword rules and "
                "answer composed directly from records."
            )
        elif not used_model:
            notices.append("The question was interpreted by keyword rules.")

        resolved = resolve_plan(
            raw_plan, snapshot=snapshot, scope=scope, context=request.context
        )
        notices.extend(resolved.notices)

        yield {"stage": "searching"}
        if resolved.plan.intent in INTELLIGENCE_INTENTS and resolved.outcome == "ok":
            result = run_intelligence_query(snapshot, resolved.plan, thresholds, utcnow())
        else:
            result = run_query(snapshot, resolved.plan)
        built = build_rows(result)

        answer_mode = "records"
        if resolved.outcome == "unsupported":
            outcome = "unsupported"
            answer = AskAnswer(
                headline=UNSUPPORTED_MESSAGE,
                points=[
                    AskAnswerPoint(
                        text="Ask what needs attention, which suppliers have open "
                        "discrepancies, or why a specific transaction is flagged."
                    )
                ],
            )
            notices = [n for n in notices if not n.startswith("Model not configured")]
        elif resolved.outcome == "not_found":
            outcome = "not_found"
            answer = AskAnswer(headline=resolved.message or "No matching records found.")
        elif result.total_matches == 0 and resolved.focus is None:
            outcome = "no_results"
            answer = no_results_answer(resolved, scope_name)
        else:
            outcome = "answered"
            answer = (
                insight_answer(result, built)
                if result.result_kind == "insights"
                else compose_from_records(resolved, result, built, scope_name)
            )
            if model is not None:
                yield {"stage": "composing"}
                context = grounded_context(
                    question=question,
                    plan=resolved.plan,
                    scope_name=scope_name,
                    result=result,
                    built=built,
                )
                try:
                    composed = await run_in_threadpool(model.compose, context)
                    accepted = accept_model_answer(composed, context, built.registry.ids())
                except Exception:  # noqa: BLE001
                    logger.exception("Ask answer model call failed.")
                    accepted = None
                    notices.append("Answer composed directly from records — the model was unavailable.")
                else:
                    if accepted is None:
                        notices.append(
                            "Answer composed directly from records — the model's wording "
                            "referenced values not found in the records."
                        )
                if accepted is not None:
                    answer = accepted
                    answer_mode = "model"

        shown = built.shown(result.result_kind)
        if outcome == "answered" and result.result_kind != "insights" and result.total_matches > shown:
            notices.append(f"Showing {shown} of {result.total_matches} matching records.")
        if any(not s.snippet_available for s in built.registry.sources) or any(
            not row.source_ids for row in built.issues
        ):
            notices.append("Some values have no captured source snippet.")

        response = AskResponse(
            question=question,
            intent=resolved.plan.intent,
            filters=resolved.plan.filters,
            scope="transaction" if scope else "workspace",
            scope_transaction_id=scope.id if scope else None,
            scope_transaction_name=scope_name,
            outcome=outcome,
            answer=answer,
            answer_mode=answer_mode,
            metrics=result.metrics,
            result_kind=result.result_kind if outcome == "answered" else "none",
            issues=built.issues if outcome == "answered" else [],
            transactions=built.transactions if outcome == "answered" else [],
            suppliers=built.suppliers if outcome == "answered" else [],
            sources=built.registry.sources if outcome == "answered" else [],
            notices=notices,
            follow_up_suggestions=_drop_asked(
                follow_ups(resolved, result, built, scope is not None), question
            )
            if outcome in {"answered", "no_results"}
            else (
                ["What needs my attention?", "Show recent transactions."]
                if scope is None
                else ["Why is this transaction flagged?"]
            ),
            context=conversation_state(question, resolved, built),
        )
        yield {"stage": "done", "response": response}
    finally:
        await session.rollback()


async def ask(
    session: AsyncSession, request: AskRequest, model: AskModel | None, workspace_id: UUID
) -> AskResponse:
    async for event in run_ask(session, request, model, workspace_id):
        if event["stage"] == "done":
            return event["response"]
    raise AskError(500, "Ask cermat. could not produce an answer.")


# ---------------------------------------------------------------------------
# Suggestions
# ---------------------------------------------------------------------------


def build_suggestions(snapshot: WorkspaceSnapshot, scope: TransactionSnap | None) -> AskSuggestions:
    if scope is not None:
        suggestions = ["Why is this transaction flagged?"]
        families = {issue.family for issue in scope.open_issues}
        if "price" in families:
            suggestions.append("How much was the price variance?")
        if "quantity" in families:
            suggestions.append("Show the quantity mismatches.")
        if scope.resolved_issues:
            suggestions.append("Show resolved issues for this transaction.")
        suggestions.append("Which documents are attached?")
        return AskSuggestions(scope="transaction", suggestions=suggestions[:5])

    pairs = snapshot.issue_pairs()
    open_pairs = [(t, i) for t, i in pairs if i.status == "open"]
    suggestions = ["What needs my attention?"]
    if any(i.severity == "high" for _, i in open_pairs):
        suggestions.append("Show high-severity issues.")
    if any(i.family == "price" for _, i in open_pairs):
        suggestions.append("Which suppliers have unresolved price discrepancies?")
    elif open_pairs:
        suggestions.append("Which suppliers have unresolved discrepancies?")
    flagged = next((t for t in snapshot.transactions if t.open_issues), None)
    if flagged is not None:
        suggestions.append(f"Why is {flagged.name} flagged?")
    if any(i.status == "resolved" for _, i in pairs):
        suggestions.append("Which transactions were resolved recently?")
    for fallback in (
        "Show invoice price mismatches.",
        "Show recent transactions.",
        "Which transactions are missing documents?",
    ):
        if len(suggestions) >= 5:
            break
        if fallback not in suggestions:
            suggestions.append(fallback)
    return AskSuggestions(scope="workspace", suggestions=suggestions[:5])
