"""Standardised monetary variance.

Every exception with a financial effect gets one ``FinancialVariance``:

| Issue                                   | Signed variance                                   |
| --------------------------------------- | ------------------------------------------------- |
| invoice price differs from PO           | (invoice unit price − PO unit price) × invoiced qty |
| invoiced qty differs from delivered qty | (invoiced qty − delivered qty) × invoice unit price |
| invoice line arithmetic                 | printed line total − (qty × unit price)           |

Other issues (supplier name, missing items, DO vs PO quantity) have no
billed amount and return ``None`` — never a guessed value. Totals are grouped
per currency and never converted.
"""

from __future__ import annotations

from collections import defaultdict
from decimal import ROUND_HALF_UP, Decimal
from statistics import median

from app.intelligence.dataset import IssueFact, TxnFact
from app.schemas import CurrencyTotal, FinancialVariance
from app.services.ask_queries import parse_amount

_CENT = Decimal("0.01")


def _round(value: Decimal) -> float:
    return float(value.quantize(_CENT, rounding=ROUND_HALF_UP))


def _line_for(txn: TxnFact, issue: IssueFact) -> dict | None:
    if not issue.item_description:
        return None
    return next(
        (line for line in txn.lines if line.get("description") == issue.item_description),
        None,
    )


def issue_percentage(issue: IssueFact) -> float | None:
    """(actual − expected) / expected × 100 for numeric price/quantity/arithmetic issues."""
    if issue.family not in {"price", "quantity", "arithmetic"}:
        return None
    expected = parse_amount(issue.expected)
    actual = parse_amount(issue.actual)
    if expected is None or actual is None or expected[1] == 0:
        return None
    return _round((actual[1] - expected[1]) / expected[1] * Decimal(100))


BILLED_CODES = {
    "invoice_price_variance",
    "unit_price_mismatch",
    "invoice_delivery_quantity_variance",
    "invoice_line_arithmetic_mismatch",
}


def billed_percentage(issue: IssueFact) -> float | None:
    """Variance % only for exceptions that affect the billed amount.

    A delivery that is short of the order is an operational exception, not a
    billing variance, so it never trips percentage thresholds.
    """
    return issue_percentage(issue) if issue.code in BILLED_CODES else None


def financial_variance(txn: TxnFact, issue: IssueFact) -> FinancialVariance | None:
    expected = parse_amount(issue.expected)
    actual = parse_amount(issue.actual)
    if expected is None or actual is None:
        return None
    currency = (actual[0] or expected[0] or txn.currency or None)
    currency = currency.upper() if currency else None
    delta = actual[1] - expected[1]
    percentage = issue_percentage(issue)

    signed: Decimal | None = None
    basis: str | None = None
    if issue.code in {"invoice_price_variance", "unit_price_mismatch"}:
        line = _line_for(txn, issue)
        quantity = line.get("invoice_quantity") if line else None
        if quantity is not None:
            signed = delta * Decimal(str(quantity))
            basis = "billed_price_difference"
    elif issue.code == "invoice_delivery_quantity_variance":
        line = _line_for(txn, issue)
        unit_price = line.get("invoice_unit_price") if line else None
        if unit_price is not None:
            signed = delta * Decimal(str(unit_price))
            basis = "billed_quantity_difference"
            currency = currency or txn.currency
    elif issue.code == "invoice_line_arithmetic_mismatch":
        signed = delta
        basis = "line_arithmetic"

    if signed is None or basis is None:
        return None
    return FinancialVariance(
        basis=basis,
        currency=currency,
        signed_variance=_round(signed),
        absolute_variance=_round(abs(signed)),
        variance_percentage=percentage,
    )


def currency_totals(variances: list[FinancialVariance]) -> list[CurrencyTotal]:
    """Group by currency. Different currencies are never summed together."""
    grouped: dict[str | None, list[FinancialVariance]] = defaultdict(list)
    for variance in variances:
        grouped[variance.currency].append(variance)
    totals = [
        CurrencyTotal(
            currency=currency,
            signed_total=_round(sum((Decimal(str(v.signed_variance)) for v in items), Decimal(0))),
            absolute_total=_round(sum((Decimal(str(v.absolute_variance)) for v in items), Decimal(0))),
            issue_count=len(items),
        )
        for currency, items in grouped.items()
    ]
    return sorted(totals, key=lambda total: (-total.absolute_total, total.currency or ""))


def average_abs_percentage(values: list[float | None]) -> float | None:
    known = [abs(value) for value in values if value is not None]
    if not known:
        return None
    return round(sum(known) / len(known), 2)


def median_or_none(values: list[float]) -> float | None:
    return round(median(values), 2) if values else None
