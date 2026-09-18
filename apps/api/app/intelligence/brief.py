"""cermat. brief — a short operational summary of deterministic metrics.

1. ``brief_facts`` selects the computed overview numbers the brief may use.
2. ``records_brief`` writes the brief directly from those facts (always available).
3. A model may rephrase it. Its output is rejected — and the records brief used
   instead — if it contains any number not in the facts or any banned wording
   (fraud, suspicious, risky…). Results are cached per unique fact set.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from collections import OrderedDict
from datetime import datetime
from typing import Any

from fastapi.concurrency import run_in_threadpool

from app.schemas import BriefResponse, OverviewResponse
from app.services.ask_llm import BriefModel
from app.services.grounding import ungrounded
from app.services.ask_queries import FAMILY_LABELS

logger = logging.getLogger("cermat.brief")

BANNED_WORDS = re.compile(
    r"\b(fraud\w*|suspicious\w*|suspect\w*|dishonest\w*|risky|scam\w*|cheat\w*|"
    r"theft|steal\w*|manipulat\w*|criminal\w*|illegal\w*)\b",
    re.IGNORECASE,
)
PERIOD_TEXT = {"7d": "last 7 days", "30d": "last 30 days", "90d": "last 90 days", "all": "all time"}
FAMILIES = ("price", "quantity", "supplier", "currency", "arithmetic")
_CACHE: "OrderedDict[str, list[str]]" = OrderedDict()
_CACHE_SIZE = 32


def _money(currency: str | None, value: float) -> str:
    return f"{currency + ' ' if currency else ''}{value:,.2f}"


def brief_facts(overview: OverviewResponse) -> dict[str, Any]:
    metrics = overview.metrics
    activity = overview.activity
    top_supplier = next((s for s in overview.suppliers if s.open_issue_count), None)
    top = overview.priority[0] if overview.priority else None
    facts: dict[str, Any] = {
        "period": PERIOD_TEXT[overview.period],
        "open_exceptions": metrics.open_issue_count,
        "high_severity_open": metrics.high_severity_issue_count,
        "transactions_needing_review": metrics.transactions_needing_review,
        "overdue_reviews": metrics.overdue_issue_count,
        "active_variance_by_currency": [
            {"currency": t.currency, "absolute_total": t.absolute_total, "exceptions": t.issue_count}
            for t in metrics.total_active_variance_amount
        ],
        "exceptions_created_this_period": activity.issues_created,
        "exceptions_created_previous_period": activity.previous_issues_created,
        "exceptions_resolved_this_period": activity.issues_resolved,
        "created_this_period_by_type": {
            FAMILY_LABELS.get(f, f): activity.created_by_family.get(f, 0) for f in FAMILIES
            if activity.created_by_family.get(f)
            or (activity.previous_created_by_family or {}).get(f)
        },
        "created_previous_period_by_type": {
            FAMILY_LABELS.get(f, f): (activity.previous_created_by_family or {}).get(f, 0)
            for f in FAMILIES
            if activity.created_by_family.get(f)
            or (activity.previous_created_by_family or {}).get(f)
        }
        if activity.previous_created_by_family is not None
        else None,
        "trend_direction": overview.trend.direction,
        "trend_history_sufficient": overview.trend.sufficient_history,
    }
    if top_supplier:
        facts["supplier_with_most_open_exceptions"] = {
            "name": top_supplier.supplier_name,
            "open_exceptions": top_supplier.open_issue_count,
            "open_price_discrepancies": top_supplier.price_discrepancy_count,
        }
    if top:
        facts["top_priority"] = {
            "transaction": top.transaction_name,
            "exception": top.title,
            "band": top.priority_band,
            "score": top.priority_score,
        }
    if overview.patterns:
        facts["recurring_patterns"] = [p.title for p in overview.patterns[:2]]
    if overview.anomalies:
        facts["signals"] = [a.title for a in overview.anomalies[:2]]
    return facts


def records_brief(facts: dict[str, Any]) -> list[str]:
    lines: list[str] = []
    if not facts["open_exceptions"] and not facts["exceptions_created_this_period"]:
        return ["No open exceptions and no new exceptions in the " + facts["period"] + "."]

    opening = (
        f"{facts['open_exceptions']} open exception{'s' if facts['open_exceptions'] != 1 else ''} "
        f"across {facts['transactions_needing_review']} transaction"
        f"{'s' if facts['transactions_needing_review'] != 1 else ''}"
    )
    if facts["high_severity_open"]:
        opening += f", {facts['high_severity_open']} high severity"
    variance = facts["active_variance_by_currency"]
    if variance:
        opening += "; active billed variance " + " and ".join(
            _money(v["currency"], v["absolute_total"]) for v in variance
        )
    lines.append(opening + ".")

    previous = facts["exceptions_created_previous_period"]
    current = facts["exceptions_created_this_period"]
    by_type_now = facts["created_this_period_by_type"]
    by_type_before = facts["created_previous_period_by_type"] or {}
    moved = [
        (label, by_type_before.get(label, 0), count)
        for label, count in by_type_now.items()
        if count != by_type_before.get(label, 0)
    ]
    if previous is not None and facts["trend_history_sufficient"] and moved:
        label, before, now = max(moved, key=lambda item: abs(item[2] - item[1]))
        verb = "increased" if now > before else "decreased"
        lines.append(
            f"{label.capitalize()}es {verb} from {before} to {now} in the {facts['period']}."
            if label.endswith("mismatch")
            else f"{label.capitalize()}s {verb} from {before} to {now} in the {facts['period']}."
        )
    elif previous is not None and facts["trend_history_sufficient"]:
        lines.append(f"{current} exceptions were created in the {facts['period']}, against {previous} in the period before.")
    else:
        lines.append(f"{current} exceptions were created in the {facts['period']}; not enough history to compare periods yet.")

    supplier = facts.get("supplier_with_most_open_exceptions")
    if supplier:
        text = f"{supplier['name']} accounts for {supplier['open_exceptions']} open exception"
        text += "s" if supplier["open_exceptions"] != 1 else ""
        if supplier["open_price_discrepancies"]:
            text += f", including {supplier['open_price_discrepancies']} price discrepanc"
            text += "ies" if supplier["open_price_discrepancies"] != 1 else "y"
        lines.append(text + ".")

    top = facts.get("top_priority")
    if top:
        lines.append(f"Review first: {top['transaction']} — {top['exception'].lower()}.")
    return lines[:4]


def acceptable(lines: list[str], facts: dict[str, Any]) -> bool:
    cleaned = [line.strip() for line in lines if line.strip()]
    if not cleaned or len(cleaned) > 5:
        return False
    if any(BANNED_WORDS.search(line) for line in cleaned):
        return False
    return not ungrounded(cleaned, facts)


async def generate_brief(
    overview: OverviewResponse,
    model: BriefModel | None,
    now: datetime,
) -> BriefResponse:
    facts = brief_facts(overview)
    notices: list[str] = []
    lines = records_brief(facts)
    mode = "records"

    if not overview.has_data:
        lines = ["No transactions yet. The brief appears once documents have been reconciled."]
    elif model is not None and (facts["open_exceptions"] or facts["exceptions_created_this_period"]):
        key = hashlib.sha256(json.dumps(facts, sort_keys=True, default=str).encode()).hexdigest()
        cached = _CACHE.get(key)
        if cached is not None:
            lines, mode = cached, "model"
        else:
            try:
                composed = await run_in_threadpool(model.brief, facts)
            except Exception:  # noqa: BLE001 — never surface model errors
                logger.exception("Brief model call failed.")
                notices.append("Written directly from metrics — the model was unavailable.")
            else:
                candidate = [line.strip() for line in composed.lines if line.strip()]
                if acceptable(candidate, facts):
                    lines, mode = candidate, "model"
                    _CACHE[key] = candidate
                    while len(_CACHE) > _CACHE_SIZE:
                        _CACHE.popitem(last=False)
                else:
                    notices.append(
                        "Written directly from metrics — the model's wording did not pass "
                        "grounding checks."
                    )
    elif model is None:
        notices.append("Written directly from metrics — no model configured.")

    supplier = facts.get("supplier_with_most_open_exceptions")
    suggested = (
        f"Why is {supplier['name']} appearing in the priority queue?"
        if supplier
        else "What changed in the " + facts["period"] + "?"
        if overview.period != "all"
        else "What needs my attention?"
    )
    return BriefResponse(
        period=overview.period,
        generated_at=now,
        mode=mode,
        lines=lines,
        facts=facts,
        notices=notices,
        suggested_question=suggested,
    )
