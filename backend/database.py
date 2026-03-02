from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from backend.config import settings
from backend.models.db import Base

engine = create_async_engine(settings.db_url, echo=False, future=True)
AsyncSessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def init_db() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await _ensure_event_summary_columns(conn)


async def _ensure_event_summary_columns(conn) -> None:
    if conn.dialect.name != "sqlite":
        return

    result = await conn.execute(text("PRAGMA table_info(events)"))
    existing_columns = {row[1] for row in result.fetchall()}

    if "summary" not in existing_columns:
        await conn.execute(text("ALTER TABLE events ADD COLUMN summary TEXT NOT NULL DEFAULT ''"))

    if "narrative_summary" not in existing_columns:
        await conn.execute(
            text("ALTER TABLE events ADD COLUMN narrative_summary TEXT NOT NULL DEFAULT ''")
        )


async def get_db() -> AsyncSession:
    async with AsyncSessionLocal() as session:
        yield session
