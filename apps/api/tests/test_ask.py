from __future__ import annotations

import asyncio
import inspect
import re
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock
from uuid import UUID, uuid4

from pydantic import ValidationError

from app.schemas import (
    AskConversationContext,
    AskEntity,
    AskIntent,
    AskPlan,
    AskRequest,
    DocumentType,
    EvidenceReference,
    MatchedLine,
)
from app.services import ask as ask_service
from app.services.ask import accept_model_answer, build_suggestions
from app.services.ask_context import build_rows, grounded_context, transaction_context
from app.services.ask_llm import ComposedAnswer, ComposedPoint, PlannerOutput
from app.services.ask_planner import heuristic_plan, resolve_plan
from app.services.ask_queries import (
    INTENT_HANDLERS,
    DocumentSnap,
    IssueSnap,
    TransactionSnap,
    WorkspaceSnapshot,
    calculate_variance,
    run_query,
)

NOW = datetime(2026, 9, 18, 9, 0, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# Fixture workspace
# ---------------------------------------------------------------------------


def _doc(doc_type: str, number: str, supplier: str, total: float | None = None) -> DocumentSnap:
    return DocumentSnap(
        id=uuid4(),
        filename=f"{number.lower()}.pdf",
        document_type=doc_type,
        status="extracted",
        document_number=number,
        supplier_name=supplier,
        currency="MYR",
        total=total,
        overall_confidence=0.94,
        evidence=[
            {"field_path": "line_items.0.unit_price", "source_text": f"Unit Price {number}", "page": 1, "confidence": 0.91},
            {"field_path": "line_items.0.quantity", "source_text": f"Qty {number}", "page": 1, "confidence": 0.88},
            {"field_path": "total", "source_text": f"Total {total}", "page": 1, "confidence": 0.97},
        ],
    )


def _ref(doc: DocumentSnap, field_path: str, value: str, text: str | None = None) -> EvidenceReference:
    return EvidenceReference(
        document_id=doc.id,
        filename=doc.filename,
        document_type=DocumentType(doc.document_type),
        field_path=field_path,
        source_text=text or f"{doc.document_number} {field_path}",
        page=1,
        value=value,
    )


def _issue(
    txn_id: UUID,
    code: str,
    severity: str,
    *,
    status: str = "open",
    expected: str | None = None,
    actual: str | None = None,
    item: str | None = "Office Chair",
    sources: list[EvidenceReference] | None = None,
    note: str | None = None,
    age_hours: int = 1,
) -> IssueSnap:
    stamp = NOW - timedelta(hours=age_hours)
    return IssueSnap(
        id=uuid4(),
        transaction_id=txn_id,
        code=code,
        title=code.replace("_", " ").capitalize(),
        severity=severity,
        status=status,
        item_description=item,
        expected=expected,
        actual=actual,
        delta=None,
        explanation="Rule explanation.",
        sources=sources or [],
        resolution_note=note,
        resolved_at=stamp if status == "resolved" else None,
        created_at=stamp,
        updated_at=stamp,
    )


def build_workspace() -> WorkspaceSnapshot:
    # T1 — ABC Supplies, price + over-invoiced quantity, both open.
    t1 = TransactionSnap(
        id=uuid4(), name="PO-2026-001", status="review_required",
        created_at=NOW - timedelta(days=2), updated_at=NOW - timedelta(hours=1),
        reconciled_at=NOW - timedelta(hours=1),
    )
    po1 = _doc("purchase_order", "PO-2026-001", "ABC Supplies Sdn. Bhd.", 420.0)
    do1 = _doc("delivery_order", "DO-0933", "ABC Supplies Sdn. Bhd.")
    inv1 = _doc("invoice", "INV-4482", "ABC Supplies Sdn Bhd", 440.0)
    t1.documents = [po1, do1, inv1]
    t1.lines = [
        MatchedLine(
            key="chair", description="Office Chair", sku="CHAIR-1",
            po_quantity=10, delivered_quantity=8, invoice_quantity=10,
            po_unit_price=42, invoice_unit_price=44, status="review_required",
        )
    ]
    t1.issues = [
        _issue(
            t1.id, "invoice_delivery_quantity_variance", "high",
            expected="8", actual="10",
            sources=[
                _ref(do1, "line_items.0.quantity", "8"),
                _ref(inv1, "line_items.0.quantity", "10"),
            ],
        ),
        _issue(
            t1.id, "invoice_price_variance", "medium",
            expected="MYR 42.00", actual="MYR 44.00",
            sources=[
                _ref(po1, "line_items.0.unit_price", "MYR 42.00", "Unit Price RM42.00"),
                _ref(inv1, "line_items.0.unit_price", "MYR 44.00", "Unit Price RM44.00"),
            ],
        ),
    ]

    # T2 — Delta Office, supplier mismatch open + short delivery resolved.
    t2 = TransactionSnap(
        id=uuid4(), name="Chairs September", status="review_required",
        created_at=NOW - timedelta(days=5), updated_at=NOW - timedelta(hours=3),
        reconciled_at=NOW - timedelta(hours=3),
    )
    po2 = _doc("purchase_order", "PO-2026-103", "Delta Office Sdn Bhd", 900.0)
    do2 = _doc("delivery_order", "DO-1201", "Delta Office Sdn Bhd")
    inv2 = _doc("invoice", "INV-9001", "Delta Offices Trading", 900.0)
    t2.documents = [po2, do2, inv2]
    t2.issues = [
        _issue(
            t2.id, "supplier_mismatch", "medium", item=None,
            expected="Delta Office Sdn Bhd", actual="Delta Offices Trading",
            sources=[_ref(po2, "supplier_name", "Delta Office Sdn Bhd"), _ref(inv2, "supplier_name", "Delta Offices Trading")],
        ),
        _issue(
            t2.id, "delivery_quantity_variance", "medium", status="resolved",
            expected="12", actual="10", note="Balance shipped separately",
            sources=[_ref(po2, "line_items.0.quantity", "12"), _ref(do2, "line_items.0.quantity", "10")],
            age_hours=2,
        ),
    ]

    # T3 — ABC Supplies, clean match.
    t3 = TransactionSnap(
        id=uuid4(), name="Paper restock", status="matched",
        created_at=NOW - timedelta(days=1), updated_at=NOW - timedelta(hours=6),
        reconciled_at=NOW - timedelta(hours=6),
    )
    t3.documents = [
        _doc("purchase_order", "PO-2026-050", "ABC Supplies", 100.0),
        _doc("delivery_order", "DO-0950", "ABC Supplies"),
        _doc("invoice", "INV-5000", "ABC Supplies", 100.0),
    ]

    # T4 — incomplete, never reconciled.
    t4 = TransactionSnap(
        id=uuid4(), name="Toner order", status="collecting",
        created_at=NOW - timedelta(hours=12), updated_at=NOW - timedelta(hours=12),
    )
    t4.documents = [_doc("purchase_order", "PO-2026-077", "Inkwell Trading", 250.0)]

    return WorkspaceSnapshot(transactions=[t1, t2, t3, t4])


def plan(intent: AskIntent, **filters) -> AskPlan:
    return AskPlan.model_validate({"intent": intent, "filters": filters})


def planner(intent: AskIntent, **fields) -> PlannerOutput:
    base = dict(
        supplier=None, severity=None, status=None, transaction_ref=None,
        issue_type=None, quantity_direction=None, search=None, limit=None,
    )
    return PlannerOutput(intent=intent, **{**base, **fields})


class FakeSession:
    def __init__(self) -> None:
        self.rolled_back = False

    async def rollback(self) -> None:
        self.rolled_back = True


class FakeModel:
    """Records exactly what the model layer receives."""

    def __init__(self, planned: PlannerOutput, composed: ComposedAnswer | None = None) -> None:
        self.planned = planned
        self.composed = composed
        self.plan_calls: list[tuple] = []
        self.compose_calls: list[dict] = []

    def plan(self, question, conversation):
        self.plan_calls.append((question, conversation))
        return self.planned

    def compose(self, context):
        self.compose_calls.append(context)
        if self.composed is None:
            raise RuntimeError("model down")
        return self.composed


def run_ask(snapshot: WorkspaceSnapshot, request: AskRequest, model=None):
    async def fake_load(_session, transaction_id=None):
        if transaction_id is None:
            return snapshot
        return WorkspaceSnapshot(
            transactions=[t for t in snapshot.transactions if t.id == transaction_id]
        )

    session = FakeSession()
    with mock.patch.object(ask_service, "load_snapshot", fake_load):
        response = asyncio.run(ask_service.ask(session, request, model))
    return response, session


# ---------------------------------------------------------------------------
# Query handlers
# ---------------------------------------------------------------------------


class QueryHandlerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.ws = build_workspace()
        self.t1, self.t2, self.t3, self.t4 = self.ws.transactions

    def test_open_issue_query(self) -> None:
        result = run_query(self.ws, plan(AskIntent.list_open_issues))
        self.assertEqual(result.result_kind, "issues")
        self.assertEqual(result.metrics.open_issue_count, 3)
        self.assertEqual(result.metrics.transaction_count, 2)
        self.assertTrue(all(issue.status == "open" for _, issue in result.issues))
        # Highest severity first.
        self.assertEqual(result.issues[0][1].severity, "high")

    def test_high_severity_query(self) -> None:
        result = run_query(self.ws, plan(AskIntent.list_high_severity_issues))
        self.assertEqual(len(result.issues), 1)
        txn, issue = result.issues[0]
        self.assertEqual(txn.name, "PO-2026-001")
        self.assertEqual(issue.code, "invoice_delivery_quantity_variance")

    def test_price_mismatch_query(self) -> None:
        result = run_query(self.ws, plan(AskIntent.price_discrepancies))
        self.assertEqual([i.code for _, i in result.issues], ["invoice_price_variance"])

    def test_quantity_mismatch_query_with_direction(self) -> None:
        any_status = run_query(self.ws, plan(AskIntent.quantity_discrepancies, status="any"))
        self.assertEqual(len(any_status.issues), 2)

        over = run_query(
            self.ws,
            plan(AskIntent.quantity_discrepancies, quantity_direction="invoice_over_delivery"),
        )
        self.assertEqual(len(over.issues), 1)
        self.assertEqual(over.issues[0][0].name, "PO-2026-001")

        short = run_query(
            self.ws,
            plan(
                AskIntent.quantity_discrepancies,
                status="any",
                quantity_direction="delivery_short_of_order",
            ),
        )
        self.assertEqual([t.name for t, _ in short.issues], ["Chairs September"])

    def test_resolved_issue_query_carries_note(self) -> None:
        result = run_query(self.ws, plan(AskIntent.list_resolved_issues))
        self.assertEqual(len(result.issues), 1)
        self.assertEqual(result.issues[0][1].resolution_note, "Balance shipped separately")

    def test_supplier_specific_question(self) -> None:
        result = run_query(
            self.ws, plan(AskIntent.supplier_issues, supplier="ABC Supplies Sdn. Bhd.")
        )
        self.assertEqual({t.name for t, _ in result.issues}, {"PO-2026-001"})
        self.assertEqual(result.metrics.open_issue_count, 2)

    def test_supplier_summary_with_price_filter(self) -> None:
        result = run_query(
            self.ws,
            plan(AskIntent.supplier_issue_summary, issue_type="price", status="open"),
        )
        self.assertEqual(result.result_kind, "suppliers")
        self.assertEqual([row.supplier for row in result.suppliers], ["ABC Supplies Sdn. Bhd."])

    def test_supplier_summary_ranks_by_open_issues(self) -> None:
        result = run_query(self.ws, plan(AskIntent.supplier_issue_summary))
        self.assertEqual(result.suppliers[0].open_issue_count, 2)
        self.assertEqual(result.suppliers[0].high_open_count, 1)

    def test_transaction_scope_filters_results(self) -> None:
        result = run_query(
            self.ws, plan(AskIntent.list_open_issues, transaction_id=str(self.t2.id))
        )
        self.assertTrue(result.issues)
        self.assertTrue(all(txn.id == self.t2.id for txn, _ in result.issues))

    def test_transaction_explanation_includes_resolved(self) -> None:
        result = run_query(
            self.ws, plan(AskIntent.transaction_explanation, transaction_id=str(self.t2.id))
        )
        self.assertIs(result.focus, self.t2)
        self.assertEqual({i.status for _, i in result.issues}, {"open", "resolved"})

    def test_missing_documents(self) -> None:
        result = run_query(self.ws, plan(AskIntent.missing_documents))
        self.assertEqual([t.name for t in result.transactions], ["Toner order"])

    def test_no_result_query(self) -> None:
        result = run_query(self.ws, plan(AskIntent.currency_mismatches))
        self.assertEqual(result.total_matches, 0)
        self.assertEqual(result.issues, [])

    def test_unsupported_intent_returns_nothing(self) -> None:
        result = run_query(self.ws, plan(AskIntent.unsupported))
        self.assertEqual(result.result_kind, "none")
        self.assertEqual(result.total_matches, 0)

    def test_every_intent_maps_to_a_fixed_handler(self) -> None:
        self.assertEqual(set(INTENT_HANDLERS), set(AskIntent))
        for handler in INTENT_HANDLERS.values():
            self.assertTrue(callable(handler))

    def test_unknown_intent_is_rejected_by_schema(self) -> None:
        with self.assertRaises(ValidationError):
            AskPlan.model_validate({"intent": "DROP TABLE review_issues"})
        with self.assertRaises(ValidationError):
            AskPlan.model_validate({"intent": "list_open_issues", "filters": {"sql": "1=1"}, "limit": 10_000})

    def test_variance_is_calculated_in_python(self) -> None:
        price = next(i for i in self.t1.issues if i.family == "price")
        variance = calculate_variance(self.t1, price)
        self.assertEqual(variance.delta, 2.0)
        self.assertEqual(variance.percentage, 4.76)
        self.assertEqual(variance.currency, "MYR")
        self.assertEqual(variance.billed_impact, 20.0)  # +2.00 × 10 invoiced units

        quantity = next(i for i in self.t1.issues if i.family == "quantity")
        variance = calculate_variance(self.t1, quantity)
        self.assertEqual((variance.kind, variance.delta, variance.percentage), ("quantity", 2.0, 25.0))


# ---------------------------------------------------------------------------
# Planning and entity resolution
# ---------------------------------------------------------------------------


class PlannerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.ws = build_workspace()
        self.suppliers = self.ws.suppliers()

    def intent(self, question: str, **kwargs) -> AskIntent:
        return heuristic_plan(
            question, suppliers=self.suppliers, context=kwargs.get("context"), scoped=kwargs.get("scoped", False)
        ).intent

    def test_keyword_planner_intents(self) -> None:
        cases = {
            "What needs my attention?": AskIntent.list_open_issues,
            "Show me high-severity issues.": AskIntent.list_high_severity_issues,
            "Which invoices have price discrepancies?": AskIntent.price_discrepancies,
            "Show transactions where invoiced quantity exceeded delivered quantity.": AskIntent.quantity_discrepancies,
            "Were there any supplier mismatches?": AskIntent.supplier_mismatches,
            "Which suppliers have unresolved price discrepancies?": AskIntent.supplier_issue_summary,
            "Which transactions were resolved recently?": AskIntent.resolved_transactions,
            "Why is PO-2026-001 flagged?": AskIntent.transaction_explanation,
            "What will Tesla stock do tomorrow?": AskIntent.unsupported,
            "Resolve all open issues": AskIntent.unsupported,
            "Delete transaction PO-2026-001": AskIntent.unsupported,
        }
        for question, expected in cases.items():
            with self.subTest(question=question):
                self.assertEqual(self.intent(question), expected)

    def test_quantity_direction_detected(self) -> None:
        raw = heuristic_plan(
            "Show transactions where invoiced quantity exceeded delivered quantity",
            suppliers=self.suppliers, context=None, scoped=False,
        )
        self.assertEqual(raw.quantity_direction, "invoice_over_delivery")

    def test_supplier_summary_picks_up_issue_type_and_status(self) -> None:
        raw = heuristic_plan(
            "Which suppliers have unresolved price discrepancies?",
            suppliers=self.suppliers, context=None, scoped=False,
        )
        self.assertEqual((raw.issue_type, raw.status), ("price", "open"))

    def test_transaction_ref_resolves_to_id(self) -> None:
        resolved = resolve_plan(
            planner(AskIntent.transaction_explanation, transaction_ref="inv-4482"),
            snapshot=self.ws, scope=None, context=None,
        )
        self.assertEqual(resolved.outcome, "ok")
        self.assertEqual(resolved.plan.filters.transaction_id, self.ws.transactions[0].id)

    def test_unknown_transaction_is_not_found(self) -> None:
        resolved = resolve_plan(
            planner(AskIntent.transaction_explanation, transaction_ref="PO-9999-999"),
            snapshot=self.ws, scope=None, context=None,
        )
        self.assertEqual(resolved.outcome, "not_found")

    def test_unknown_supplier_is_not_found(self) -> None:
        resolved = resolve_plan(
            planner(AskIntent.supplier_issues, supplier="Globex Corporation"),
            snapshot=self.ws, scope=None, context=None,
        )
        self.assertEqual(resolved.outcome, "not_found")

    def test_supplier_resolves_to_canonical_name(self) -> None:
        resolved = resolve_plan(
            planner(AskIntent.supplier_issues, supplier="abc supplies"),
            snapshot=self.ws, scope=None, context=None,
        )
        self.assertIn("ABC Supplies", resolved.plan.filters.supplier)

    def test_scope_overrides_any_model_supplied_reference(self) -> None:
        scope = self.ws.transactions[1]
        resolved = resolve_plan(
            planner(AskIntent.transaction_explanation, transaction_ref="PO-2026-001"),
            snapshot=WorkspaceSnapshot([scope]), scope=scope, context=None,
        )
        self.assertEqual(resolved.plan.filters.transaction_id, scope.id)
        self.assertTrue(resolved.notices)

    def test_follow_up_resolves_entity_by_id(self) -> None:
        t2 = self.ws.transactions[1]
        context = AskConversationContext(
            previous_question="What needs my attention?",
            entities=[AskEntity(kind="transaction", id=str(t2.id), label=t2.name)],
        )
        raw = heuristic_plan("Why is it flagged?", suppliers=self.suppliers, context=context, scoped=False)
        resolved = resolve_plan(raw, snapshot=self.ws, scope=None, context=context)
        self.assertEqual(resolved.plan.intent, AskIntent.transaction_explanation)
        self.assertEqual(resolved.plan.filters.transaction_id, t2.id)

    def test_follow_up_with_fabricated_entity_id_is_ignored(self) -> None:
        context = AskConversationContext(
            entities=[AskEntity(kind="transaction", id=str(uuid4()), label="Ghost")],
        )
        resolved = resolve_plan(
            planner(AskIntent.transaction_explanation), snapshot=self.ws, scope=None, context=context
        )
        self.assertEqual(resolved.outcome, "not_found")


# ---------------------------------------------------------------------------
# Grounded answers
# ---------------------------------------------------------------------------


class GroundedAnswerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.ws = build_workspace()
        self.t1 = self.ws.transactions[0]

    def context_for(self, intent: AskIntent, **filters):
        p = plan(intent, **filters)
        result = run_query(self.ws, p)
        built = build_rows(result)
        return grounded_context(question="q", plan=p, scope_name=None, result=result, built=built), built

    def test_context_only_contains_known_records(self) -> None:
        context, built = self.context_for(AskIntent.price_discrepancies)
        self.assertEqual([i["transaction"] for i in context["issues"]], ["PO-2026-001"])
        self.assertNotIn("Chairs September", str(context))
        self.assertEqual(context["issues"][0]["calculated_variance"]["difference_percent"], 4.76)
        cited = {sid for issue in context["issues"] for sid in issue["source_ids"]}
        self.assertEqual(cited, {s["id"] for s in context["sources"]})

    def test_sources_carry_page_snippet_and_confidence(self) -> None:
        _, built = self.context_for(AskIntent.price_discrepancies)
        sources = built.registry.sources
        self.assertEqual([s.label for s in sources], ["PO-2026-001 · p.1", "INV-4482 · p.1"])
        self.assertEqual(sources[0].source_text, "Unit Price RM42.00")
        self.assertEqual(sources[0].confidence, 0.91)

    def test_model_answer_with_invented_number_is_rejected(self) -> None:
        context, built = self.context_for(AskIntent.price_discrepancies)
        invented = ComposedAnswer(
            headline="Invoice INV-4482 is MYR 7.50 above the purchase order.",
            points=[],
        )
        self.assertIsNone(accept_model_answer(invented, context, built.registry.ids()))

    def test_model_answer_with_known_numbers_and_unknown_citation(self) -> None:
        context, built = self.context_for(AskIntent.price_discrepancies)
        grounded = ComposedAnswer(
            headline="1 open price mismatch: INV-4482 bills MYR 44.00 against MYR 42.00 on PO-2026-001.",
            points=[ComposedPoint(text="Calculated difference +MYR 2.00 (+4.76%).", source_ids=["S1", "S2", "S99"])],
        )
        accepted = accept_model_answer(grounded, context, built.registry.ids())
        self.assertIsNotNone(accepted)
        self.assertEqual(accepted.points[0].source_ids, ["S1", "S2"])

    def test_transaction_context_endpoint_payload(self) -> None:
        context = transaction_context(self.t1)
        self.assertEqual(context.transaction.name, "PO-2026-001")
        self.assertEqual(len(context.issues), 2)
        self.assertTrue(all(issue.source_ids for issue in context.issues))


# ---------------------------------------------------------------------------
# End-to-end pipeline (fake session + fake model)
# ---------------------------------------------------------------------------


class PipelineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.ws = build_workspace()
        self.t1, self.t2 = self.ws.transactions[:2]

    def test_what_needs_attention_without_model(self) -> None:
        response, session = run_ask(self.ws, AskRequest(question="What needs my attention?"))
        self.assertEqual(response.intent, AskIntent.list_open_issues)
        self.assertEqual(response.outcome, "answered")
        self.assertEqual(response.answer_mode, "records")
        self.assertTrue(response.answer.headline.startswith("2 transactions currently need review"))
        self.assertEqual(len(response.issues), 3)
        self.assertTrue(session.rolled_back)

    def test_why_flagged_explains_variance_and_evidence(self) -> None:
        response, _ = run_ask(self.ws, AskRequest(question="Why is PO-2026-001 flagged?"))
        self.assertEqual(response.intent, AskIntent.transaction_explanation)
        text = response.answer.headline + " " + " ".join(p.text for p in response.answer.points)
        for fragment in ("MYR 42.00", "MYR 44.00", "+MYR 2.00", "+4.76%", "high", "medium"):
            self.assertIn(fragment, text)
        labels = {s.label for s in response.sources}
        self.assertTrue({"PO-2026-001 · p.1", "INV-4482 · p.1", "DO-0933 · p.1"} <= labels)

    def test_supplier_price_question_is_database_grounded(self) -> None:
        response, _ = run_ask(
            self.ws, AskRequest(question="Which suppliers have unresolved price discrepancies?")
        )
        self.assertEqual(response.result_kind, "suppliers")
        self.assertEqual([s.supplier for s in response.suppliers], ["ABC Supplies Sdn. Bhd."])
        self.assertIn("1 supplier has unresolved price mismatches", response.answer.headline)

    def test_unsupported_question(self) -> None:
        model = FakeModel(planner(AskIntent.unsupported))
        response, _ = run_ask(self.ws, AskRequest(question="What will Tesla stock do tomorrow?"), model)
        self.assertEqual(response.outcome, "unsupported")
        self.assertEqual(response.answer.headline, ask_service.UNSUPPORTED_MESSAGE)
        self.assertEqual(response.issues + response.transactions, [])
        self.assertEqual(model.compose_calls, [])  # no model fallback to general knowledge

    def test_no_result_query(self) -> None:
        response, _ = run_ask(self.ws, AskRequest(question="Any currency mismatches?"))
        self.assertEqual(response.outcome, "no_results")
        self.assertTrue(response.answer.headline.startswith("No open currency mismatches found"))

    def test_transaction_scope_only_sees_that_transaction(self) -> None:
        response, _ = run_ask(
            self.ws,
            AskRequest(question="What needs my attention?", transaction_id=self.t2.id),
        )
        self.assertEqual(response.scope, "transaction")
        self.assertTrue(response.issues)
        self.assertTrue(all(row.transaction_id == self.t2.id for row in response.issues))
        self.assertNotIn("PO-2026-001", response.answer.headline)

    def test_scoped_this_resolves_to_scope(self) -> None:
        response, _ = run_ask(
            self.ws, AskRequest(question="Why is this flagged?", transaction_id=self.t1.id)
        )
        self.assertEqual(response.intent, AskIntent.transaction_explanation)
        self.assertEqual({row.transaction_id for row in response.issues}, {self.t1.id})

    def test_model_receives_only_grounded_context(self) -> None:
        composed = ComposedAnswer(
            headline="1 open price mismatch on PO-2026-001: MYR 44.00 invoiced vs MYR 42.00 ordered.",
            points=[ComposedPoint(text="Difference +MYR 2.00 (+4.76%).", source_ids=["S1", "S2"])],
        )
        model = FakeModel(planner(AskIntent.price_discrepancies), composed)
        response, _ = run_ask(self.ws, AskRequest(question="Price issues?"), model)

        self.assertEqual(response.answer_mode, "model")
        self.assertEqual(len(model.compose_calls), 1)
        context = model.compose_calls[0]
        self.assertIsInstance(context, dict)
        serialised = str(context)
        self.assertNotIn("Chairs September", serialised)
        self.assertNotIn("postgresql", serialised.lower())
        self.assertNotIn("select ", serialised.lower())
        # Planner sees the question and minimal follow-up state only.
        _, conversation = model.plan_calls[0]
        self.assertEqual(
            set(conversation),
            {"scoped_to_transaction", "previous_question", "previous_intent", "previous_entities"},
        )

    def test_hallucinated_model_answer_falls_back_to_records(self) -> None:
        composed = ComposedAnswer(headline="3 suppliers overcharged you by MYR 950.00.", points=[])
        model = FakeModel(planner(AskIntent.price_discrepancies), composed)
        response, _ = run_ask(self.ws, AskRequest(question="Price issues?"), model)
        self.assertEqual(response.answer_mode, "records")
        self.assertNotIn("950", response.answer.headline)
        self.assertTrue(any("not found in the records" in n for n in response.notices))

    def test_model_failure_falls_back_to_records(self) -> None:
        model = FakeModel(planner(AskIntent.list_open_issues), composed=None)
        response, _ = run_ask(self.ws, AskRequest(question="What needs my attention?"), model)
        self.assertEqual(response.outcome, "answered")
        self.assertEqual(response.answer_mode, "records")

    def test_follow_up_uses_previous_entities(self) -> None:
        first, _ = run_ask(self.ws, AskRequest(question="Which suppliers have the most issues?"))
        second, _ = run_ask(
            self.ws, AskRequest(question="Show me Delta Office", context=first.context)
        )
        self.assertEqual(second.intent, AskIntent.supplier_issues)
        self.assertTrue(all(row.transaction_id == self.t2.id for row in second.issues))

    def test_suggestions_reflect_data(self) -> None:
        suggestions = build_suggestions(self.ws, None).suggestions
        self.assertIn("What needs my attention?", suggestions)
        self.assertLessEqual(len(suggestions), 6)
        scoped = build_suggestions(self.ws, self.t1)
        self.assertEqual(scoped.scope, "transaction")


# ---------------------------------------------------------------------------
# Read-only / no-arbitrary-query guarantees
# ---------------------------------------------------------------------------


class SafetyTests(unittest.TestCase):
    ASK_MODULES = [
        "app/services/ask.py",
        "app/services/ask_queries.py",
        "app/services/ask_context.py",
        "app/services/ask_planner.py",
        "app/services/ask_llm.py",
        "app/routers/ask.py",
        "app/prompts/ask_cermat.py",
    ]

    def test_ask_modules_never_write_or_run_raw_sql(self) -> None:
        root = Path(__file__).resolve().parents[1]
        forbidden = re.compile(
            r"session\.(?:add|add_all|commit|delete|merge|flush)\(|"
            r"from sqlalchemy[\w.]* import[^\n]*\b(?:insert|update|delete|text)\b|"
            r"\.execute\(\s*[\"'f]|exec_driver_sql|DATABASE_URL|database_url"
        )
        for relative in self.ASK_MODULES:
            with self.subTest(module=relative):
                source = (root / relative).read_text()
                self.assertIsNone(forbidden.search(source), forbidden.search(source))

    def test_model_interface_has_no_database_access(self) -> None:
        from app.services.ask_llm import OpenAIAskModel

        for name in ("plan", "compose"):
            params = set(inspect.signature(getattr(OpenAIAskModel, name)).parameters)
            self.assertFalse(params & {"session", "engine", "connection", "db"})

    def test_planner_output_cannot_carry_sql(self) -> None:
        fields = set(PlannerOutput.model_fields)
        self.assertFalse({"sql", "query", "where", "order_by", "table"} & fields)


if __name__ == "__main__":
    unittest.main()
