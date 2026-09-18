from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from difflib import SequenceMatcher
from uuid import UUID

from app.schemas import (
    AIExtraction,
    DocumentType,
    EvidenceReference,
    ExtractedLineItem,
    MatchedLine,
    ReconciliationIssue,
    ReconciliationResult,
    ReconciliationSummary,
    TransactionDocumentSummary,
)

DESCRIPTION_MATCH_THRESHOLD = 0.72
MONEY_TOLERANCE = Decimal("0.01")
QUANTITY_TOLERANCE = Decimal("0.000001")


@dataclass(frozen=True)
class ReconciliationDocument:
    id: UUID
    filename: str
    document_type: DocumentType
    extraction: AIExtraction


def _decimal(value: float | None) -> Decimal | None:
    if value is None:
        return None
    return Decimal(str(value))


def _normalise_text(value: str | None) -> str:
    if not value:
        return ""
    return " ".join(re.findall(r"[a-z0-9]+", value.lower()))


def _normalise_supplier(value: str | None) -> str:
    tokens = _normalise_text(value).split()
    ignored = {
        "sdn",
        "bhd",
        "berhad",
        "ltd",
        "limited",
        "inc",
        "corp",
        "corporation",
        "pte",
        "plc",
        "llp",
        "enterprise",
    }
    return " ".join(token for token in tokens if token not in ignored)


def _similarity(left: str | None, right: str | None) -> float:
    a = _normalise_text(left)
    b = _normalise_text(right)
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a, b).ratio()


def _supplier_similarity(left: str | None, right: str | None) -> float:
    a = _normalise_supplier(left)
    b = _normalise_supplier(right)
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a, b).ratio()


def _qty(value: float | None) -> str:
    if value is None:
        return "—"
    return f"{value:g}"


def _money(currency: str | None, value: float | None) -> str:
    if value is None:
        return "—"
    prefix = f"{currency} " if currency else ""
    return f"{prefix}{value:,.2f}"


def _delta_money(currency: str | None, actual: float, expected: float) -> str:
    delta = actual - expected
    prefix = f"{currency} " if currency else ""
    sign = "+" if delta > 0 else ""
    if expected != 0:
        pct = (delta / expected) * 100
        return f"{sign}{prefix}{delta:,.2f} ({pct:+.2f}%)"
    return f"{sign}{prefix}{delta:,.2f}"


def _evidence_reference(
    document: ReconciliationDocument,
    field_path: str,
    value: str | None,
) -> EvidenceReference:
    exact = next(
        (
            evidence
            for evidence in document.extraction.evidence
            if evidence.field_path == field_path
        ),
        None,
    )

    if exact is None and field_path.startswith("line_items."):
        prefix = ".".join(field_path.split(".")[:2]) + "."
        exact = next(
            (
                evidence
                for evidence in document.extraction.evidence
                if evidence.field_path.startswith(prefix)
            ),
            None,
        )

    return EvidenceReference(
        document_id=document.id,
        filename=document.filename,
        document_type=document.document_type,
        field_path=field_path,
        source_text=(
            exact.source_text
            if exact is not None
            else "No source snippet was captured for this structured value."
        ),
        page=exact.page if exact is not None else None,
        value=value,
    )


def _find_line_match(
    source: ExtractedLineItem,
    candidates: list[ExtractedLineItem],
    used_indexes: set[int],
) -> int | None:
    source_sku = _normalise_text(source.sku)
    if source_sku:
        for index, candidate in enumerate(candidates):
            if index in used_indexes:
                continue
            if _normalise_text(candidate.sku) == source_sku:
                return index

    best_index: int | None = None
    best_score = 0.0
    for index, candidate in enumerate(candidates):
        if index in used_indexes:
            continue
        score = _similarity(source.description, candidate.description)
        if score > best_score:
            best_score = score
            best_index = index

    if best_score >= DESCRIPTION_MATCH_THRESHOLD:
        return best_index
    return None


def _add_issue(
    issues: list[ReconciliationIssue],
    *,
    code: str,
    title: str,
    severity: str,
    explanation: str,
    sources: list[EvidenceReference],
    item_description: str | None = None,
    expected: str | None = None,
    actual: str | None = None,
    delta: str | None = None,
) -> None:
    issues.append(
        ReconciliationIssue(
            code=code,
            title=title,
            severity=severity,
            item_description=item_description,
            expected=expected,
            actual=actual,
            delta=delta,
            explanation=explanation,
            sources=sources,
        )
    )


def reconcile_three_way(
    *,
    transaction_id: UUID,
    po: ReconciliationDocument,
    delivery: ReconciliationDocument,
    invoice: ReconciliationDocument,
    documents: list[TransactionDocumentSummary],
) -> ReconciliationResult:
    issues: list[ReconciliationIssue] = []
    lines: list[MatchedLine] = []

    # Header-level checks.
    currencies = {
        document.extraction.currency.upper()
        for document in (po, delivery, invoice)
        if document.extraction.currency
    }
    if len(currencies) > 1:
        _add_issue(
            issues,
            code="currency_mismatch",
            title="Currency mismatch",
            severity="high",
            expected=po.extraction.currency or "—",
            actual=invoice.extraction.currency or delivery.extraction.currency or "—",
            explanation="The transaction documents do not use the same extracted currency.",
            sources=[
                _evidence_reference(po, "currency", po.extraction.currency),
                _evidence_reference(invoice, "currency", invoice.extraction.currency),
            ],
        )

    for other in (delivery, invoice):
        if (
            po.extraction.supplier_name
            and other.extraction.supplier_name
            and _supplier_similarity(
                po.extraction.supplier_name, other.extraction.supplier_name
            )
            < 0.82
        ):
            _add_issue(
                issues,
                code="supplier_mismatch",
                title="Supplier name differs",
                severity="medium",
                expected=po.extraction.supplier_name,
                actual=other.extraction.supplier_name,
                explanation=(
                    "The supplier name on this document differs materially from the "
                    "purchase order after normalising common company suffixes."
                ),
                sources=[
                    _evidence_reference(
                        po, "supplier_name", po.extraction.supplier_name
                    ),
                    _evidence_reference(
                        other, "supplier_name", other.extraction.supplier_name
                    ),
                ],
            )

    po_items = po.extraction.line_items
    delivery_items = delivery.extraction.line_items
    invoice_items = invoice.extraction.line_items

    if not po_items or not invoice_items:
        if not po_items:
            _add_issue(
                issues,
                code="missing_po_line_items",
                title="Purchase order line items unavailable",
                severity="high",
                explanation="Three-way matching requires readable purchase-order line items.",
                sources=[],
            )
        if not invoice_items:
            _add_issue(
                issues,
                code="missing_invoice_line_items",
                title="Invoice line items unavailable",
                severity="high",
                explanation="Three-way matching requires readable invoice line items.",
                sources=[],
            )

        summary = ReconciliationSummary(
            issue_count=len(issues),
            high=sum(issue.severity == "high" for issue in issues),
            medium=sum(issue.severity == "medium" for issue in issues),
            low=sum(issue.severity == "low" for issue in issues),
            matched_lines=0,
            review_lines=0,
        )
        return ReconciliationResult(
            transaction_id=transaction_id,
            status="insufficient_data",
            documents=documents,
            summary=summary,
            lines=[],
            issues=issues,
            generated_at=datetime.now(timezone.utc),
        )

    used_delivery: set[int] = set()
    used_invoice: set[int] = set()

    for po_index, po_item in enumerate(po_items):
        delivery_index = _find_line_match(po_item, delivery_items, used_delivery)
        invoice_index = _find_line_match(po_item, invoice_items, used_invoice)

        delivery_item = (
            delivery_items[delivery_index] if delivery_index is not None else None
        )
        invoice_item = invoice_items[invoice_index] if invoice_index is not None else None

        if delivery_index is not None:
            used_delivery.add(delivery_index)
        if invoice_index is not None:
            used_invoice.add(invoice_index)

        line_has_issue = False
        description = po_item.description
        key = po_item.sku or _normalise_text(description) or f"po-{po_index + 1}"

        if delivery_item is None:
            line_has_issue = True
            _add_issue(
                issues,
                code="item_missing_from_delivery",
                title="Ordered item not found on delivery order",
                severity="high",
                item_description=description,
                expected=f"Ordered quantity {_qty(po_item.quantity)}",
                actual="No matching delivery line",
                explanation=(
                    "The engine could not match this purchase-order item to a delivery-order line."
                ),
                sources=[
                    _evidence_reference(
                        po,
                        f"line_items.{po_index}.description",
                        po_item.description,
                    )
                ],
            )
        else:
            po_q = _decimal(po_item.quantity)
            do_q = _decimal(delivery_item.quantity)
            if po_q is not None and do_q is not None and abs(po_q - do_q) > QUANTITY_TOLERANCE:
                line_has_issue = True
                _add_issue(
                    issues,
                    code="delivery_quantity_variance",
                    title="Delivered quantity differs from ordered quantity",
                    severity="medium",
                    item_description=description,
                    expected=_qty(po_item.quantity),
                    actual=_qty(delivery_item.quantity),
                    delta=_qty(float(do_q - po_q)),
                    explanation=(
                        "The delivery-order quantity does not equal the purchase-order quantity. "
                        "This can be legitimate for partial deliveries, so it requires review."
                    ),
                    sources=[
                        _evidence_reference(
                            po,
                            f"line_items.{po_index}.quantity",
                            _qty(po_item.quantity),
                        ),
                        _evidence_reference(
                            delivery,
                            f"line_items.{delivery_index}.quantity",
                            _qty(delivery_item.quantity),
                        ),
                    ],
                )

        if invoice_item is None:
            line_has_issue = True
            _add_issue(
                issues,
                code="item_missing_from_invoice",
                title="Ordered item not found on invoice",
                severity="low",
                item_description=description,
                expected=f"Ordered quantity {_qty(po_item.quantity)}",
                actual="No matching invoice line",
                explanation=(
                    "The engine could not match this purchase-order item to an invoice line. "
                    "This may be expected when billing is incomplete or split."
                ),
                sources=[
                    _evidence_reference(
                        po,
                        f"line_items.{po_index}.description",
                        po_item.description,
                    )
                ],
            )
        else:
            po_price = _decimal(po_item.unit_price)
            invoice_price = _decimal(invoice_item.unit_price)
            if (
                po_price is not None
                and invoice_price is not None
                and abs(po_price - invoice_price) > MONEY_TOLERANCE
            ):
                line_has_issue = True
                _add_issue(
                    issues,
                    code="invoice_price_variance",
                    title="Invoice price differs from purchase order",
                    severity="medium",
                    item_description=description,
                    expected=_money(po.extraction.currency, po_item.unit_price),
                    actual=_money(invoice.extraction.currency, invoice_item.unit_price),
                    delta=_delta_money(
                        invoice.extraction.currency or po.extraction.currency,
                        invoice_item.unit_price,
                        po_item.unit_price,
                    ),
                    explanation=(
                        "The extracted invoice unit price differs from the agreed unit price "
                        "shown on the purchase order."
                    ),
                    sources=[
                        _evidence_reference(
                            po,
                            f"line_items.{po_index}.unit_price",
                            _money(po.extraction.currency, po_item.unit_price),
                        ),
                        _evidence_reference(
                            invoice,
                            f"line_items.{invoice_index}.unit_price",
                            _money(invoice.extraction.currency, invoice_item.unit_price),
                        ),
                    ],
                )

            invoice_q = _decimal(invoice_item.quantity)
            if delivery_item is not None:
                do_q = _decimal(delivery_item.quantity)
                if (
                    do_q is not None
                    and invoice_q is not None
                    and abs(do_q - invoice_q) > QUANTITY_TOLERANCE
                ):
                    line_has_issue = True
                    severity = "high" if invoice_q > do_q else "medium"
                    _add_issue(
                        issues,
                        code="invoice_delivery_quantity_variance",
                        title="Invoiced quantity differs from delivered quantity",
                        severity=severity,
                        item_description=description,
                        expected=_qty(delivery_item.quantity),
                        actual=_qty(invoice_item.quantity),
                        delta=_qty(float(invoice_q - do_q)),
                        explanation=(
                            "The invoice quantity does not equal the quantity shown as delivered."
                        ),
                        sources=[
                            _evidence_reference(
                                delivery,
                                f"line_items.{delivery_index}.quantity",
                                _qty(delivery_item.quantity),
                            ),
                            _evidence_reference(
                                invoice,
                                f"line_items.{invoice_index}.quantity",
                                _qty(invoice_item.quantity),
                            ),
                        ],
                    )

            quantity = _decimal(invoice_item.quantity)
            unit_price = _decimal(invoice_item.unit_price)
            printed_line_total = _decimal(invoice_item.line_total)
            if (
                quantity is not None
                and unit_price is not None
                and printed_line_total is not None
                and abs((quantity * unit_price) - printed_line_total) > MONEY_TOLERANCE
            ):
                line_has_issue = True
                calculated = float(quantity * unit_price)
                _add_issue(
                    issues,
                    code="invoice_line_arithmetic_mismatch",
                    title="Invoice line arithmetic does not add up",
                    severity="high",
                    item_description=description,
                    expected=_money(invoice.extraction.currency, calculated),
                    actual=_money(invoice.extraction.currency, invoice_item.line_total),
                    delta=_delta_money(
                        invoice.extraction.currency,
                        invoice_item.line_total,
                        calculated,
                    ),
                    explanation=(
                        "Quantity multiplied by unit price does not equal the printed line total. "
                        "The printed value has not been altered."
                    ),
                    sources=[
                        _evidence_reference(
                            invoice,
                            f"line_items.{invoice_index}.quantity",
                            _qty(invoice_item.quantity),
                        ),
                        _evidence_reference(
                            invoice,
                            f"line_items.{invoice_index}.unit_price",
                            _money(invoice.extraction.currency, invoice_item.unit_price),
                        ),
                        _evidence_reference(
                            invoice,
                            f"line_items.{invoice_index}.line_total",
                            _money(invoice.extraction.currency, invoice_item.line_total),
                        ),
                    ],
                )

        lines.append(
            MatchedLine(
                key=key,
                description=description,
                sku=po_item.sku,
                po_quantity=po_item.quantity,
                delivered_quantity=delivery_item.quantity if delivery_item else None,
                invoice_quantity=invoice_item.quantity if invoice_item else None,
                po_unit_price=po_item.unit_price,
                invoice_unit_price=invoice_item.unit_price if invoice_item else None,
                status="review_required" if line_has_issue else "matched",
            )
        )

    for index, item in enumerate(delivery_items):
        if index in used_delivery:
            continue
        _add_issue(
            issues,
            code="unexpected_delivery_item",
            title="Delivery item not found on purchase order",
            severity="medium",
            item_description=item.description,
            actual=f"Delivered quantity {_qty(item.quantity)}",
            explanation=(
                "This delivery-order line could not be matched to any purchase-order line."
            ),
            sources=[
                _evidence_reference(
                    delivery, f"line_items.{index}.description", item.description
                )
            ],
        )
        lines.append(
            MatchedLine(
                key=item.sku or _normalise_text(item.description) or f"do-{index + 1}",
                description=item.description,
                sku=item.sku,
                po_quantity=None,
                delivered_quantity=item.quantity,
                invoice_quantity=None,
                po_unit_price=None,
                invoice_unit_price=None,
                status="unmatched",
            )
        )

    for index, item in enumerate(invoice_items):
        if index in used_invoice:
            continue
        _add_issue(
            issues,
            code="unexpected_invoice_item",
            title="Invoice item not found on purchase order",
            severity="high",
            item_description=item.description,
            actual=(
                f"Invoice quantity {_qty(item.quantity)} at "
                f"{_money(invoice.extraction.currency, item.unit_price)}"
            ),
            explanation=(
                "This invoice line could not be matched to any purchase-order line."
            ),
            sources=[
                _evidence_reference(
                    invoice, f"line_items.{index}.description", item.description
                )
            ],
        )
        lines.append(
            MatchedLine(
                key=item.sku or _normalise_text(item.description) or f"inv-{index + 1}",
                description=item.description,
                sku=item.sku,
                po_quantity=None,
                delivered_quantity=None,
                invoice_quantity=item.quantity,
                po_unit_price=None,
                invoice_unit_price=item.unit_price,
                status="unmatched",
            )
        )

    summary = ReconciliationSummary(
        issue_count=len(issues),
        high=sum(issue.severity == "high" for issue in issues),
        medium=sum(issue.severity == "medium" for issue in issues),
        low=sum(issue.severity == "low" for issue in issues),
        matched_lines=sum(line.status == "matched" for line in lines),
        review_lines=sum(line.status != "matched" for line in lines),
    )

    return ReconciliationResult(
        transaction_id=transaction_id,
        status="matched" if not issues else "review_required",
        documents=documents,
        summary=summary,
        lines=lines,
        issues=issues,
        generated_at=datetime.now(timezone.utc),
    )
