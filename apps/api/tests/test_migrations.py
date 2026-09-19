"""Migrations: a clean database migrates, and a Phase 6 database upgrades without data loss."""

from __future__ import annotations

import asyncio
import unittest

import asyncpg
from support import _asyncpg_dsn, fresh_database, migrate

from app.config import settings

UPGRADE_DB = settings.database_url.rsplit("/", 1)[0] + "/cermat_upgrade_test"


async def _query(url: str, sql: str) -> list:
    connection = await asyncpg.connect(_asyncpg_dsn(url))
    try:
        return await connection.fetch(sql)
    finally:
        await connection.close()


async def _execute(url: str, sql: str) -> None:
    connection = await asyncpg.connect(_asyncpg_dsn(url))
    try:
        await connection.execute(sql)
    finally:
        await connection.close()


class MigrationTests(unittest.TestCase):
    def test_clean_database_migrates_to_head(self) -> None:
        if not fresh_database(UPGRADE_DB):
            self.skipTest("PostgreSQL not reachable")
        tables = {r["tablename"] for r in asyncio.run(_query(UPGRADE_DB, "SELECT tablename FROM pg_tables WHERE schemaname='public'"))}
        for table in ("users", "workspaces", "workspace_members", "auth_sessions", "audit_events",
                      "documents", "transaction_sets", "review_issues", "attention_events", "intelligence_settings"):
            self.assertIn(table, tables)
        version = asyncio.run(_query(UPGRADE_DB, "SELECT version_num FROM alembic_version"))[0]["version_num"]
        self.assertEqual(version, "0002_workspaces_auth_audit")

    def test_phase6_database_upgrades_preserving_data(self) -> None:
        if not fresh_database(UPGRADE_DB, revision="0001_baseline"):
            self.skipTest("PostgreSQL not reachable")
        asyncio.run(_execute(UPGRADE_DB, """
            INSERT INTO transaction_sets (id, name, status, created_at, updated_at)
              VALUES ('11111111-1111-1111-1111-111111111111', 'OLD-TXN', 'review_required', now(), now());
            INSERT INTO documents (id, filename, document_type, mime_type, size_bytes, status, storage_path, created_at, updated_at)
              VALUES ('22222222-2222-2222-2222-222222222222', 'po.pdf', 'purchase_order', 'application/pdf', 10,
                      'extracting', '/app/data/uploads/22222222.pdf', now(), now());
            INSERT INTO review_issues (id, transaction_id, issue_key, code, title, severity, status, payload, active, created_at, updated_at)
              VALUES ('33333333-3333-3333-3333-333333333333', '11111111-1111-1111-1111-111111111111', 'k', 'invoice_price_variance',
                      't', 'medium', 'resolved', '{}', true, now(), now());
            INSERT INTO attention_events (id, event_key, event_type, title, message, severity, entity_type, created_at)
              VALUES ('44444444-4444-4444-4444-444444444444', 'high_severity_issue:x', 'high_severity_issue', 't', 'm', 'high', 'workspace', now());
            INSERT INTO intelligence_settings (id, config, updated_at) VALUES (1, '{"overdue_review_days": 9}', now());
        """))

        migrate(UPGRADE_DB)
        rows = asyncio.run(_query(UPGRADE_DB, """
            SELECT
              (SELECT count(*) FROM workspaces WHERE is_legacy) AS legacy,
              (SELECT count(*) FROM transaction_sets WHERE workspace_id IS NULL) AS orphan_txns,
              (SELECT workspace_id = (SELECT id FROM workspaces WHERE is_legacy) FROM review_issues) AS issue_scoped,
              (SELECT storage_key FROM documents) AS storage_key,
              (SELECT status FROM documents) AS doc_status,
              (SELECT config::text FROM intelligence_settings) AS config,
              (SELECT count(*) FROM attention_events) AS events
        """))[0]
        self.assertEqual(rows["legacy"], 1)
        self.assertEqual(rows["orphan_txns"], 0)
        self.assertTrue(rows["issue_scoped"])
        self.assertEqual(rows["storage_key"], "22222222.pdf")
        self.assertEqual(rows["doc_status"], "processing")
        self.assertIn("overdue_review_days", rows["config"])
        self.assertEqual(rows["events"], 1)

        migrate(UPGRADE_DB)  # re-running is a no-op
