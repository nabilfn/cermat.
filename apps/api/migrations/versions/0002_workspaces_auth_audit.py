"""Phase 7: users, workspaces, sessions, audit trail, workspace ownership.

Existing business rows (created before workspaces existed) are moved into a
single "Legacy workspace" (is_legacy = true). No data is deleted.

Other changes:
- documents.storage_path (absolute container path) → storage_key (object key)
- documents: page_count, error_code, uploaded_by; status 'extracting' → 'processing'
- transaction_sets.created_by, review_issues.resolved_by
- attention event keys are unique per workspace (not globally)
- intelligence_settings keyed by workspace instead of a single id=1 row

Revision ID: 0002_workspaces_auth_audit
Revises: 0001_baseline
Create Date: 2026-09-19
"""

from alembic import op

revision = "0002_workspaces_auth_audit"
down_revision = "0001_baseline"
branch_labels = None
depends_on = None


def _add_fk(table: str, name: str, column: str, target: str, on_delete: str) -> str:
    return f"""
    DO $$ BEGIN
      IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = '{name}') THEN
        ALTER TABLE {table} ADD CONSTRAINT {name}
          FOREIGN KEY ({column}) REFERENCES {target}(id) ON DELETE {on_delete};
      END IF;
    END $$;
    """


def upgrade() -> None:
    # --- identity & tenancy tables ---------------------------------------
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            id uuid PRIMARY KEY,
            email varchar(254) NOT NULL,
            display_name varchar(80) NOT NULL,
            password_hash varchar(255) NOT NULL,
            created_at timestamptz NOT NULL,
            last_login_at timestamptz
        )
        """
    )
    op.execute("CREATE UNIQUE INDEX IF NOT EXISTS ix_users_email ON users (email)")
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS workspaces (
            id uuid PRIMARY KEY,
            name varchar(120) NOT NULL,
            is_demo boolean NOT NULL DEFAULT false,
            is_legacy boolean NOT NULL DEFAULT false,
            created_by uuid REFERENCES users(id) ON DELETE SET NULL,
            created_at timestamptz NOT NULL,
            updated_at timestamptz NOT NULL
        )
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS workspace_members (
            id uuid PRIMARY KEY,
            workspace_id uuid NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
            user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            role varchar(16) NOT NULL,
            created_at timestamptz NOT NULL,
            CONSTRAINT uq_workspace_member UNIQUE (workspace_id, user_id)
        )
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_workspace_members_workspace_id ON workspace_members (workspace_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_workspace_members_user_id ON workspace_members (user_id)")
    op.execute(
        """
        DO $$ BEGIN
          IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'ck_workspace_member_role') THEN
            ALTER TABLE workspace_members ADD CONSTRAINT ck_workspace_member_role
              CHECK (role IN ('owner', 'member'));
          END IF;
        END $$;
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS auth_sessions (
            id uuid PRIMARY KEY,
            token_hash varchar(64) NOT NULL,
            csrf_hash varchar(64) NOT NULL,
            user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            created_at timestamptz NOT NULL,
            expires_at timestamptz NOT NULL,
            last_seen_at timestamptz NOT NULL
        )
        """
    )
    op.execute("CREATE UNIQUE INDEX IF NOT EXISTS ix_auth_sessions_token_hash ON auth_sessions (token_hash)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_auth_sessions_user_id ON auth_sessions (user_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_auth_sessions_expires_at ON auth_sessions (expires_at)")
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS audit_events (
            id uuid PRIMARY KEY,
            workspace_id uuid NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
            actor_user_id uuid REFERENCES users(id) ON DELETE SET NULL,
            action varchar(60) NOT NULL,
            entity_type varchar(40) NOT NULL,
            entity_id varchar(64),
            metadata_json json NOT NULL,
            request_id varchar(64),
            created_at timestamptz NOT NULL
        )
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_audit_events_workspace_id ON audit_events (workspace_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_audit_events_workspace_created ON audit_events (workspace_id, created_at)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_audit_events_action ON audit_events (action)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_audit_events_entity_id ON audit_events (entity_id)")

    # --- workspace ownership columns ----------------------------------------
    for table in ("documents", "transaction_sets", "review_issues", "attention_events"):
        op.execute(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS workspace_id uuid")

    # Pre-existing rows → one legacy workspace (only if there is anything to adopt).
    op.execute(
        """
        DO $$
        DECLARE legacy uuid;
        BEGIN
          IF EXISTS (SELECT 1 FROM documents WHERE workspace_id IS NULL)
             OR EXISTS (SELECT 1 FROM transaction_sets WHERE workspace_id IS NULL)
             OR EXISTS (SELECT 1 FROM attention_events WHERE workspace_id IS NULL)
             OR EXISTS (SELECT 1 FROM information_schema.columns
                        WHERE table_name = 'intelligence_settings' AND column_name = 'id')
                AND EXISTS (SELECT 1 FROM intelligence_settings) THEN
            SELECT id INTO legacy FROM workspaces WHERE is_legacy LIMIT 1;
            IF legacy IS NULL THEN
              INSERT INTO workspaces (id, name, is_demo, is_legacy, created_at, updated_at)
              VALUES (gen_random_uuid(), 'Legacy workspace', false, true, now(), now())
              RETURNING id INTO legacy;
            END IF;
            UPDATE documents SET workspace_id = legacy WHERE workspace_id IS NULL;
            UPDATE transaction_sets SET workspace_id = legacy WHERE workspace_id IS NULL;
            UPDATE attention_events SET workspace_id = legacy WHERE workspace_id IS NULL;
          END IF;
        END $$;
        """
    )
    op.execute(
        """
        UPDATE review_issues r SET workspace_id = t.workspace_id
        FROM transaction_sets t
        WHERE r.transaction_id = t.id AND r.workspace_id IS NULL
        """
    )
    for table in ("documents", "transaction_sets", "review_issues", "attention_events"):
        op.execute(f"ALTER TABLE {table} ALTER COLUMN workspace_id SET NOT NULL")
        op.execute(_add_fk(table, f"fk_{table}_workspace", "workspace_id", "workspaces", "CASCADE"))
        op.execute(f"CREATE INDEX IF NOT EXISTS ix_{table}_workspace_id ON {table} (workspace_id)")

    # --- documents: storage key, recoverable extraction state -----------------
    op.execute("ALTER TABLE documents ADD COLUMN IF NOT EXISTS storage_key varchar(300)")
    op.execute("ALTER TABLE documents ADD COLUMN IF NOT EXISTS page_count integer")
    op.execute("ALTER TABLE documents ADD COLUMN IF NOT EXISTS error_code varchar(40)")
    op.execute("ALTER TABLE documents ADD COLUMN IF NOT EXISTS uploaded_by uuid")
    op.execute(_add_fk("documents", "fk_documents_uploaded_by", "uploaded_by", "users", "SET NULL"))
    op.execute(
        """
        DO $$ BEGIN
          IF EXISTS (SELECT 1 FROM information_schema.columns
                     WHERE table_name = 'documents' AND column_name = 'storage_path') THEN
            -- Absolute container paths become bare object keys; seed placeholders become NULL.
            UPDATE documents
               SET storage_key = regexp_replace(storage_path, '^.*/', '')
             WHERE storage_key IS NULL AND storage_path LIKE '/%';
            ALTER TABLE documents DROP COLUMN storage_path;
          END IF;
        END $$;
        """
    )
    op.execute("UPDATE documents SET status = 'processing' WHERE status = 'extracting'")
    op.execute("CREATE INDEX IF NOT EXISTS ix_documents_workspace_created ON documents (workspace_id, created_at)")

    # --- actor columns -------------------------------------------------------
    op.execute("ALTER TABLE transaction_sets ADD COLUMN IF NOT EXISTS created_by uuid")
    op.execute(_add_fk("transaction_sets", "fk_transaction_sets_created_by", "created_by", "users", "SET NULL"))
    op.execute("ALTER TABLE review_issues ADD COLUMN IF NOT EXISTS resolved_by uuid")
    op.execute(_add_fk("review_issues", "fk_review_issues_resolved_by", "resolved_by", "users", "SET NULL"))
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_transaction_sets_workspace_updated "
        "ON transaction_sets (workspace_id, updated_at)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_review_issues_workspace_active_status "
        "ON review_issues (workspace_id, active, status)"
    )

    # --- attention keys unique per workspace ----------------------------------
    op.execute("DROP INDEX IF EXISTS ix_attention_events_event_key")
    op.execute(
        """
        DO $$ BEGIN
          IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'uq_attention_workspace_event_key') THEN
            ALTER TABLE attention_events ADD CONSTRAINT uq_attention_workspace_event_key
              UNIQUE (workspace_id, event_key);
          END IF;
        END $$;
        """
    )

    # --- intelligence settings per workspace -----------------------------------
    op.execute(
        """
        DO $$ BEGIN
          IF EXISTS (SELECT 1 FROM information_schema.columns
                     WHERE table_name = 'intelligence_settings' AND column_name = 'id') THEN
            CREATE TABLE intelligence_settings_v7 (
              workspace_id uuid PRIMARY KEY REFERENCES workspaces(id) ON DELETE CASCADE,
              config json NOT NULL,
              updated_at timestamptz NOT NULL
            );
            INSERT INTO intelligence_settings_v7 (workspace_id, config, updated_at)
              SELECT w.id, s.config, s.updated_at
                FROM intelligence_settings s
                JOIN workspaces w ON w.is_legacy
               WHERE s.id = 1;
            DROP TABLE intelligence_settings;
            ALTER TABLE intelligence_settings_v7 RENAME TO intelligence_settings;
            ALTER TABLE intelligence_settings
              RENAME CONSTRAINT intelligence_settings_v7_pkey TO intelligence_settings_pkey;
          END IF;
        END $$;
        """
    )


def downgrade() -> None:
    raise NotImplementedError(
        "0002 moves data into workspaces and drops storage_path; restore from a backup "
        "taken before upgrading instead of downgrading."
    )
