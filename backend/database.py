"""
Async SQLAlchemy engine, session factory, and database initialisation.

Uses aiosqlite as the async driver for SQLite.
WAL mode is enabled on every connection for concurrent read performance.
"""
from __future__ import annotations

import os

from sqlalchemy import event
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase


DATABASE_URL = os.getenv(
    "DATABASE_URL", "sqlite+aiosqlite:///data/job_agent.db"
)

engine = create_async_engine(DATABASE_URL, echo=False)


# SQLite performance pragmas applied on every new connection.
# sync_engine exposes the underlying synchronous engine that aiosqlite wraps.
@event.listens_for(engine.sync_engine, "connect")
def _set_sqlite_pragmas(dbapi_connection, connection_record):  # noqa: ANN001
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA synchronous=NORMAL")
    cursor.execute("PRAGMA cache_size=-64000")  # 64 MB page cache
    cursor.close()


AsyncSessionLocal = async_sessionmaker(
    engine, class_=AsyncSession, expire_on_commit=False
)


class Base(DeclarativeBase):
    """Shared declarative base — all ORM models inherit from this."""


async def get_session():
    """FastAPI dependency: yields an AsyncSession, auto-closed on exit."""
    async with AsyncSessionLocal() as session:
        yield session


async def init_db() -> None:
    """Create all tables defined on Base.metadata. Safe to run multiple times."""
    # Import models here so their table definitions are registered on Base.metadata
    import backend.models  # noqa: F401

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
