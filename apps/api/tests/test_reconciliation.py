from __future__ import annotations

import unittest
from uuid import uuid4

from app.schemas import (
    AIExtraction,
    DocumentType,
    ExtractedLineItem,
    TransactionDocumentSummary,
)
from app.services.reconciliation import ReconciliationDocument, reconcile_three_way


class ReconciliationTests(unittest.TestCase):
    def extraction(self, items: list[ExtractedLineItem]) -> AIExtraction:
        return AIExtraction(
            supplier_name="Demo Supplier Sdn. Bhd.",
            supplier_registration_no=None,
            document_number="DOC-1",
            document_date="2026-09-18",
            currency="MYR",
            subtotal=None,
            tax=None,
            total=None,
            line_items=items,
            evidence=[],
            overall_confidence=0.95,
            review_reasons=[],
        )

    def document(
        self,
        document_type: DocumentType,
        filename: str,
        items: list[ExtractedLineItem],
    ) -> ReconciliationDocument:
        return ReconciliationDocument(
            id=uuid4(),
            filename=filename,
            document_type=document_type,
            extraction=self.extraction(items),
        )

    def summary(self, document: ReconciliationDocument) -> TransactionDocumentSummary:
        return TransactionDocumentSummary(
            id=document.id,
            filename=document.filename,
            document_type=document.document_type,
            status="extracted",
            document_number="DOC-1",
            supplier_name="Demo Supplier Sdn. Bhd.",
            currency="MYR",
            total=None,
            overall_confidence=0.95,
        )

    def test_flags_quantity_and_price_variances(self) -> None:
        po = self.document(
            DocumentType.purchase_order,
            "po.pdf",
            [
                ExtractedLineItem(
                    description="Office Chair",
                    sku="CHAIR-1",
                    quantity=10,
                    unit_price=42,
                    line_total=420,
                )
            ],
        )
        delivery = self.document(
            DocumentType.delivery_order,
            "do.pdf",
            [
                ExtractedLineItem(
                    description="Office Chair",
                    sku="CHAIR-1",
                    quantity=8,
                    unit_price=None,
                    line_total=None,
                )
            ],
        )
        invoice = self.document(
            DocumentType.invoice,
            "invoice.pdf",
            [
                ExtractedLineItem(
                    description="Office Chair",
                    sku="CHAIR-1",
                    quantity=10,
                    unit_price=44,
                    line_total=440,
                )
            ],
        )

        result = reconcile_three_way(
            transaction_id=uuid4(),
            po=po,
            delivery=delivery,
            invoice=invoice,
            documents=[
                self.summary(po),
                self.summary(delivery),
                self.summary(invoice),
            ],
        )

        codes = {issue.code for issue in result.issues}
        self.assertEqual(result.status, "review_required")
        self.assertIn("delivery_quantity_variance", codes)
        self.assertIn("invoice_delivery_quantity_variance", codes)
        self.assertIn("invoice_price_variance", codes)

    def test_clean_transaction_matches(self) -> None:
        item = ExtractedLineItem(
            description="Printer Paper A4",
            sku="PAPER-A4",
            quantity=5,
            unit_price=20,
            line_total=100,
        )
        po = self.document(DocumentType.purchase_order, "po.pdf", [item])
        delivery = self.document(
            DocumentType.delivery_order,
            "do.pdf",
            [item.model_copy(update={"unit_price": None, "line_total": None})],
        )
        invoice = self.document(DocumentType.invoice, "invoice.pdf", [item])

        result = reconcile_three_way(
            transaction_id=uuid4(),
            po=po,
            delivery=delivery,
            invoice=invoice,
            documents=[
                self.summary(po),
                self.summary(delivery),
                self.summary(invoice),
            ],
        )

        self.assertEqual(result.status, "matched")
        self.assertEqual(result.summary.issue_count, 0)
        self.assertEqual(result.summary.matched_lines, 1)


if __name__ == "__main__":
    unittest.main()
