from __future__ import annotations

import asyncio
import unittest
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from app.intelligence.anomalies import detect_anomalies
from app.intelligence.attention import (
    Candidate,
    ExistingEvent,
    attention_candidates,
    plan_attention_changes,
)
from app.intelligence.brief import acceptable, brief_facts, generate_brief, records_brief
from app.intelligence.config import merge_settings
from app.intelligence.dataset import (
    DocFact,
    IntelDataset,
    IssueFact,
    TxnFact,
    supplier_key,
    window_for,
)
from app.intelligence.overview import compute_overview, resolution_performance
from app.intelligence.patterns import detect_patterns
from app.intelligence.priority import priority_queue, score_issue
from app.intelligence.suppliers import supplier_detail, supplier_intelligence
from app.intelligence.trends import exception_trend
from app.intelligence.variance import currency_totals, financial_variance
from app.schemas import AskIntent, AskRequest, IntelligenceSettings, IntelligenceSettingsUpdate

NOW = datetime(2026, 9, 18, 12, 0, tzinfo=timezone.utc)
DEFAULTS = IntelligenceSettings()


# ---------------------------------------------------------------------------
# Builders
# ---------------------------------------------------------------------------


def txn(
    name: str,
    supplier: str = "ABC Supplies Sdn. Bhd.",
    *,
    days_ago: float = 1,
    currency: str = "MYR",
    invoice_total: float | None = 100.0,
    lines: list[dict] | None = None,
    reconciled: bool = True,
    documents: tuple[str, ...] = ("purchase_order", "delivery_order", "invoice"),
) -> TxnFact:
    created = NOW - timedelta(days=days_ago)
    fact = TxnFact(
        id=uuid4(), name=name, status="review_required",
        created_at=created, updated_at=created, reconciled=reconciled, lines=lines or [],
    )
    for document_type in documents:
        fact.documents.append(DocFact(
            id=uuid4(), transaction_id=fact.id, document_type=document_type,
            filename=f"{name}-{document_type}.pdf", status="extracted",
            supplier_name=supplier, document_number=f"{name}-{document_type[:2]}",
            document_date="2026-09-01", currency=currency,
            total=invoice_total if document_type == "invoice" else None,
            overall_confidence=0.95, evidence_count=5, extracted=True,
        ))
    return fact


def issue(
    owner: TxnFact,
    code: str = "invoice_price_variance",
    *,
    severity: str = "medium",
    status: str = "open",
    expected: str | None = "MYR 42.00",
    actual: str | None = "MYR 44.00",
    item: str | None = "Office Chair",
    days_ago: float | None = None,
    resolved_after_hours: float | None = None,
) -> IssueFact:
    created = NOW - timedelta(days=days_ago) if days_ago is not None else owner.created_at
    fact = IssueFact(
        id=uuid4(), transaction_id=owner.id, code=code, title=code.replace("_", " "),
        severity=severity, status=status, item_description=item,
        expected=expected, actual=actual, created_at=created,
        resolved_at=created + timedelta(hours=resolved_after_hours)
        if status == "resolved" and resolved_after_hours is not None
        else None,
    )
    owner.issues.append(fact)
    return fact


def chair_line(invoice_quantity: float = 10, unit_price: float = 44.0) -> dict:
    return {
        "description": "Office Chair", "sku": "CHAIR-1",
        "invoice_quantity": invoice_quantity, "invoice_unit_price": unit_price, "status": "review_required",
    }


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class VarianceTests(unittest.TestCase):
    def test_price_variance_is_billed_on_invoiced_quantity(self) -> None:
        t = txn("T1", lines=[chair_line(10)])
        v = financial_variance(t, issue(t))
        self.assertEqual((v.signed_variance, v.absolute_variance, v.variance_percentage), (20.0, 20.0, 4.76))
        self.assertEqual((v.currency, v.basis), ("MYR", "billed_price_difference"))

    def test_quantity_variance_uses_invoice_unit_price(self) -> None:
        t = txn("T1", lines=[chair_line(12, 385.0)])
        v = financial_variance(t, issue(t, "invoice_delivery_quantity_variance", expected="10", actual="12"))
        self.assertEqual((v.signed_variance, v.basis), (770.0, "billed_quantity_difference"))

    def test_no_line_quantity_means_no_amount(self) -> None:
        t = txn("T1", lines=[])
        self.assertIsNone(financial_variance(t, issue(t)))

    def test_short_delivery_has_no_billed_amount(self) -> None:
        t = txn("T1", lines=[chair_line()])
        self.assertIsNone(financial_variance(t, issue(t, "delivery_quantity_variance", expected="10", actual="8")))

    def test_different_currencies_are_never_summed(self) -> None:
        myr = txn("M", lines=[chair_line(10)])
        usd = txn("U", currency="USD", lines=[{**chair_line(5), "invoice_unit_price": 12.8}])
        variances = [
            financial_variance(myr, issue(myr)),
            financial_variance(myr, issue(myr, expected="MYR 10.00", actual="MYR 11.00")),
            financial_variance(usd, issue(usd, expected="USD 12.00", actual="USD 12.80")),
        ]
        totals = {t.currency: t for t in currency_totals(variances)}
        self.assertEqual(set(totals), {"MYR", "USD"})
        self.assertEqual((totals["MYR"].absolute_total, totals["MYR"].issue_count), (30.0, 2))
        self.assertEqual(totals["USD"].absolute_total, 4.0)


class OverviewTests(unittest.TestCase):
    def test_overview_counts_exclude_resolved_from_current(self) -> None:
        a, b = txn("A", lines=[chair_line()]), txn("B", supplier="Delta Office")
        issue(a, severity="high", code="invoice_delivery_quantity_variance", expected="8", actual="10")
        issue(a)
        issue(b, "supplier_mismatch", expected="Delta Office", actual="Delta Offices Trading", item=None)
        issue(b, "delivery_quantity_variance", status="resolved", resolved_after_hours=5)
        clean = txn("C")
        overview = compute_overview(IntelDataset([a, b, clean]), DEFAULTS, "30d", NOW)
        m = overview.metrics
        self.assertEqual((m.total_transactions, m.transactions_needing_review), (3, 2))
        self.assertEqual((m.open_issue_count, m.resolved_issue_count), (3, 1))
        self.assertEqual((m.high_severity_issue_count, m.medium_severity_issue_count), (1, 2))
        self.assertEqual((m.price_discrepancy_count, m.quantity_discrepancy_count, m.supplier_mismatch_count), (1, 1, 1))
        self.assertEqual(sum(row.count for row in overview.issue_mix), m.open_issue_count)

    def test_missing_documents_counted(self) -> None:
        ds = IntelDataset([txn("A"), txn("B", documents=("purchase_order",), reconciled=False)])
        self.assertEqual(compute_overview(ds, DEFAULTS, "30d", NOW).metrics.missing_document_count, 1)

    def test_empty_workspace(self) -> None:
        overview = compute_overview(IntelDataset([]), DEFAULTS, "30d", NOW)
        self.assertFalse(overview.has_data)
        self.assertEqual(overview.metrics.open_issue_count, 0)
        self.assertIsNone(overview.metrics.resolution_rate)
        self.assertIsNone(overview.metrics.average_variance_percentage)
        self.assertEqual(overview.metrics.total_active_variance_amount, [])
        self.assertFalse(overview.trend.sufficient_history)
        self.assertEqual((overview.priority, overview.patterns, overview.anomalies), ([], [], []))
        brief = asyncio.run(generate_brief(overview, None, NOW))
        self.assertIn("No transactions yet", brief.lines[0])


class PeriodTests(unittest.TestCase):
    def test_window_boundaries(self) -> None:
        window = window_for("7d", NOW)
        start = NOW - timedelta(days=7)
        self.assertTrue(window.contains(start))
        self.assertFalse(window.contains(start - timedelta(microseconds=1)))
        self.assertTrue(window.contains(NOW))
        self.assertFalse(window.contains(NOW + timedelta(seconds=1)))
        self.assertTrue(window.in_previous(start - timedelta(days=7)))
        self.assertFalse(window.in_previous(start))
        self.assertIsNone(window_for("all", NOW).start)
        with self.assertRaises(ValueError):
            window_for("365d", NOW)

    def test_period_filters_activity_not_current_state(self) -> None:
        old, new = txn("Old", days_ago=40), txn("New", days_ago=3)
        issue(old)
        issue(new)
        o7 = compute_overview(IntelDataset([old, new]), DEFAULTS, "7d", NOW)
        o90 = compute_overview(IntelDataset([old, new]), DEFAULTS, "90d", NOW)
        self.assertEqual((o7.activity.issues_created, o90.activity.issues_created), (1, 2))
        self.assertEqual(o7.metrics.open_issue_count, o90.metrics.open_issue_count)

    def test_resolution_time_median_and_average(self) -> None:
        t = txn("T", days_ago=5)
        for hours in (2, 4, 30):
            issue(t, status="resolved", resolved_after_hours=hours)
        issue(t)
        perf = resolution_performance(IntelDataset([t]), DEFAULTS, window_for("30d", NOW), NOW)
        self.assertEqual((perf.issues_created, perf.issues_resolved), (4, 3))
        self.assertEqual((perf.median_resolution_hours, perf.average_resolution_hours), (4.0, 12.0))
        self.assertEqual(perf.resolution_rate, 75.0)
        self.assertEqual(perf.oldest_open_issue_age_days, 5.0)


class PriorityTests(unittest.TestCase):
    def test_score_components_are_transparent(self) -> None:
        t = txn("T", days_ago=10, lines=[chair_line(12, 385.0)])
        i = issue(t, "invoice_delivery_quantity_variance", severity="high", expected="10", actual="12")
        item = score_issue(t, i, settings=DEFAULTS, now=NOW, recurring={})
        points = {c.factor: c.points for c in item.components}
        # 40 severity + 20 value (770 ≥ 500) + 15 pct (20%) + 15 age cap + 10 overdue + 4 type
        self.assertEqual(points, {
            "severity": 40, "variance_amount": 20, "variance_percentage": 15,
            "age": 15, "overdue": 10, "issue_type": 4,
        })
        self.assertEqual(item.priority_score, 104)
        self.assertEqual(item.priority_band, "critical")
        self.assertEqual(len(item.priority_reasons), len(item.components))

    def test_critical_requires_a_clear_condition(self) -> None:
        # High score but medium severity → never critical.
        t = txn("T", days_ago=30, lines=[chair_line(100)])
        i = issue(t, expected="MYR 42.00", actual="MYR 52.00")
        issue(t, "supplier_mismatch", expected="A", actual="B", item=None)  # +4 related
        item = score_issue(t, i, settings=DEFAULTS, now=NOW, recurring={})
        self.assertGreaterEqual(item.priority_score, DEFAULTS.critical_score)
        self.assertEqual(item.priority_band, "high")

    def test_queue_is_sorted_and_excludes_resolved(self) -> None:
        t = txn("T")
        low = issue(t, severity="low")
        high = issue(t, severity="high", code="currency_mismatch", expected="MYR", actual="USD", item=None)
        issue(t, severity="high", status="resolved", resolved_after_hours=1)
        queue = priority_queue(IntelDataset([t]), DEFAULTS, NOW, [])
        self.assertEqual([q.issue_id for q in queue], [high.id, low.id])


class SupplierTests(unittest.TestCase):
    def test_aggregation_and_issue_rate(self) -> None:
        a1 = txn("A1", "ABC Supplies Sdn. Bhd.", lines=[chair_line()])
        a2 = txn("A2", "ABC Supplies Sdn Bhd")  # same supplier after normalisation
        a3 = txn("A3", "ABC SUPPLIES")
        a4 = txn("A4", "ABC Supplies", documents=("purchase_order",), reconciled=False)
        d1 = txn("D1", "Delta Office")
        issue(a1)
        issue(a1, status="resolved", resolved_after_hours=10)
        issue(a2, "invoice_delivery_quantity_variance", expected="8", actual="10")
        rows = {r.supplier_key: r for r in supplier_intelligence(IntelDataset([a1, a2, a3, a4, d1]))}
        abc = rows["abc-supplies"]
        self.assertEqual((abc.transaction_count, abc.reconciled_transaction_count), (4, 3))
        self.assertEqual((abc.open_issue_count, abc.resolved_issue_count), (2, 1))
        self.assertEqual(abc.issue_rate, 66.7)  # 2 of 3 reconciled transactions have exceptions
        self.assertEqual((abc.price_discrepancy_count, abc.quantity_discrepancy_count), (1, 1))
        self.assertEqual(abc.average_resolution_time_hours, 10.0)
        self.assertEqual(rows["delta-office"].issue_rate, 0.0)

    def test_different_suppliers_are_not_merged(self) -> None:
        self.assertEqual(supplier_key("ABC Supplies Sdn. Bhd."), supplier_key("abc supplies sdn bhd"))
        self.assertNotEqual(supplier_key("Delta Office"), supplier_key("Delta Offices Trading"))

    def test_detail_pattern_summary_is_counted(self) -> None:
        txns = []
        for n in range(5):
            t = txn(f"A{n}", days_ago=10 - n, lines=[chair_line()])
            issue(t, expected="MYR 42.00", actual="MYR 44.00" if n != 4 else "MYR 40.00")
            txns.append(t)
        detail = supplier_detail(IntelDataset(txns), DEFAULTS, NOW, "abc-supplies")
        self.assertEqual(detail.pattern_summary.price_above_po_count, 4)
        self.assertTrue(detail.pattern_summary.sentence.startswith("4 of the last 5 exceptions"))


class PatternTests(unittest.TestCase):
    def _supplier_with_price_issues(self, count: int, transactions: int) -> IntelDataset:
        txns = [txn(f"T{n}", days_ago=5 + n, lines=[chair_line()]) for n in range(transactions)]
        for n in range(count):
            issue(txns[n % transactions])
        return IntelDataset(txns)

    def test_below_threshold_is_not_a_pattern(self) -> None:
        patterns = detect_patterns(self._supplier_with_price_issues(2, 2), DEFAULTS, NOW)
        self.assertFalse([p for p in patterns if p.pattern_type == "repeated_price_discrepancy"])

    def test_threshold_reached_across_transactions(self) -> None:
        patterns = detect_patterns(self._supplier_with_price_issues(3, 3), DEFAULTS, NOW)
        price = next(p for p in patterns if p.pattern_type == "repeated_price_discrepancy")
        self.assertEqual((price.count, price.threshold, price.period_days), (3, 3, 90))
        self.assertEqual(len(price.transaction_ids), 3)
        self.assertIn("generated 3 price discrepancies in the last 90 days", price.title)

    def test_one_transaction_is_not_a_pattern(self) -> None:
        patterns = detect_patterns(self._supplier_with_price_issues(4, 1), DEFAULTS, NOW)
        self.assertEqual(patterns, [])

    def test_outside_period_is_ignored(self) -> None:
        txns = [txn(f"T{n}", days_ago=100 + n) for n in range(3)]
        for t in txns:
            issue(t)
        self.assertEqual(detect_patterns(IntelDataset(txns), DEFAULTS, NOW), [])

    def test_threshold_is_configurable(self) -> None:
        settings = DEFAULTS.model_copy(update={"recurring_issue_min_count": 2})
        patterns = detect_patterns(self._supplier_with_price_issues(2, 2), settings, NOW)
        self.assertTrue([p for p in patterns if p.pattern_type == "repeated_price_discrepancy"])


class AnomalyTests(unittest.TestCase):
    def _dataset(self, actual_price: str) -> IntelDataset:
        history = []
        for n, price in enumerate(("MYR 43.00", "MYR 43.50", "MYR 44.00", "MYR 42.84", "MYR 43.26")):
            t = txn(f"H{n}", days_ago=40 + n)
            issue(t, actual=price, status="resolved", resolved_after_hours=24)
            history.append(t)
        current = txn("NOW", days_ago=2)
        issue(current, actual=actual_price)
        return IntelDataset([*history, current])

    def test_percentage_threshold_and_median_baseline(self) -> None:
        signals = [s for s in detect_anomalies(self._dataset("MYR 49.644"), DEFAULTS, NOW)
                   if s.signal == "high_variance_percentage"]
        self.assertEqual(len(signals), 1)
        signal = signals[0]
        self.assertEqual((signal.observed_value, signal.threshold), (18.2, 10.0))
        self.assertEqual(signal.baseline, 3.0)  # median of 2.38, 3.57, 4.76, 2.00, 3.00
        self.assertIn("Configured review threshold: 10%", signal.reason)

    def test_just_below_threshold_is_quiet(self) -> None:
        signals = detect_anomalies(self._dataset("MYR 46.19"), DEFAULTS, NOW)  # +9.98%
        self.assertFalse([s for s in signals if s.signal == "high_variance_percentage"])

    def test_invoice_total_against_supplier_median(self) -> None:
        txns = [txn(f"N{n}", "Northwind", days_ago=60 - n * 10, invoice_total=450.0) for n in range(4)]
        txns.append(txn("BIG", "Northwind", days_ago=1, invoice_total=2990.0))
        signals = [s for s in detect_anomalies(IntelDataset(txns), DEFAULTS, NOW)
                   if s.signal == "amount_above_supplier_median"]
        self.assertEqual(len(signals), 1)
        self.assertEqual((signals[0].baseline, signals[0].threshold), (450.0, 1350.0))

    def test_spike_needs_history(self) -> None:
        recent = [txn(f"R{n}", days_ago=3 + n) for n in range(4)]
        for t in recent:
            issue(t)
        signals = detect_anomalies(IntelDataset(recent), DEFAULTS, NOW)
        self.assertFalse([s for s in signals if s.signal == "issue_frequency_spike"])


class AttentionTests(unittest.TestCase):
    def _candidates(self) -> list[Candidate]:
        t = txn("T", days_ago=9, lines=[chair_line(12, 385.0)])
        issue(t, "invoice_delivery_quantity_variance", severity="high", expected="10", actual="12")
        ds = IntelDataset([t])
        return attention_candidates(ds, DEFAULTS, NOW, detect_patterns(ds, DEFAULTS, NOW),
                                    detect_anomalies(ds, DEFAULTS, NOW))

    def _apply(self, store: dict[str, ExistingEvent], candidates: list[Candidate]) -> int:
        inserts, clears, restores = plan_attention_changes(store, candidates)
        for c in inserts:
            store[c.event_key] = ExistingEvent(c.event_key, cleared=False, dismissed=False)
        for key in clears:
            store[key] = ExistingEvent(key, cleared=True, dismissed=store[key].dismissed)
        for key in restores:
            store[key] = ExistingEvent(key, cleared=False, dismissed=False)
        return len(inserts)

    def test_events_are_created_once(self) -> None:
        candidates = self._candidates()
        types = {c.event_type for c in candidates}
        self.assertTrue({"high_severity_issue", "large_variance", "overdue_review"} <= types)
        # The same issue's anomaly signals are already covered by issue events.
        self.assertFalse([c for c in candidates if c.event_type == "anomaly"
                          and "high_variance_percentage" in c.event_key])
        store: dict[str, ExistingEvent] = {}
        self.assertEqual(self._apply(store, candidates), len(candidates))
        for _ in range(3):  # page refreshes
            self.assertEqual(self._apply(store, self._candidates_same(candidates)), 0)

    def _candidates_same(self, candidates: list[Candidate]) -> list[Candidate]:
        return list(candidates)

    def test_cleared_and_restored_without_duplicates(self) -> None:
        candidates = self._candidates()
        store: dict[str, ExistingEvent] = {}
        self._apply(store, candidates)
        self._apply(store, [])  # condition resolved
        self.assertTrue(all(e.cleared for e in store.values()))
        self.assertEqual(self._apply(store, candidates), 0)  # reopened → restored, not re-inserted
        self.assertFalse(any(e.cleared for e in store.values()))

    def test_dismissed_events_stay_dismissed(self) -> None:
        key = "high_severity_issue:x"
        store = {key: ExistingEvent(key, cleared=True, dismissed=True)}
        candidate = Candidate(key, "high_severity_issue", "t", "m", "high", "transaction", None, None, None)
        inserts, clears, restores = plan_attention_changes(store, [candidate])
        self.assertEqual((inserts, clears, restores), ([], [], []))

    def test_recurring_pattern_notifies_once_as_count_grows(self) -> None:
        txns = [txn(f"T{n}", days_ago=5 + n) for n in range(3)]
        for t in txns:
            issue(t)
        ds = IntelDataset(txns)
        first = attention_candidates(ds, DEFAULTS, NOW, detect_patterns(ds, DEFAULTS, NOW), [])
        store: dict[str, ExistingEvent] = {}
        self._apply(store, first)
        extra = txn("T9", days_ago=1)
        issue(extra)
        ds2 = IntelDataset([*txns, extra])
        second = attention_candidates(ds2, DEFAULTS, NOW, detect_patterns(ds2, DEFAULTS, NOW), [])
        new = [c for c in plan_attention_changes(store, second)[0] if c.event_type == "recurring_pattern"]
        self.assertEqual(new, [])


class TrendTests(unittest.TestCase):
    def test_series_totals_match_and_direction(self) -> None:
        txns = []
        for days in (40, 45, 2, 3, 5, 6, 8):
            t = txn(f"T{days}", days_ago=days)
            issue(t)
            txns.append(t)
        trend = exception_trend(IntelDataset(txns), "30d", NOW)
        self.assertEqual(trend.granularity, "day")
        self.assertEqual(sum(p.created for p in trend.series), trend.total_created)
        self.assertEqual((trend.total_created, trend.previous_period_created), (5, 2))
        self.assertEqual(trend.direction, "increasing")
        self.assertTrue(trend.sufficient_history)

    def test_single_date_is_not_a_trend(self) -> None:
        t = txn("T", days_ago=2)
        for _ in range(3):
            issue(t)
        trend = exception_trend(IntelDataset([t]), "30d", NOW)
        self.assertFalse(trend.sufficient_history)
        self.assertIsNone(trend.direction)
        self.assertIn("Not enough", trend.message)

    def test_category_filter(self) -> None:
        t = txn("T", days_ago=2)
        issue(t)
        issue(t, "supplier_mismatch", expected="A", actual="B", item=None)
        self.assertEqual(exception_trend(IntelDataset([t]), "30d", NOW, "supplier").total_created, 1)


class BriefAndSettingsTests(unittest.TestCase):
    def test_brief_grounding_rejects_invented_numbers_and_accusations(self) -> None:
        t = txn("T", days_ago=2, lines=[chair_line()])
        issue(t)
        facts = brief_facts(compute_overview(IntelDataset([t]), DEFAULTS, "30d", NOW))
        self.assertTrue(acceptable(records_brief(facts), facts))
        self.assertFalse(acceptable(["1 open exception worth MYR 999.00."], facts))
        self.assertFalse(acceptable(["ABC Supplies shows suspicious pricing."], facts))
        self.assertFalse(acceptable(["Possible fraud on 1 invoice."], facts))

    def test_settings_merge_and_validation(self) -> None:
        merged = merge_settings(DEFAULTS, IntelligenceSettingsUpdate(
            high_variance_percentage=15, priority_weights={"overdue": 25, "issue_type": {"price": 9}},
        ))
        self.assertEqual(merged.high_variance_percentage, 15)
        self.assertEqual(merged.priority_weights.overdue, 25)
        self.assertEqual(merged.priority_weights.issue_type["price"], 9)
        self.assertEqual(merged.priority_weights.issue_type["currency"], 8)
        with self.assertRaises(ValueError):
            merge_settings(DEFAULTS, IntelligenceSettingsUpdate(priority_weights={"magic": 1}))
        with self.assertRaises(ValueError):
            merge_settings(DEFAULTS, IntelligenceSettingsUpdate(high_score=90))
        self.assertEqual(merge_settings(merged, IntelligenceSettingsUpdate(reset=True)), DEFAULTS)


class AskIntelligenceTests(unittest.TestCase):
    """Phase 6 intents through the Ask pipeline (fake session, no model)."""

    def setUp(self) -> None:
        from test_ask import build_workspace, run_ask

        self.ws = build_workspace()
        self.run_ask = run_ask

    def ask(self, question: str):
        response, _ = self.run_ask(self.ws, AskRequest(question=question))
        return response

    def test_what_changed_is_grounded_in_intelligence_metrics(self) -> None:
        response = self.ask("What changed in the last 30 days?")
        self.assertEqual(response.intent, AskIntent.overview_summary)
        self.assertEqual(response.filters.period, "30d")
        self.assertEqual(response.result_kind, "insights")
        self.assertTrue(response.answer.headline.startswith("3 open exceptions across 2 transactions"))

    def test_intelligence_intents_route(self) -> None:
        cases = {
            "Which recurring patterns should I review?": AskIntent.recurring_patterns,
            "Are price discrepancies increasing?": AskIntent.exception_trend,
            "Show the priority queue": AskIntent.priority_queue,
            "Any unusual signals?": AskIntent.anomaly_signals,
            "What is our total variance?": AskIntent.variance_summary,
            "How fast are we resolving issues?": AskIntent.resolution_performance,
            "Why is ABC Supplies appearing in the priority queue?": AskIntent.supplier_summary,
        }
        for question, intent in cases.items():
            with self.subTest(question=question):
                response = self.ask(question)
                self.assertEqual(response.intent, intent)
                self.assertEqual(response.outcome, "answered")

    def test_priority_answer_cites_evidence(self) -> None:
        response = self.ask("Show the priority queue")
        self.assertTrue(response.issues)
        self.assertTrue(any(point.source_ids for point in response.answer.points))

    def test_supplier_summary_explains_priority(self) -> None:
        response = self.ask("Why is ABC Supplies appearing in the priority queue?")
        text = " ".join([response.answer.headline, *(p.text for p in response.answer.points)])
        self.assertIn("priority queue", text)
        self.assertIn("high severity", text)


if __name__ == "__main__":
    unittest.main()
