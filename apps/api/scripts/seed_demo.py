"""Seed a local workspace with demo data for Ask cermat. and the Overview.

Inserts pre-extracted documents (no model call), then runs the real
deterministic reconciliation and review-issue sync used by the API.
Transactions are backdated across ~80 days so trends, recurring patterns,
anomalies, overdue reviews and resolution times have something to show.

    docker compose exec api python -m scripts.seed_demo

Opt-in only: nothing seeds automatically. Skips any demo transaction whose
name already exists, so it is safe to run twice.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from sqlalchemy import select

from app.database import SessionLocal, init_db
from app.main import _document_summary, _refresh_transaction_status, _sync_review_issues
from app.models import DocumentModel, TransactionDocumentModel, TransactionSetModel
from app.schemas import AIExtraction, DocumentType
from app.services.reconciliation import ReconciliationDocument, reconcile_three_way

SYMBOLS = {"MYR": "RM", "USD": "US$", "SGD": "S$"}


def _extraction(
    *,
    supplier: str,
    number: str,
    currency: str,
    items: list[tuple[str, str, float, float | None]],
    printed_totals: list[float | None] | None = None,
    confidence: float = 0.93,
    document_date: str | None = "2026-09-10",
) -> dict:
    symbol = SYMBOLS.get(currency, currency)
    line_items = []
    evidence = [
        {"field_path": "supplier_name", "source_text": supplier, "page": 1, "confidence": 0.97},
        {"field_path": "document_number", "source_text": f"No. {number}", "page": 1, "confidence": 0.98},
        {"field_path": "currency", "source_text": symbol, "page": 1, "confidence": 0.9},
    ]
    total = 0.0
    for index, (description, sku, quantity, unit_price) in enumerate(items):
        printed = (printed_totals or [None] * len(items))[index]
        line_total = printed if printed is not None else (
            round(quantity * unit_price, 2) if unit_price is not None else None
        )
        total += line_total or 0
        line_items.append(
            {
                "description": description,
                "sku": sku,
                "quantity": quantity,
                "unit_price": unit_price,
                "line_total": line_total,
            }
        )
        evidence.append(
            {
                "field_path": f"line_items.{index}.description",
                "source_text": f"{sku} {description}",
                "page": 1,
                "confidence": 0.93,
            }
        )
        evidence.append(
            {
                "field_path": f"line_items.{index}.quantity",
                "source_text": f"{description} Qty {quantity:g}",
                "page": 1,
                "confidence": 0.9,
            }
        )
        if unit_price is not None:
            evidence.append(
                {
                    "field_path": f"line_items.{index}.unit_price",
                    "source_text": f"Unit Price {symbol}{unit_price:.2f}",
                    "page": 1,
                    "confidence": 0.92,
                }
            )
        if line_total is not None:
            evidence.append(
                {
                    "field_path": f"line_items.{index}.line_total",
                    "source_text": f"Amount {symbol}{line_total:,.2f}",
                    "page": 1,
                    "confidence": 0.9,
                }
            )
    has_prices = any(item[3] is not None for item in items)
    if has_prices:
        evidence.append(
            {"field_path": "total", "source_text": f"TOTAL {symbol}{total:,.2f}", "page": 1, "confidence": 0.96}
        )
    return AIExtraction(
        supplier_name=supplier,
        supplier_registration_no=None,
        document_number=number,
        document_date=document_date,
        currency=currency,
        subtotal=round(total, 2) if has_prices else None,
        tax=None,
        total=round(total, 2) if has_prices else None,
        line_items=line_items,
        evidence=evidence,
        overall_confidence=confidence,
        review_reasons=["Some fields were hard to read."] if confidence < 0.75 else [],
    ).model_dump(mode="json")


ABC = "ABC Supplies Sdn. Bhd."
DELTA = "Delta Office Sdn Bhd"
NORTHWIND = "Northwind Stationery"
PACIFIC = "Pacific Office Imports"
INKWELL = "Inkwell Trading"

CHAIR = ("Ergonomic Office Chair", "CHAIR-01")
PAPER = ("A4 Paper 80gsm", "PAPER-A4")
STAPLER = ("Stapler Heavy Duty", "STP-HD")
CABINET = ("Steel Filing Cabinet", "CAB-4D")
MONITOR_ARM = ("Dual Monitor Arm", "ARM-2X")
TONER = ("Toner Cartridge 85A", "TN-85A")
DESK_LAMP = ("LED Desk Lamp", "LAMP-01")


def three_way(
    supplier: str,
    po: str,
    do: str,
    inv: str,
    lines: list[tuple[tuple[str, str], float, float, float, float]],
    *,
    currency: str = "MYR",
    invoice_supplier: str | None = None,
    invoice_currency: str | None = None,
    invoice_confidence: float = 0.93,
    invoice_date: str | None = "2026-09-10",
) -> dict:
    """lines: ((description, sku), ordered, delivered, invoiced, (po_price, inv_price))."""
    return {
        "purchase_order": dict(
            supplier=supplier, number=po, currency=currency,
            items=[(d, s, ordered, po_price) for (d, s), ordered, _, _, (po_price, _) in lines],
        ),
        "delivery_order": dict(
            supplier=supplier, number=do, currency=currency,
            items=[(d, s, delivered, None) for (d, s), _, delivered, _, _ in lines],
        ),
        "invoice": dict(
            supplier=invoice_supplier or supplier, number=inv,
            currency=invoice_currency or currency,
            items=[(d, s, invoiced, inv_price) for (d, s), _, _, invoiced, (_, inv_price) in lines],
            confidence=invoice_confidence,
            document_date=invoice_date,
        ),
    }


DEMO = [
    # --- Phase 5 demo set (unchanged) --------------------------------------
    {
        "name": "PO-2026-001",
        "documents": {
            "purchase_order": dict(
                supplier=ABC, number="PO-2026-001", currency="MYR",
                items=[("Ergonomic Office Chair", "CHAIR-01", 10, 42.0), ("A4 Paper 80gsm", "PAPER-A4", 20, 12.5)],
            ),
            "delivery_order": dict(
                supplier=ABC, number="DO-0933", currency="MYR",
                items=[("Ergonomic Office Chair", "CHAIR-01", 8, None), ("A4 Paper 80gsm", "PAPER-A4", 20, None)],
            ),
            "invoice": dict(
                supplier="ABC Supplies Sdn Bhd", number="INV-4418", currency="MYR",
                items=[("Ergonomic Office Chair", "CHAIR-01", 10, 44.0), ("A4 Paper 80gsm", "PAPER-A4", 20, 12.5)],
            ),
        },
    },
    {
        "name": "PO-2026-091",
        "documents": {
            "purchase_order": dict(
                supplier=ABC, number="PO-2026-091", currency="MYR",
                items=[("Whiteboard Marker (Box)", "MARK-12", 30, 18.0)],
            ),
            "delivery_order": dict(
                supplier=ABC, number="DO-1011", currency="MYR",
                items=[("Whiteboard Marker (Box)", "MARK-12", 30, None)],
            ),
            "invoice": dict(
                supplier=ABC, number="INV-4482", currency="MYR",
                items=[("Whiteboard Marker (Box)", "MARK-12", 30, 19.5)],
                printed_totals=[595.0],
            ),
        },
    },
    {
        "name": "PO-2026-103",
        "documents": {
            "purchase_order": dict(
                supplier=DELTA, number="PO-2026-103", currency="MYR",
                items=[("Steel Filing Cabinet", "CAB-4D", 12, 385.0)],
            ),
            "delivery_order": dict(
                supplier=DELTA, number="DO-2210", currency="MYR",
                items=[("Steel Filing Cabinet", "CAB-4D", 10, None)],
            ),
            "invoice": dict(
                supplier="Delta Offices Trading", number="INV-7730", currency="MYR",
                items=[("Steel Filing Cabinet", "CAB-4D", 10, 385.0)],
            ),
        },
        "resolve": {"delivery_quantity_variance": "Balance of 2 cabinets shipped on DO-2244."},
    },
    {
        "name": "PO-2026-114",
        "documents": {
            "purchase_order": dict(
                supplier=NORTHWIND, number="PO-2026-114", currency="MYR",
                items=[("Stapler Heavy Duty", "STP-HD", 15, 29.9)],
            ),
            "delivery_order": dict(
                supplier=NORTHWIND, number="DO-3301", currency="MYR",
                items=[("Stapler Heavy Duty", "STP-HD", 15, None)],
            ),
            "invoice": dict(
                supplier=NORTHWIND, number="INV-0192", currency="MYR",
                items=[("Stapler Heavy Duty", "STP-HD", 15, 29.9)],
            ),
        },
    },
    {
        "name": "PO-2026-120",
        "documents": {
            "purchase_order": dict(
                supplier=INKWELL, number="PO-2026-120", currency="MYR",
                items=[("Toner Cartridge 85A", "TN-85A", 6, 210.0)],
            ),
        },
    },
    # --- Phase 6 history (backdated) ---------------------------------------
    # ABC Supplies: recurring small price increases, one large outlier.
    {
        "name": "PO-2026-021", "days_ago": 80,
        "documents": three_way(ABC, "PO-2026-021", "DO-0601", "INV-3810", [(STAPLER, 20, 20, 20, (25.0, 26.0))]),
        "resolve": {"invoice_price_variance": ("Supplier issued credit note CN-118.", 30)},
    },
    {
        "name": "PO-2026-048", "days_ago": 55,
        "documents": three_way(ABC, "PO-2026-048", "DO-0712", "INV-3977", [(PAPER, 40, 40, 40, (12.5, 13.0))]),
        "resolve": {"invoice_price_variance": ("Accepted: supplier price list updated in August.", 50)},
    },
    {
        "name": "PO-2026-067", "days_ago": 35,
        "documents": three_way(ABC, "PO-2026-067", "DO-0788", "INV-4105", [(PAPER, 30, 30, 30, (12.5, 12.5))]),
    },
    {
        "name": "PO-2026-083", "days_ago": 20,
        "documents": three_way(ABC, "PO-2026-083", "DO-0851", "INV-4260", [(CHAIR, 10, 10, 10, (42.0, 43.0))]),
    },
    {
        "name": "PO-2026-097", "days_ago": 12,
        "documents": three_way(
            ABC, "PO-2026-097", "DO-0902", "INV-4391",
            [(MONITOR_ARM, 6, 6, 6, (180.0, 212.76)), (CHAIR, 4, 4, 4, (42.0, 44.0))],
        ),
    },
    # Delta Office: invoices repeatedly exceed delivered quantities.
    {
        "name": "PO-2026-058", "days_ago": 58,
        "documents": three_way(DELTA, "PO-2026-058", "DO-2011", "INV-7302", [(CABINET, 5, 4, 5, (385.0, 385.0))]),
        "resolve": {
            "invoice_delivery_quantity_variance": ("Fifth cabinet delivered late on DO-2019.", 20),
            "delivery_quantity_variance": ("Fifth cabinet delivered late on DO-2019.", 20),
        },
    },
    {
        "name": "PO-2026-066", "days_ago": 45,
        "documents": three_way(DELTA, "PO-2026-066", "DO-2080", "INV-7415", [(DESK_LAMP, 10, 10, 10, (68.0, 68.0))]),
    },
    {
        "name": "PO-2026-075", "days_ago": 28,
        "documents": three_way(DELTA, "PO-2026-075", "DO-2144", "INV-7561", [(CABINET, 12, 10, 12, (385.0, 385.0))]),
    },
    {
        "name": "PO-2026-110", "days_ago": 5,
        "documents": three_way(DELTA, "PO-2026-110", "DO-2301", "INV-7802", [(CABINET, 8, 6, 8, (385.0, 385.0))]),
    },
    # Northwind: clean history, then one order far above its usual size.
    {
        "name": "PO-2026-030", "days_ago": 70,
        "documents": three_way(NORTHWIND, "PO-2026-030", "DO-3101", "INV-0141", [(STAPLER, 14, 14, 14, (29.9, 29.9))]),
    },
    {
        "name": "PO-2026-062", "days_ago": 40,
        "documents": three_way(NORTHWIND, "PO-2026-062", "DO-3188", "INV-0163", [(STAPLER, 16, 16, 16, (29.9, 29.9))]),
    },
    {
        "name": "PO-2026-101", "days_ago": 15,
        "documents": three_way(NORTHWIND, "PO-2026-101", "DO-3260", "INV-0180", [(STAPLER, 15, 15, 15, (29.9, 29.9))]),
    },
    {
        "name": "PO-2026-121", "days_ago": 0,
        "documents": three_way(NORTHWIND, "PO-2026-121", "DO-3340", "INV-0204", [(STAPLER, 100, 100, 100, (29.9, 29.9))]),
    },
    # Pacific Office Imports: new USD supplier, immediate currency mismatch.
    {
        "name": "PO-2026-105", "days_ago": 9,
        "documents": three_way(
            PACIFIC, "PO-2026-105", "DO-P-0017", "INV-P-2291",
            [(DESK_LAMP, 50, 50, 50, (12.0, 12.8))],
            currency="USD", invoice_confidence=0.62, invoice_date=None,
        ),
    },
    {
        "name": "PO-2026-112", "days_ago": 4,
        "documents": three_way(
            PACIFIC, "PO-2026-112", "DO-P-0021", "INV-P-2310",
            [(MONITOR_ARM, 10, 10, 10, (150.0, 150.0))],
            currency="USD", invoice_currency="SGD",
        ),
    },
    # Inkwell: purchase orders keep arriving without invoices.
    {
        "name": "PO-2026-099", "days_ago": 10,
        "documents": {
            "purchase_order": dict(supplier=INKWELL, number="PO-2026-099", currency="MYR", items=[(*TONER, 4, 210.0)]),
            "delivery_order": dict(supplier=INKWELL, number="DO-5102", currency="MYR", items=[(*TONER, 4, None)]),
        },
    },
    {
        "name": "PO-2026-102", "days_ago": 8,
        "documents": {
            "purchase_order": dict(supplier=INKWELL, number="PO-2026-102", currency="MYR", items=[(*TONER, 2, 210.0)]),
        },
    },
    {
        "name": "PO-2026-107", "days_ago": 6,
        "documents": {
            "purchase_order": dict(supplier=INKWELL, number="PO-2026-107", currency="MYR", items=[(*TONER, 3, 210.0)]),
        },
    },
]


async def seed() -> None:
    await init_db()
    now = datetime.now(timezone.utc)
    async with SessionLocal() as session:
        for spec in DEMO:
            existing = await session.execute(
                select(TransactionSetModel).where(TransactionSetModel.name == spec["name"])
            )
            if existing.scalar_one_or_none():
                print(f"skip  {spec['name']} (exists)")
                continue

            created = now - timedelta(days=spec.get("days_ago", 0))
            transaction = TransactionSetModel(name=spec["name"], status="collecting")
            session.add(transaction)
            await session.flush()

            documents: dict[str, DocumentModel] = {}
            for document_type, fields in spec["documents"].items():
                # Date backdated documents; an explicit None (unreadable date) is kept.
                if "days_ago" in spec and fields.get("document_date", "2026-09-10") == "2026-09-10":
                    fields = {**fields, "document_date": created.date().isoformat()}
                document = DocumentModel(
                    id=uuid4(),
                    filename=f"{fields['number'].lower()}.pdf",
                    document_type=document_type,
                    mime_type="application/pdf",
                    size_bytes=0,
                    status="extracted",
                    storage_path="seed://demo",
                    extraction_model="seed",
                    extraction_data=_extraction(**fields),
                    created_at=created,
                    updated_at=created,
                )
                session.add(document)
                await session.flush()
                session.add(
                    TransactionDocumentModel(
                        transaction_id=transaction.id,
                        document_id=document.id,
                        document_type=document_type,
                    )
                )
                documents[document_type] = document
            await session.flush()

            if {"purchase_order", "delivery_order", "invoice"} <= set(documents):
                def as_input(kind: DocumentType) -> ReconciliationDocument:
                    document = documents[kind.value]
                    return ReconciliationDocument(
                        id=document.id,
                        filename=document.filename,
                        document_type=kind,
                        extraction=AIExtraction.model_validate(document.extraction_data),
                    )

                result = reconcile_three_way(
                    transaction_id=transaction.id,
                    po=as_input(DocumentType.purchase_order),
                    delivery=as_input(DocumentType.delivery_order),
                    invoice=as_input(DocumentType.invoice),
                    documents=[_document_summary(doc) for doc in documents.values()],
                )
                result.generated_at = created + timedelta(hours=2)
                transaction.last_reconciliation = result.model_dump(mode="json")
                issues = await _sync_review_issues(session, transaction, result)
                for issue in issues:
                    issue.created_at = created + timedelta(hours=2)
                    issue.updated_at = issue.created_at
                    resolution = spec.get("resolve", {}).get(issue.code)
                    if resolution:
                        note, hours = resolution if isinstance(resolution, tuple) else (resolution, 0)
                        issue.status = "resolved"
                        issue.resolution_note = note
                        issue.resolved_at = (
                            issue.created_at + timedelta(hours=hours) if hours else now
                        )
                        issue.updated_at = issue.resolved_at
                await _refresh_transaction_status(session, transaction)
                print(f"seed  {spec['name']}: {result.status}, {len(result.issues)} issue(s)")
            else:
                print(f"seed  {spec['name']}: collecting")
            transaction.created_at = created
            transaction.updated_at = created + timedelta(hours=2)
            await session.commit()


if __name__ == "__main__":
    asyncio.run(seed())
