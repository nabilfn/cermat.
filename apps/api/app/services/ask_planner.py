"""Question → validated ``AskPlan``.

The model (or the keyword fallback) proposes a ``PlannerOutput``. This module
then resolves every entity it mentions against persisted records, so a plan
only ever carries database IDs and canonical names — never free text that a
handler would have to trust.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from uuid import UUID

from app.schemas import (
    AskConversationContext,
    AskFilters,
    AskIntent,
    AskPlan,
)
from app.services.ask_llm import PlannerOutput
from app.services.ask_queries import (
    TRANSACTION_INTENTS,
    TransactionSnap,
    WorkspaceSnapshot,
    resolve_supplier,
    resolve_transaction_ref,
)
from app.services.reconciliation import _normalise_supplier

DEFAULT_LIMIT = 20

_WRITE_REQUEST = re.compile(
    r"^\s*(?:please\s+|can you\s+|could you\s+)?"
    r"(?:resolve|reopen|delete|remove|update|edit|change|set|mark|close|approve|"
    r"reject|fix|correct|drop|insert|create|rename)\b",
    re.IGNORECASE,
)
_DOCUMENT_REF = re.compile(r"\b[A-Za-z]{2,5}-?\d[\w-]*\b")
_DEICTIC = re.compile(
    r"\b(this|it|that one|that transaction|this transaction|the first one|same one)\b",
    re.IGNORECASE,
)


def _has(text: str, *patterns: str) -> bool:
    return any(re.search(pattern, text) for pattern in patterns)


def _issue_type_from(text: str) -> str | None:
    if _has(text, r"\bprice", r"\bpric"):
        return "price"
    if _has(text, r"quantit", r"\bqty\b", r"\bunits?\b", r"deliver"):
        return "quantity"
    if _has(text, r"currenc"):
        return "currency"
    if _has(text, r"arithmetic", r"line total", r"add up"):
        return "arithmetic"
    return None


def _find_supplier(text: str, suppliers: list[str]) -> str | None:
    normalised = f" {_normalise_supplier(text)} "
    for supplier in sorted(suppliers, key=len, reverse=True):
        key = _normalise_supplier(supplier)
        if key and f" {key} " in normalised:
            return supplier
    return None


def heuristic_plan(
    question: str,
    *,
    suppliers: list[str],
    context: AskConversationContext | None,
    scoped: bool,
) -> PlannerOutput:
    """Keyword planner. Deterministic; used when the model is unavailable."""
    text = question.lower()
    blank = dict(
        supplier=None,
        severity=None,
        status=None,
        transaction_ref=None,
        issue_type=None,
        quantity_direction=None,
        search=None,
        limit=None,
    )

    def out(intent: AskIntent, **filters) -> PlannerOutput:
        return PlannerOutput(intent=intent, **{**blank, **filters})

    if _WRITE_REQUEST.search(question):
        return out(AskIntent.unsupported)

    status = (
        "open"
        if _has(text, r"unresolved", r"\bopen\b", r"outstanding")
        else "resolved"
        if _has(text, r"\bresolved\b", r"\bclosed\b")
        else None
    )
    supplier = _find_supplier(question, suppliers)
    if supplier is None and context:
        for entity in context.entities:
            if entity.kind == "supplier" and _normalise_supplier(entity.label) in _normalise_supplier(text):
                supplier = entity.label
                break

    reference_match = _DOCUMENT_REF.search(question)
    reference = reference_match.group(0) if reference_match else None
    if reference is None and context:
        for entity in context.entities:
            if entity.kind == "transaction" and entity.label.lower() in text:
                reference = entity.label
                break
    deictic = bool(_DEICTIC.search(question))
    if reference is None and deictic and not scoped and context:
        transactions = [e for e in context.entities if e.kind == "transaction"]
        if len(transactions) == 1:
            reference = transactions[0].label

    explain = _has(
        text, r"\bwhy\b", r"flag", r"wrong", r"variance", r"explain", r"issue",
        r"problem", r"discrepanc", r"mismatch",
    )
    if reference or (scoped and deictic):
        if reference and not explain and _has(text, r"\bfind\b", r"\bsearch\b"):
            return out(AskIntent.transaction_search, search=reference)
        return out(
            AskIntent.transaction_explanation if explain else AskIntent.transaction_details,
            transaction_ref=reference,
            status=status,
        )

    if _has(text, r"supplier", r"vendor") and _has(
        text, r"\bwhich\b", r"\bmost\b", r"\brank", r"\bcompare", r"\bworst\b", r"\btop\b"
    ):
        return out(
            AskIntent.supplier_issue_summary,
            issue_type=_issue_type_from(text),
            status=status,
        )

    if _has(text, r"supplier (name )?mismatch", r"supplier names? differ", r"different supplier"):
        return out(AskIntent.supplier_mismatches, status=status, supplier=supplier)
    if _has(text, r"currenc"):
        return out(AskIntent.currency_mismatches, status=status, supplier=supplier)
    if _has(text, r"arithmetic", r"line total", r"add up"):
        return out(AskIntent.arithmetic_mismatches, status=status, supplier=supplier)
    if _has(text, r"\bpric"):
        return out(AskIntent.price_discrepancies, status=status, supplier=supplier)
    if _has(text, r"quantit", r"\bqty\b", r"\bunits\b", r"delivered", r"short"):
        direction = None
        if _has(text, r"exceed", r"more than", r"higher than", r"greater than", r"\bover") and _has(
            text, r"invoic"
        ):
            direction = "invoice_over_delivery"
        elif _has(text, r"short", r"less than", r"fewer", r"under"):
            direction = "delivery_short_of_order"
        return out(
            AskIntent.quantity_discrepancies,
            status=status,
            supplier=supplier,
            quantity_direction=direction,
        )
    if _has(text, r"missing", r"incomplete", r"not (yet )?reconciled", r"unreconciled"):
        return out(AskIntent.missing_documents, supplier=supplier)
    if _has(text, r"\bhigh\b", r"critical", r"urgent", r"severe"):
        return out(AskIntent.list_high_severity_issues, status=status, supplier=supplier)
    if _has(text, r"\bresolved\b", r"\bclosed\b"):
        if _has(text, r"transaction"):
            return out(AskIntent.resolved_transactions, supplier=supplier)
        return out(AskIntent.list_resolved_issues, supplier=supplier)
    if _has(text, r"\brecent", r"\blatest\b", r"\blast\b", r"\bnewest\b"):
        return out(AskIntent.recent_transactions, supplier=supplier)
    if supplier:
        return out(AskIntent.supplier_issues, supplier=supplier, status=status)
    if _has(
        text, r"attention", r"\bopen\b", r"unresolved", r"review", r"issues?\b",
        r"exceptions?", r"flag", r"problems?", r"discrepanc", r"mismatch",
    ):
        return out(AskIntent.list_open_issues, status=status)
    if _has(
        text, r"summar", r"overview", r"status", r"how many", r"how are",
        r"transactions?\b", r"invoices?\b", r"workspace",
    ):
        return out(AskIntent.general_summary)
    return out(AskIntent.unsupported)


@dataclass
class ResolvedPlan:
    plan: AskPlan
    outcome: str = "ok"  # "ok" | "not_found" | "unsupported"
    message: str | None = None
    notices: list[str] = field(default_factory=list)
    focus: TransactionSnap | None = None


def _entity_transaction(
    snapshot: WorkspaceSnapshot,
    context: AskConversationContext | None,
    reference: str | None,
) -> TransactionSnap | None:
    """Map a follow-up reference to a transaction ID from the previous answer."""
    if context is None:
        return None
    candidates = [e for e in context.entities if e.kind == "transaction"]
    if reference:
        candidates = [e for e in candidates if e.label.strip().lower() == reference.strip().lower()]
    elif len(candidates) != 1:
        return None
    for entity in candidates:
        try:
            txn = snapshot.transaction(UUID(entity.id))
        except ValueError:
            continue
        if txn is not None:
            return txn
    return None


def resolve_plan(
    raw: PlannerOutput,
    *,
    snapshot: WorkspaceSnapshot,
    scope: TransactionSnap | None,
    context: AskConversationContext | None,
) -> ResolvedPlan:
    intent = raw.intent
    limit = raw.limit if raw.limit and 1 <= raw.limit <= 50 else DEFAULT_LIMIT
    filters = AskFilters(
        severity=raw.severity,
        status=raw.status,
        issue_type=raw.issue_type,
        quantity_direction=raw.quantity_direction,
        search=(raw.search or "").strip()[:160] or None,
    )

    def done(**kwargs) -> ResolvedPlan:
        return ResolvedPlan(plan=AskPlan(intent=intent, filters=filters, limit=limit), **kwargs)

    if intent == AskIntent.unsupported:
        return done(outcome="unsupported")

    notices: list[str] = []

    # Supplier → canonical name as stored in extracted documents.
    if raw.supplier:
        canonical = resolve_supplier(snapshot, raw.supplier)
        if canonical is None:
            where = f" in {scope.name}" if scope else " in this workspace"
            return done(
                outcome="not_found",
                message=f"No supplier matching “{raw.supplier.strip()[:80]}” was found{where}.",
            )
        filters.supplier = canonical

    # Transaction → ID.
    focus: TransactionSnap | None = None
    reference = (raw.transaction_ref or "").strip()[:160] or None
    if scope is not None:
        filters.transaction_id = scope.id
        focus = scope
        if reference:
            matches = resolve_transaction_ref(snapshot, reference)
            if not matches:
                notices.append(
                    f"Answer is limited to {scope.name}. Clear the scope to search "
                    "the whole workspace."
                )
    elif reference:
        filters.transaction_ref = reference
        matches = resolve_transaction_ref(snapshot, reference)
        entity = _entity_transaction(snapshot, context, reference)
        if entity is not None:
            matches = [entity]
        if not matches:
            return done(
                outcome="not_found",
                message=f"No transaction or document numbered “{reference}” was found in this workspace.",
            )
        if len(matches) > 1:
            if intent in TRANSACTION_INTENTS:
                intent = AskIntent.transaction_search
                filters.search = reference
                notices.append(f"“{reference}” matches {len(matches)} transactions.")
        else:
            focus = matches[0]
            filters.transaction_id = focus.id
    elif intent in TRANSACTION_INTENTS:
        focus = _entity_transaction(snapshot, context, None)
        if focus is None:
            return done(
                outcome="not_found",
                message=(
                    "Name the transaction by its PO, DO or invoice number, or open it "
                    "from Review history and ask from there."
                ),
            )
        filters.transaction_id = focus.id

    if intent in TRANSACTION_INTENTS and focus is None:
        return done(outcome="not_found", message="That transaction could not be found.")

    return ResolvedPlan(
        plan=AskPlan(intent=intent, filters=filters, limit=limit),
        notices=notices,
        focus=focus if intent in TRANSACTION_INTENTS else None,
    )
