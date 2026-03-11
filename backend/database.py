from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from backend.config import settings
from backend.models.db import Base

# PostgreSQL connection pooling (ignored for SQLite)
pool_args = {}
if settings.db_url.startswith("postgresql"):
    pool_args = {"pool_size": 10, "max_overflow": 20, "pool_pre_ping": True}

engine = create_async_engine(settings.db_url, echo=False, future=True, **pool_args)
AsyncSessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def init_db() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await _ensure_event_summary_columns(conn)
        await _ensure_agent_memory_indexes(conn)
        await _ensure_home_threshold_configs(conn)


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

    if "source_event_id" not in existing_columns:
        await conn.execute(text("ALTER TABLE events ADD COLUMN source_event_id VARCHAR"))

    if "source" not in existing_columns:
        await conn.execute(text("ALTER TABLE events ADD COLUMN source VARCHAR"))

    if "zone" not in existing_columns:
        await conn.execute(text("ALTER TABLE events ADD COLUMN zone VARCHAR"))

    if "event_context" not in existing_columns:
        await conn.execute(text("ALTER TABLE events ADD COLUMN event_context TEXT NOT NULL DEFAULT '{}'"))

    if "sequence_id" not in existing_columns:
        await conn.execute(text("ALTER TABLE events ADD COLUMN sequence_id VARCHAR"))

    if "sequence_position" not in existing_columns:
        await conn.execute(text("ALTER TABLE events ADD COLUMN sequence_position INTEGER"))

    if "sequence_type" not in existing_columns:
        await conn.execute(text("ALTER TABLE events ADD COLUMN sequence_type VARCHAR"))

    if "parent_event_id" not in existing_columns:
        await conn.execute(text("ALTER TABLE events ADD COLUMN parent_event_id VARCHAR"))

    if "user_tag" not in existing_columns:
        await conn.execute(text("ALTER TABLE events ADD COLUMN user_tag VARCHAR"))

    if "user_feedback" not in existing_columns:
        await conn.execute(text("ALTER TABLE events ADD COLUMN user_feedback VARCHAR"))

    if "user_feedback_timestamp" not in existing_columns:
        await conn.execute(text("ALTER TABLE events ADD COLUMN user_feedback_timestamp DATETIME"))

    # Create dedup index if missing (for existing DBs migrated via ALTER)
    idx_result = await conn.execute(
        text("SELECT name FROM sqlite_master WHERE type='index' AND name='ix_events_source_dedup'")
    )
    if idx_result.scalar_one_or_none() is None:
        try:
            await conn.execute(
                text("CREATE UNIQUE INDEX ix_events_source_dedup ON events (source, source_event_id)")
            )
        except Exception:
            pass  # Columns might not exist yet; create_all will add index for fresh DBs

    seq_idx_result = await conn.execute(
        text("SELECT name FROM sqlite_master WHERE type='index' AND name='ix_events_sequence'")
    )
    if seq_idx_result.scalar_one_or_none() is None:
        try:
            await conn.execute(
                text("CREATE INDEX ix_events_sequence ON events (sequence_id, sequence_position)")
            )
        except Exception:
            pass


async def get_db() -> AsyncSession:
    async with AsyncSessionLocal() as session:
        yield session


async def _ensure_home_threshold_configs(conn) -> None:
    """Ensure HomeThresholdConfig entries exist for all sites with events."""
    if conn.dialect.name != "sqlite":
        return

    # Get all unique site_ids from events table
    result = await conn.execute(
        text("""
            SELECT DISTINCT s.site_id 
            FROM streams s 
            WHERE s.site_id IS NOT NULL
        """)
    )
    site_ids = [row[0] for row in result.fetchall()]

    # For each site, check if threshold config exists; create if not
    for site_id in site_ids:
        existing = await conn.execute(
            text("SELECT id FROM home_threshold_configs WHERE site_id = :site_id"),
            {"site_id": site_id},
        )
        if existing.scalar_one_or_none() is None:
            # Insert default threshold config for this site
            import uuid
            config_id = str(uuid.uuid4())
            await conn.execute(
                text("""
                    INSERT INTO home_threshold_configs 
                    (id, site_id, vote_confidence_threshold, strong_vote_threshold, 
                     min_alert_confidence, fp_count_30d, fn_count_30d, total_alerts_30d)
                    VALUES (:id, :site_id, :vote_threshold, :strong_threshold, :min_alert, 0, 0, 0)
                """),
                {
                    "id": config_id,
                    "site_id": site_id,
                    "vote_threshold": 0.55,
                    "strong_threshold": 0.70,
                    "min_alert": 0.35,
                },
            )


async def _ensure_agent_memory_indexes(conn) -> None:
    if conn.dialect.name != "sqlite":
        return

    idx_result = await conn.execute(
        text("SELECT name FROM sqlite_master WHERE type='index' AND name='ix_agent_memories_scope_key'")
    )
    if idx_result.scalar_one_or_none() is None:
        try:
            await conn.execute(
                text(
                    "CREATE UNIQUE INDEX ix_agent_memories_scope_key "
                    "ON agent_memories (scope_type, scope_id, memory_key)"
                )
            )
        except Exception:
            pass
