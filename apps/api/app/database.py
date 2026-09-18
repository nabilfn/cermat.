from __future__ import annotations

from collections.abc import AsyncIterator

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.config import settings


class Base(DeclarativeBase):
    pass


engine = create_async_engine(settings.database_url, pool_pre_ping=True)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False)


async def get_session() -> AsyncIterator[AsyncSession]:
    async with SessionLocal() as session:
        yield session


INTELLIGENCE_INDEXES = (
    "CREATE INDEX IF NOT EXISTS ix_review_issues_created_at ON review_issues (created_at)",
    "CREATE INDEX IF NOT EXISTS ix_review_issues_resolved_at ON review_issues (resolved_at)",
    "CREATE INDEX IF NOT EXISTS ix_review_issues_active_status ON review_issues (active, status)",
    "CREATE INDEX IF NOT EXISTS ix_transaction_sets_created_at ON transaction_sets (created_at)",
    "CREATE INDEX IF NOT EXISTS ix_transaction_sets_updated_at ON transaction_sets (updated_at)",
    "CREATE INDEX IF NOT EXISTS ix_documents_supplier_name "
    "ON documents ((extraction_data->>'supplier_name'))",
)


async def init_db() -> None:
    from app import models  # noqa: F401

    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
        # create_all does not add indexes to existing tables; these are idempotent.
        for statement in INTELLIGENCE_INDEXES:
            await connection.execute(text(statement))
