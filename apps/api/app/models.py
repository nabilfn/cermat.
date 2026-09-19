"""SQLAlchemy models. The schema itself is owned by Alembic migrations."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID, uuid4

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _workspace_fk() -> Mapped[UUID]:
    return mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )


# ---------------------------------------------------------------------------
# Identity & tenancy
# ---------------------------------------------------------------------------


class UserModel(Base):
    __tablename__ = "users"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    email: Mapped[str] = mapped_column(String(254), unique=True, index=True)  # stored lowercased
    display_name: Mapped[str] = mapped_column(String(80))
    password_hash: Mapped[str] = mapped_column(String(255))  # Argon2id, never plaintext
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class WorkspaceModel(Base):
    __tablename__ = "workspaces"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    name: Mapped[str] = mapped_column(String(120))
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    # True only for the workspace that received data created before Phase 7.
    is_legacy: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    created_by: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)


class WorkspaceMemberModel(Base):
    __tablename__ = "workspace_members"
    __table_args__ = (UniqueConstraint("workspace_id", "user_id", name="uq_workspace_member"),)

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    workspace_id: Mapped[UUID] = _workspace_fk()
    user_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    role: Mapped[str] = mapped_column(String(16))  # owner | member
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class SessionModel(Base):
    """Server-side session. Only HMACs of the session and CSRF tokens are stored."""

    __tablename__ = "auth_sessions"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    csrf_hash: Mapped[str] = mapped_column(String(64))
    user_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class AuditEventModel(Base):
    """Append-only operational audit trail. Never updated or deleted by the API."""

    __tablename__ = "audit_events"
    __table_args__ = (Index("ix_audit_events_workspace_created", "workspace_id", "created_at"),)

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    workspace_id: Mapped[UUID] = _workspace_fk()
    actor_user_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    action: Mapped[str] = mapped_column(String(60), index=True)
    entity_type: Mapped[str] = mapped_column(String(40))
    entity_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict)
    request_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


# ---------------------------------------------------------------------------
# Business records — every row belongs to exactly one workspace
# ---------------------------------------------------------------------------


class DocumentModel(Base):
    __tablename__ = "documents"
    __table_args__ = (Index("ix_documents_workspace_created", "workspace_id", "created_at"),)

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    workspace_id: Mapped[UUID] = _workspace_fk()
    filename: Mapped[str] = mapped_column(String(255))  # original name — metadata only
    document_type: Mapped[str] = mapped_column(String(32), index=True)
    mime_type: Mapped[str] = mapped_column(String(100))
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    page_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # uploaded | processing | extracted | needs_review | failed
    status: Mapped[str] = mapped_column(String(32), default="uploaded", index=True)
    error_code: Mapped[str | None] = mapped_column(String(40), nullable=True)
    # Server-generated object key. Null for demo records that have no source file.
    storage_key: Mapped[str | None] = mapped_column(String(300), nullable=True)
    extraction_model: Mapped[str | None] = mapped_column(String(100), nullable=True)
    extraction_data: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    uploaded_by: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)


class TransactionSetModel(Base):
    __tablename__ = "transaction_sets"
    __table_args__ = (Index("ix_transaction_sets_workspace_updated", "workspace_id", "updated_at"),)

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    workspace_id: Mapped[UUID] = _workspace_fk()
    name: Mapped[str] = mapped_column(String(120))
    status: Mapped[str] = mapped_column(String(32), default="collecting", index=True)
    last_reconciliation: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_by: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)


class TransactionDocumentModel(Base):
    __tablename__ = "transaction_documents"
    __table_args__ = (
        UniqueConstraint("transaction_id", "document_type", name="uq_transaction_document_type"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    transaction_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("transaction_sets.id", ondelete="CASCADE"), index=True
    )
    document_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("documents.id", ondelete="CASCADE"), unique=True, index=True
    )
    document_type: Mapped[str] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class ReviewIssueModel(Base):
    __tablename__ = "review_issues"
    __table_args__ = (
        UniqueConstraint("transaction_id", "issue_key", name="uq_transaction_review_issue_key"),
        Index("ix_review_issues_workspace_active_status", "workspace_id", "active", "status"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    workspace_id: Mapped[UUID] = _workspace_fk()
    transaction_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("transaction_sets.id", ondelete="CASCADE"), index=True
    )
    issue_key: Mapped[str] = mapped_column(String(64))
    code: Mapped[str] = mapped_column(String(100), index=True)
    title: Mapped[str] = mapped_column(String(180))
    severity: Mapped[str] = mapped_column(String(16), index=True)
    status: Mapped[str] = mapped_column(String(16), default="open", index=True)
    resolution_note: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    resolved_by: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    payload: Mapped[dict] = mapped_column(JSON)
    active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)


class IntelligenceSettingsModel(Base):
    """Operational thresholds per workspace. Absent row means defaults."""

    __tablename__ = "intelligence_settings"

    workspace_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), primary_key=True
    )
    config: Mapped[dict] = mapped_column(JSON)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)


class AttentionEventModel(Base):
    """In-app attention queue. ``event_key`` is stable per workspace, so a condition notifies once."""

    __tablename__ = "attention_events"
    __table_args__ = (
        UniqueConstraint("workspace_id", "event_key", name="uq_attention_workspace_event_key"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    workspace_id: Mapped[UUID] = _workspace_fk()
    event_key: Mapped[str] = mapped_column(String(200))
    event_type: Mapped[str] = mapped_column(String(40), index=True)
    title: Mapped[str] = mapped_column(String(200))
    message: Mapped[str] = mapped_column(String(600))
    severity: Mapped[str] = mapped_column(String(16))
    entity_type: Mapped[str] = mapped_column(String(20))
    entity_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    entity_label: Mapped[str | None] = mapped_column(String(200), nullable=True)
    transaction_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("transaction_sets.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, index=True)
    seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    dismissed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Set when the underlying condition no longer holds (e.g. issue resolved).
    cleared_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
