"""Baseline: the Phase 1–6 schema.

Idempotent on purpose. Databases created before Alembic (via create_all)
already have these tables; `alembic upgrade head` adopts them unchanged.
A fresh database gets exactly the same schema.

Revision ID: 0001_baseline
Revises:
Create Date: 2026-09-19
"""

from alembic import op

revision = "0001_baseline"
down_revision = None
branch_labels = None
depends_on = None

STATEMENTS = [
    """
    CREATE TABLE IF NOT EXISTS documents (
        id uuid PRIMARY KEY,
        filename varchar(255) NOT NULL,
        document_type varchar(32) NOT NULL,
        mime_type varchar(100) NOT NULL,
        size_bytes bigint NOT NULL,
        status varchar(32) NOT NULL,
        storage_path varchar(500) NOT NULL,
        extraction_model varchar(100),
        extraction_data json,
        created_at timestamptz NOT NULL,
        updated_at timestamptz NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS transaction_sets (
        id uuid PRIMARY KEY,
        name varchar(120) NOT NULL,
        status varchar(32) NOT NULL,
        last_reconciliation json,
        created_at timestamptz NOT NULL,
        updated_at timestamptz NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS transaction_documents (
        id uuid PRIMARY KEY,
        transaction_id uuid NOT NULL REFERENCES transaction_sets(id) ON DELETE CASCADE,
        document_id uuid NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
        document_type varchar(32) NOT NULL,
        created_at timestamptz NOT NULL,
        CONSTRAINT uq_transaction_document_type UNIQUE (transaction_id, document_type)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS review_issues (
        id uuid PRIMARY KEY,
        transaction_id uuid NOT NULL REFERENCES transaction_sets(id) ON DELETE CASCADE,
        issue_key varchar(64) NOT NULL,
        code varchar(100) NOT NULL,
        title varchar(180) NOT NULL,
        severity varchar(16) NOT NULL,
        status varchar(16) NOT NULL,
        resolution_note varchar(1000),
        payload json NOT NULL,
        active boolean NOT NULL,
        resolved_at timestamptz,
        created_at timestamptz NOT NULL,
        updated_at timestamptz NOT NULL,
        CONSTRAINT uq_transaction_review_issue_key UNIQUE (transaction_id, issue_key)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS intelligence_settings (
        id integer PRIMARY KEY,
        config json NOT NULL,
        updated_at timestamptz NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS attention_events (
        id uuid PRIMARY KEY,
        event_key varchar(200) NOT NULL,
        event_type varchar(40) NOT NULL,
        title varchar(200) NOT NULL,
        message varchar(600) NOT NULL,
        severity varchar(16) NOT NULL,
        entity_type varchar(20) NOT NULL,
        entity_id varchar(200),
        entity_label varchar(200),
        transaction_id uuid REFERENCES transaction_sets(id) ON DELETE CASCADE,
        created_at timestamptz NOT NULL,
        seen_at timestamptz,
        dismissed_at timestamptz,
        cleared_at timestamptz
    )
    """,
    "CREATE INDEX IF NOT EXISTS ix_documents_document_type ON documents (document_type)",
    "CREATE INDEX IF NOT EXISTS ix_documents_status ON documents (status)",
    "CREATE INDEX IF NOT EXISTS ix_documents_supplier_name ON documents ((extraction_data->>'supplier_name'))",
    "CREATE INDEX IF NOT EXISTS ix_transaction_sets_status ON transaction_sets (status)",
    "CREATE INDEX IF NOT EXISTS ix_transaction_sets_created_at ON transaction_sets (created_at)",
    "CREATE INDEX IF NOT EXISTS ix_transaction_sets_updated_at ON transaction_sets (updated_at)",
    "CREATE UNIQUE INDEX IF NOT EXISTS ix_transaction_documents_document_id ON transaction_documents (document_id)",
    "CREATE INDEX IF NOT EXISTS ix_transaction_documents_transaction_id ON transaction_documents (transaction_id)",
    "CREATE INDEX IF NOT EXISTS ix_review_issues_transaction_id ON review_issues (transaction_id)",
    "CREATE INDEX IF NOT EXISTS ix_review_issues_code ON review_issues (code)",
    "CREATE INDEX IF NOT EXISTS ix_review_issues_severity ON review_issues (severity)",
    "CREATE INDEX IF NOT EXISTS ix_review_issues_status ON review_issues (status)",
    "CREATE INDEX IF NOT EXISTS ix_review_issues_active ON review_issues (active)",
    "CREATE INDEX IF NOT EXISTS ix_review_issues_active_status ON review_issues (active, status)",
    "CREATE INDEX IF NOT EXISTS ix_review_issues_created_at ON review_issues (created_at)",
    "CREATE INDEX IF NOT EXISTS ix_review_issues_resolved_at ON review_issues (resolved_at)",
    "CREATE UNIQUE INDEX IF NOT EXISTS ix_attention_events_event_key ON attention_events (event_key)",
    "CREATE INDEX IF NOT EXISTS ix_attention_events_event_type ON attention_events (event_type)",
    "CREATE INDEX IF NOT EXISTS ix_attention_events_transaction_id ON attention_events (transaction_id)",
    "CREATE INDEX IF NOT EXISTS ix_attention_events_created_at ON attention_events (created_at)",
]


def upgrade() -> None:
    for statement in STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    for table in (
        "attention_events",
        "intelligence_settings",
        "review_issues",
        "transaction_documents",
        "transaction_sets",
        "documents",
    ):
        op.execute(f"DROP TABLE IF EXISTS {table} CASCADE")
