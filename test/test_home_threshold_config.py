"""Tests for HomeThresholdConfig adaptive threshold configuration."""

import pytest
import pytest_asyncio
from datetime import datetime
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database import AsyncSessionLocal, init_db, engine
from backend.models.db import HomeThresholdConfig, Stream, Event


@pytest_asyncio.fixture
async def db_initialized():
    """Initialize database tables before tests."""
    async with engine.begin() as conn:
        await conn.run_sync(lambda s: __import__('backend.models.db', fromlist=['Base']).Base.metadata.create_all(s))
        await init_db()
    yield
    # Cleanup: drop all tables after tests
    async with engine.begin() as conn:
        await conn.execute(text("DROP TABLE IF EXISTS home_threshold_configs"))
        await conn.execute(text("DROP TABLE IF EXISTS events"))
        await conn.execute(text("DROP TABLE IF EXISTS streams"))


@pytest.mark.asyncio
async def test_home_threshold_config_table_created(db_initialized):
    """Verify HomeThresholdConfig table is created with correct columns."""
    async with AsyncSessionLocal() as session:
        # Try to create a config
        config = HomeThresholdConfig(site_id="test_home_1")
        session.add(config)
        await session.commit()
        
        # Verify it was persisted
        result = await session.execute(
            select(HomeThresholdConfig).where(HomeThresholdConfig.site_id == "test_home_1")
        )
        retrieved = result.scalar_one_or_none()
        
        assert retrieved is not None
        assert retrieved.site_id == "test_home_1"
        assert retrieved.id is not None


@pytest.mark.asyncio
async def test_home_threshold_config_defaults(db_initialized):
    """Verify HomeThresholdConfig uses correct default threshold values."""
    async with AsyncSessionLocal() as session:
        config = HomeThresholdConfig(site_id="test_home_2")
        session.add(config)
        await session.commit()
        
        result = await session.execute(
            select(HomeThresholdConfig).where(HomeThresholdConfig.site_id == "test_home_2")
        )
        retrieved = result.scalar_one_or_none()
        
        # Verify defaults match Phase 2 plan
        assert retrieved.vote_confidence_threshold == 0.55
        assert retrieved.strong_vote_threshold == 0.70
        assert retrieved.min_alert_confidence == 0.35
        assert retrieved.fp_count_30d == 0
        assert retrieved.fn_count_30d == 0
        assert retrieved.total_alerts_30d == 0


@pytest.mark.asyncio
async def test_home_threshold_config_per_site_isolation(db_initialized):
    """Verify each site has independent threshold configuration."""
    async with AsyncSessionLocal() as session:
        # Create configs for two different homes
        config1 = HomeThresholdConfig(site_id="home_site_1")
        config2 = HomeThresholdConfig(site_id="home_site_2")
        session.add(config1)
        session.add(config2)
        await session.commit()
        
        # Update thresholds for first home
        result = await session.execute(
            select(HomeThresholdConfig).where(HomeThresholdConfig.site_id == "home_site_1")
        )
        config1_retrieved = result.scalar_one()
        config1_retrieved.vote_confidence_threshold = 0.65
        config1_retrieved.fp_count_30d = 5
        await session.commit()
        
        # Verify second home still has defaults
        result = await session.execute(
            select(HomeThresholdConfig).where(HomeThresholdConfig.site_id == "home_site_2")
        )
        config2_retrieved = result.scalar_one()
        
        assert config1_retrieved.vote_confidence_threshold == 0.65
        assert config1_retrieved.fp_count_30d == 5
        assert config2_retrieved.vote_confidence_threshold == 0.55
        assert config2_retrieved.fp_count_30d == 0


@pytest.mark.asyncio
async def test_home_threshold_config_feedback_counter_updates(db_initialized):
    """Verify feedback counters can be incremented atomically."""
    async with AsyncSessionLocal() as session:
        config = HomeThresholdConfig(site_id="counter_test_home")
        session.add(config)
        await session.commit()
        
        # Simulate feedback counter increments
        result = await session.execute(
            select(HomeThresholdConfig).where(HomeThresholdConfig.site_id == "counter_test_home")
        )
        config_retrieved = result.scalar_one()
        
        # Simulate 5 false positives, 2 false negatives, 10 total alerts
        config_retrieved.fp_count_30d = 5
        config_retrieved.fn_count_30d = 2
        config_retrieved.total_alerts_30d = 10
        config_retrieved.last_tuned = datetime.utcnow()
        config_retrieved.tuning_reason = "FP rate exceeded 20%"
        await session.commit()
        
        # Verify updates persisted
        result = await session.execute(
            select(HomeThresholdConfig).where(HomeThresholdConfig.site_id == "counter_test_home")
        )
        retrieved = result.scalar_one()
        
        assert retrieved.fp_count_30d == 5
        assert retrieved.fn_count_30d == 2
        assert retrieved.total_alerts_30d == 10
        assert retrieved.tuning_reason == "FP rate exceeded 20%"


@pytest.mark.asyncio
async def test_home_threshold_config_threshold_bounds(db_initialized):
    """Verify thresholds can be set within valid bounds [0.0, 1.0]."""
    async with AsyncSessionLocal() as session:
        config = HomeThresholdConfig(
            site_id="bounds_test_home",
            vote_confidence_threshold=0.75,
            strong_vote_threshold=0.95,
            min_alert_confidence=0.15,
        )
        session.add(config)
        await session.commit()
        
        result = await session.execute(
            select(HomeThresholdConfig).where(HomeThresholdConfig.site_id == "bounds_test_home")
        )
        retrieved = result.scalar_one()
        
        assert 0.0 <= retrieved.vote_confidence_threshold <= 1.0
        assert 0.0 <= retrieved.strong_vote_threshold <= 1.0
        assert 0.0 <= retrieved.min_alert_confidence <= 1.0


@pytest.mark.asyncio
async def test_home_threshold_config_unique_site_constraint(db_initialized):
    """Verify only one threshold config per site_id (unique constraint)."""
    async with AsyncSessionLocal() as session:
        config1 = HomeThresholdConfig(site_id="unique_test_home")
        session.add(config1)
        await session.commit()
        
        # Try to add duplicate (should raise IntegrityError)
        config2 = HomeThresholdConfig(site_id="unique_test_home")
        session.add(config2)
        
        with pytest.raises(Exception):  # SQLAlchemy IntegrityError
            await session.commit()
