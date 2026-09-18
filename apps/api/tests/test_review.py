import unittest
from uuid import uuid4

from app.schemas import DocumentType, EvidenceReference, ReconciliationIssue
from app.services.review import make_issue_key


class ReviewIssueIdentityTests(unittest.TestCase):
    def issue(self, actual: str = "MYR 44.00") -> ReconciliationIssue:
        return ReconciliationIssue(
            code="unit_price_mismatch",
            title="Invoice price differs from purchase order",
            severity="medium",
            item_description="Office chair",
            expected="MYR 42.00",
            actual=actual,
            delta="+MYR 2.00 (+4.76%)",
            explanation="Price differs.",
            sources=[
                EvidenceReference(
                    document_id=uuid4(),
                    filename="po.pdf",
                    document_type=DocumentType.purchase_order,
                    field_path="line_items.0.unit_price",
                    source_text="RM42.00",
                    page=1,
                    value="MYR 42.00",
                ),
                EvidenceReference(
                    document_id=uuid4(),
                    filename="invoice.pdf",
                    document_type=DocumentType.invoice,
                    field_path="line_items.0.unit_price",
                    source_text="RM44.00",
                    page=1,
                    value=actual,
                ),
            ],
        )

    def test_same_business_issue_gets_same_key_across_reruns(self):
        first = self.issue()
        second = self.issue()
        self.assertEqual(make_issue_key(first), make_issue_key(second))

    def test_materially_changed_issue_gets_new_key(self):
        first = self.issue("MYR 44.00")
        changed = self.issue("MYR 45.00")
        self.assertNotEqual(make_issue_key(first), make_issue_key(changed))


if __name__ == "__main__":
    unittest.main()
