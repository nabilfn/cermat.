"""PostgreSQL integration tests for the intelligence loaders and attention sync.

Runs against a separate ``cermat_test`` database on the same server (created if
missing, reset on every test). Never touches the workspace database. Skipped
when PostgreSQL is not reachable.
"""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

import asyncpg
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config import settings as app_settings
from app.database import Base
from app.intelligence.attention import Candidate, list_attention, sync_attention
from app.intelligence.config import load_settings, merge_settings, save_settings
from app.intelligence.dataset import load_dataset
from app.models import (
    AttentionEventModel,
    DocumentModel,
    ReviewIssueModel,
    TransactionDocumentModel,
    TransactionSetModel,
)
from app.schemas import IntelligenceSettings, IntelligenceSettingsUpdate

TEST_DB = "cermat_test"
NOW = datetime.now(timezone.utc)


class IntelligenceDatabaseTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        base = app_settings.database_url
        try:
            admin = await asyncpg.connect(base.replace("postgresql+asyncpg", "postgresql"), timeout=3)
        except Exception:  # noqa: BLE001
            self.skipTest("PostgreSQL is not reachable")
        if not await admin.fetchval("SELECT 1 FROM pg_database WHERE datname = $1", TEST_DB):
            await admin.execute(f'CREATE DATABASE "{TEST_DB}"')
        await admin.close()

        self.engine = create_async_engine(base.rsplit("/", 1)[0] + f"/{TEST_DB}")
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.drop_all)
            await connection.run_sync(Base.metadata.create_all)
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)

    async def asyncTearDown(self) -> None:
        await self.engine.dispose()

    async def _seed(self) -> TransactionSetModel:
        async with self.sessions() as session:
            txn = TransactionSetModel(
                name="PO-T-1",
                status="review_required",
                created_at=NOW - timedelta(days=3),
                last_reconciliation={
                    "generated_at": NOW.isoformat(),
                    "lines": [{"description": "Chair", "sku": "C-1", "invoice_quantity": 10,
                               "invoice_unit_price": 44.0, "status": "review_required"}],
                },
            )
            session.add(txn)
            await session.flush()
            document = DocumentModel(
                filename="inv.pdf", document_type="invoice", mime_type="application/pdf",
                size_bytes=1, status="extracted", storage_path="x",
                extraction_data={
                    "supplier_name": "ABC Supplies Sdn. Bhd.", "document_number": "INV-1",
                    "document_date": None, "currency": "MYR", "total": 440.0,
                    "overall_confidence": 0.61,
                    "evidence": [{"field_path": "total"}, {"field_path": "currency"}],
                    "line_items": [{"description": "x" * 5000}],  # never loaded
                },
            )
            session.add(document)
            await session.flush()
            session.add(TransactionDocumentModel(
                transaction_id=txn.id, document_id=document.id, document_type="invoice",
            ))
            common = dict(transaction_id=txn.id, title="t", severity="medium", code="invoice_price_variance")
            session.add_all([
                ReviewIssueModel(issue_key="open", status="open", active=True,
                                 payload={"expected": "MYR 42.00", "actual": "MYR 44.00", "item_description": "Chair"},
                                 **common),
                ReviewIssueModel(issue_key="resolved", status="resolved", active=True,
                                 resolved_at=NOW, payload={"expected": "MYR 1.00", "actual": "MYR 2.00"}, **common),
                ReviewIssueModel(issue_key="stale", status="open", active=False,
                                 payload={"expected": "MYR 5.00", "actual": "MYR 9.00"}, **common),
            ])
            await session.commit()
            return txn

    async def test_loader_projects_fields_and_excludes_inactive_issues(self) -> None:
        await self._seed()
        async with self.sessions() as session:
            dataset = await load_dataset(session)
        [txn] = dataset.transactions
        self.assertTrue(txn.reconciled)
        self.assertEqual(txn.lines[0]["invoice_quantity"], 10)
        [doc] = txn.documents
        self.assertEqual((doc.supplier_name, doc.currency, doc.total), ("ABC Supplies Sdn. Bhd.", "MYR", 440.0))
        self.assertEqual((doc.overall_confidence, doc.evidence_count, doc.document_date), (0.61, 2, None))
        self.assertEqual(sorted(i.status for i in txn.issues), ["open", "resolved"])  # inactive excluded
        self.assertEqual(txn.supplier_key, "abc-supplies")

    async def test_attention_sync_is_idempotent_in_postgres(self) -> None:
        txn = await self._seed()
        candidate = Candidate(
            event_key="high_severity_issue:demo", event_type="high_severity_issue", title="t",
            message="m", severity="high", entity_type="transaction", entity_id=str(txn.id),
            entity_label=txn.name, transaction_id=txn.id,
        )
        for _ in range(3):
            async with self.sessions() as session:
                await sync_attention(session, [candidate], NOW)
        async with self.sessions() as session:
            count = await session.scalar(select(func.count()).select_from(AttentionEventModel))
            listing = await list_attention(session)
        self.assertEqual(count, 1)
        self.assertEqual((listing.active_count, listing.unseen_count), (1, 1))

        async with self.sessions() as session:
            await sync_attention(session, [], NOW)  # condition cleared
            self.assertEqual((await list_attention(session)).active_count, 0)
            await sync_attention(session, [candidate], NOW)  # holds again → restored
            self.assertEqual((await list_attention(session)).active_count, 1)
            self.assertEqual(await session.scalar(select(func.count()).select_from(AttentionEventModel)), 1)

    async def test_settings_round_trip(self) -> None:
        async with self.sessions() as session:
            loaded, updated_at = await load_settings(session)
            self.assertEqual((loaded, updated_at), (IntelligenceSettings(), None))
            merged = merge_settings(loaded, IntelligenceSettingsUpdate(overdue_review_days=10))
            record = await save_settings(session, merged)
            self.assertFalse(record.is_default)
        async with self.sessions() as session:
            loaded, _ = await load_settings(session)
            self.assertEqual(loaded.overdue_review_days, 10)


if __name__ == "__main__":
    unittest.main()
