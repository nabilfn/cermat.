"""Seed a local workspace with demo transactions for trying Ask cermat.

Inserts pre-extracted documents (no model call), then runs the real
deterministic reconciliation and review-issue sync used by the API.

    docker compose exec api python -m scripts.seed_demo

Skips any demo transaction whose name already exists.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import select

from app.database import SessionLocal, init_db
from app.main import _document_summary, _refresh_transaction_status, _sync_review_issues
from app.models import DocumentModel, TransactionDocumentModel, TransactionSetModel
from app.schemas import AIExtraction, DocumentType
from app.services.reconciliation import ReconciliationDocument, reconcile_three_way


def _extraction(
    *,
    supplier: str,
    number: str,
    currency: str,
    items: list[tuple[str, str, float, float | None]],
    printed_totals: list[float | None] | None = None,
) -> dict:
    line_items = []
    evidence = [
        {"field_path": "supplier_name", "source_text": supplier, "page": 1, "confidence": 0.97},
        {"field_path": "document_number", "source_text": f"No. {number}", "page": 1, "confidence": 0.98},
        {"field_path": "currency", "source_text": "RM" if currency == "MYR" else currency, "page": 1, "confidence": 0.9},
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
                    "source_text": f"Unit Price RM{unit_price:.2f}",
                    "page": 1,
                    "confidence": 0.92,
                }
            )
        if line_total is not None:
            evidence.append(
                {
                    "field_path": f"line_items.{index}.line_total",
                    "source_text": f"Amount RM{line_total:,.2f}",
                    "page": 1,
                    "confidence": 0.9,
                }
            )
    has_prices = any(item[3] is not None for item in items)
    if has_prices:
        evidence.append(
            {"field_path": "total", "source_text": f"TOTAL RM{total:,.2f}", "page": 1, "confidence": 0.96}
        )
    return AIExtraction(
        supplier_name=supplier,
        supplier_registration_no=None,
        document_number=number,
        document_date="2026-09-10",
        currency=currency,
        subtotal=round(total, 2) if has_prices else None,
        tax=None,
        total=round(total, 2) if has_prices else None,
        line_items=line_items,
        evidence=evidence,
        overall_confidence=0.93,
        review_reasons=[],
    ).model_dump(mode="json")


DEMO = [
    {
        "name": "PO-2026-001",
        "documents": {
            "purchase_order": dict(
                supplier="ABC Supplies Sdn. Bhd.", number="PO-2026-001", currency="MYR",
                items=[("Ergonomic Office Chair", "CHAIR-01", 10, 42.0), ("A4 Paper 80gsm", "PAPER-A4", 20, 12.5)],
            ),
            "delivery_order": dict(
                supplier="ABC Supplies Sdn. Bhd.", number="DO-0933", currency="MYR",
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
                supplier="ABC Supplies Sdn. Bhd.", number="PO-2026-091", currency="MYR",
                items=[("Whiteboard Marker (Box)", "MARK-12", 30, 18.0)],
            ),
            "delivery_order": dict(
                supplier="ABC Supplies Sdn. Bhd.", number="DO-1011", currency="MYR",
                items=[("Whiteboard Marker (Box)", "MARK-12", 30, None)],
            ),
            "invoice": dict(
                supplier="ABC Supplies Sdn. Bhd.", number="INV-4482", currency="MYR",
                items=[("Whiteboard Marker (Box)", "MARK-12", 30, 19.5)],
                printed_totals=[595.0],
            ),
        },
    },
    {
        "name": "PO-2026-103",
        "documents": {
            "purchase_order": dict(
                supplier="Delta Office Sdn Bhd", number="PO-2026-103", currency="MYR",
                items=[("Steel Filing Cabinet", "CAB-4D", 12, 385.0)],
            ),
            "delivery_order": dict(
                supplier="Delta Office Sdn Bhd", number="DO-2210", currency="MYR",
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
                supplier="Northwind Stationery", number="PO-2026-114", currency="MYR",
                items=[("Stapler Heavy Duty", "STP-HD", 15, 29.9)],
            ),
            "delivery_order": dict(
                supplier="Northwind Stationery", number="DO-3301", currency="MYR",
                items=[("Stapler Heavy Duty", "STP-HD", 15, None)],
            ),
            "invoice": dict(
                supplier="Northwind Stationery", number="INV-0192", currency="MYR",
                items=[("Stapler Heavy Duty", "STP-HD", 15, 29.9)],
            ),
        },
    },
    {
        "name": "PO-2026-120",
        "documents": {
            "purchase_order": dict(
                supplier="Inkwell Trading", number="PO-2026-120", currency="MYR",
                items=[("Toner Cartridge 85A", "TN-85A", 6, 210.0)],
            ),
        },
    },
]


async def seed() -> None:
    await init_db()
    async with SessionLocal() as session:
        for spec in DEMO:
            existing = await session.execute(
                select(TransactionSetModel).where(TransactionSetModel.name == spec["name"])
            )
            if existing.scalar_one_or_none():
                print(f"skip  {spec['name']} (exists)")
                continue

            transaction = TransactionSetModel(name=spec["name"], status="collecting")
            session.add(transaction)
            await session.flush()

            documents: dict[str, DocumentModel] = {}
            for document_type, fields in spec["documents"].items():
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
                transaction.last_reconciliation = result.model_dump(mode="json")
                issues = await _sync_review_issues(session, transaction, result)
                for issue in issues:
                    note = spec.get("resolve", {}).get(issue.code)
                    if note:
                        issue.status = "resolved"
                        issue.resolution_note = note
                        issue.resolved_at = datetime.now(timezone.utc)
                await _refresh_transaction_status(session, transaction)
                print(f"seed  {spec['name']}: {result.status}, {len(result.issues)} issue(s)")
            else:
                print(f"seed  {spec['name']}: collecting")
            await session.commit()


if __name__ == "__main__":
    asyncio.run(seed())
