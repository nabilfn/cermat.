"""Database engine and sessions. The schema is owned by Alembic (``alembic upgrade head``)."""

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


async def database_ready() -> bool:
    """Readiness probe: can we reach PostgreSQL and is the schema migrated?"""
    try:
        async with engine.connect() as connection:
            await connection.execute(text("SELECT 1 FROM alembic_version LIMIT 1"))
        return True
    except Exception:  # noqa: BLE001 — readiness only reports up/down
        return False
