"""
BarePulse — database layer.

Fully async SQLAlchemy 2.x setup. Defaults to a local SQLite file (zero
configuration, zero server cost) and switches to PostgreSQL the instant a
``DATABASE_URL`` environment variable is supplied — no code changes required.

    SQLite (default):  sqlite+aiosqlite:///./barepulse.db
    PostgreSQL:        postgresql+asyncpg://user:pass@host:5432/barepulse
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

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
DEFAULT_SQLITE_URL = "sqlite+aiosqlite:///./barepulse.db"
DATABASE_URL = os.getenv("DATABASE_URL", DEFAULT_SQLITE_URL).strip() or DEFAULT_SQLITE_URL

IS_SQLITE = DATABASE_URL.startswith("sqlite")

# SQLite needs check_same_thread disabled because the async driver hands the
# connection across the event-loop's worker threads.
_connect_args = {"check_same_thread": False} if IS_SQLITE else {}

# ---------------------------------------------------------------------------
# Engine & session factory
# ---------------------------------------------------------------------------
engine = create_async_engine(
    DATABASE_URL,
    echo=False,
    future=True,
    pool_pre_ping=True,
    connect_args=_connect_args,
)


# Enable WAL mode + sane sync level for SQLite. WAL lets readers and a writer
# work concurrently, which eliminates the classic "database is locked" errors
# under load. This is a no-op for any other backend.
if IS_SQLITE:

    @event.listens_for(engine.sync_engine, "connect")
    def _set_sqlite_pragmas(dbapi_connection, _connection_record):  # noqa: ANN001
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL;")
        cursor.execute("PRAGMA synchronous=NORMAL;")
        cursor.execute("PRAGMA busy_timeout=5000;")
        cursor.close()


AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
)


class Base(DeclarativeBase):
    """Declarative base shared by all ORM models."""


async def get_db() -> AsyncSession:  # type: ignore[misc]
    """FastAPI dependency that yields a scoped async session."""
    async with AsyncSessionLocal() as session:
        yield session


async def init_db() -> None:
    """Create tables on startup if they do not yet exist."""
    # Import models so they are registered on ``Base.metadata`` before create_all.
    from . import models  # noqa: F401

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
