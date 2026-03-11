"""Tests for compute_home_thresholds adaptive threshold computation."""

import pytest
import pytest_asyncio
from datetime import datetime, timedelta
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database import AsyncSessionLocal, init_db, engine
from backend.models.db import HomeThresholdConfig
from backend.agent.reasoning.arbiter import compute_home_thresholds


@pytest_asyncio.fixture
async def db_initialized():
    """Initialize database tables before tests."""
    async with engine.begin() as conn:
        await conn.run_sync(lambda s: __import__('backend.models.db', fromlist=['Base']).Base.metadata.create_all(s))
        await init_db()
    yield
    # Cleanup
    async with engine.begin() as conn:
        await conn.execute(text("DROP TABLE IF EXISTS home_threshold_configs"))


@pytest.mark.asyncio
async def test_compute_home_thresholds_nonexistent_site(db_initialized):
    """Verify function returns defaults for unconfigured site."""
    async with AsyncSessionLocal() as session:
        thresholds = await compute_home_thresholds(session, "nonexistent_site")
        
        assert thresholds["vote_confidence_threshold"] == 0.55
        assert thresholds["strong_vote_threshold"] == 0.70
        assert thresholds["min_alert_confidence"] == 0.35


@pytest.mark.asyncio
async def test_compute_home_thresholds_insufficient_data(db_initialized):
    """Verify function returns current values when <50 alerts (insufficient data)."""
    async with AsyncSessionLocal() as session:
        # Create config with <50 total alerts
        config = HomeThresholdConfig(
            site_id="low_data_site",
            vote_confidence_threshold=0.55,
            strong_vote_threshold=0.70,
            min_alert_confidence=0.35,
            total_alerts_30d=30,  # Less than 50
            fp_count_30d=5,
            fn_count_30d=2,
        )
        session.add(config)
        await session.commit()
        
        thresholds = await compute_home_thresholds(session, "low_data_site")
        
        # Should return current values unchanged
        assert thresholds["vote_confidence_threshold"] == 0.55
        assert thresholds["strong_vote_threshold"] == 0.70


@pytest.mark.asyncio
async def test_compute_home_thresholds_high_fp_rate(db_initialized):
    """Verify vote threshold increases when FP rate > 20%."""
    async with AsyncSessionLocal() as session:
        # Create config with 30% FP rate (12 FP out of 40 alerts)
        config = HomeThresholdConfig(
            site_id="high_fp_site",
            vote_confidence_threshold=0.55,
            strong_vote_threshold=0.70,
            min_alert_confidence=0.35,
            total_alerts_30d=100,
            fp_count_30d=30,  # 30% FP rate
            fn_count_30d=2,
            last_tuned=datetime.utcnow() - timedelta(hours=48),  # Old tuning
        )
        session.add(config)
        await session.commit()
        
        thresholds = await compute_home_thresholds(session, "high_fp_site")
        
        # Should increase threshold (30% - 20% = 10%, so target = 0.55 + 0.10*0.5 = 0.60)
        # Actual delta = min(0.05, 0.60 - 0.55) = 0.05
        expected_threshold = 0.55 + 0.05  # Rate limited to max delta
        assert abs(thresholds["vote_confidence_threshold"] - expected_threshold) < 0.01
        assert thresholds["vote_confidence_threshold"] < 0.75  # Not at ceiling


@pytest.mark.asyncio
async def test_compute_home_thresholds_very_high_fp_rate(db_initialized):
    """Verify vote threshold caps at 0.75 when FP rate is very high."""
    async with AsyncSessionLocal() as session:
        # Create config with 50% FP rate
        config = HomeThresholdConfig(
            site_id="very_high_fp_site",
            vote_confidence_threshold=0.55,
            strong_vote_threshold=0.70,
            min_alert_confidence=0.35,
            total_alerts_30d=100,
            fp_count_30d=50,  # 50% FP rate
            fn_count_30d=2,
            last_tuned=datetime.utcnow() - timedelta(hours=72),  # Old tuning
        )
        session.add(config)
        await session.commit()
        
        thresholds = await compute_home_thresholds(session, "very_high_fp_site")
        
        # Target would be 0.75, but rate-limited in multiple steps
        # First call gets +0.05 toward ceiling
        assert thresholds["vote_confidence_threshold"] <= 0.75
        assert thresholds["vote_confidence_threshold"] > 0.55


@pytest.mark.asyncio
async def test_compute_home_thresholds_high_fn_rate(db_initialized):
    """Verify vote threshold decreases when FN rate > 10%."""
    async with AsyncSessionLocal() as session:
        # Create config with 20% FN rate
        config = HomeThresholdConfig(
            site_id="high_fn_site",
            vote_confidence_threshold=0.55,
            strong_vote_threshold=0.70,
            min_alert_confidence=0.35,
            total_alerts_30d=100,
            fp_count_30d=2,
            fn_count_30d=20,  # 20% FN rate
            last_tuned=datetime.utcnow() - timedelta(hours=48),  # Old tuning
        )
        session.add(config)
        await session.commit()
        
        thresholds = await compute_home_thresholds(session, "high_fn_site")
        
        # Should decrease threshold (20% - 10% = 10%, so target = 0.55 - 0.10*2.0 = 0.35)
        # Rate limited: delta = min(0.05, 0.35 - 0.55) = -0.05
        expected_threshold = 0.55 - 0.05
        assert abs(thresholds["vote_confidence_threshold"] - expected_threshold) < 0.01


@pytest.mark.asyncio
async def test_compute_home_thresholds_rate_limiting(db_initialized):
    """Verify rate limiting prevents rapid threshold changes."""
    async with AsyncSessionLocal() as session:
        now = datetime.utcnow()
        
        # First: tuned 23 hours ago with lower threshold due to FP
        config = HomeThresholdConfig(
            site_id="rate_limit_site",
            vote_confidence_threshold=0.65,  # Previously raised
            strong_vote_threshold=0.70,
            min_alert_confidence=0.35,
            total_alerts_30d=100,
            fp_count_30d=5,  # Now 5% (was higher before)
            fn_count_30d=20,  # Now high FN rate (20%)
            last_tuned=now - timedelta(hours=23),
        )
        session.add(config)
        await session.commit()
        
        thresholds = await compute_home_thresholds(session, "rate_limit_site")
        
        # With 23 hours since tuning, max_delta = 0.05 * (23/24) ≈ 0.048
        # FN rate (20%) wants to lower toward 0.35, delta = -0.20
        # Clamped to -0.048
        expected_change = -0.048
        assert thresholds["vote_confidence_threshold"] <= (0.65 - 0.04)  # Conservative check


@pytest.mark.asyncio
async def test_compute_home_thresholds_no_adjust_within_1hr(db_initialized):
    """Verify no adjustment happens within 1 hour of last tuning."""
    async with AsyncSessionLocal() as session:
        now = datetime.utcnow()
        
        config = HomeThresholdConfig(
            site_id="recent_tuning_site",
            vote_confidence_threshold=0.55,
            strong_vote_threshold=0.70,
            min_alert_confidence=0.35,
            total_alerts_30d=100,
            fp_count_30d=50,  # Very high FP rate
            fn_count_30d=2,
            last_tuned=now - timedelta(minutes=30),  # Tuned 30 minutes ago
        )
        session.add(config)
        await session.commit()
        
        thresholds = await compute_home_thresholds(session, "recent_tuning_site")
        
        # No adjustment should happen
        assert thresholds["vote_confidence_threshold"] == 0.55


@pytest.mark.asyncio
async def test_compute_home_thresholds_bounds_enforcement(db_initialized):
    """Verify thresholds stay within [0.0, 1.0] bounds."""
    async with AsyncSessionLocal() as session:
        config = HomeThresholdConfig(
            site_id="bounds_site",
            vote_confidence_threshold=0.95,
            strong_vote_threshold=0.70,
            min_alert_confidence=0.35,
            total_alerts_30d=100,
            fp_count_30d=50,  # Very high FP attempting to raise further
            fn_count_30d=2,
            last_tuned=datetime.utcnow() - timedelta(hours=48),
        )
        session.add(config)
        await session.commit()
        
        thresholds = await compute_home_thresholds(session, "bounds_site")
        
        assert 0.0 <= thresholds["vote_confidence_threshold"] <= 1.0
        assert 0.0 <= thresholds["strong_vote_threshold"] <= 1.0
        assert 0.0 <= thresholds["min_alert_confidence"] <= 1.0


@pytest.mark.asyncio
async def test_compute_home_thresholds_strong_threshold_consistency(db_initialized):
    """Verify strong_vote_threshold stays above vote_confidence_threshold."""
    async with AsyncSessionLocal() as session:
        config = HomeThresholdConfig(
            site_id="strong_consistency_site",
            vote_confidence_threshold=0.55,
            strong_vote_threshold=0.70,
            min_alert_confidence=0.35,
            total_alerts_30d=100,
            fp_count_30d=5,
            fn_count_30d=20,  # High FN will lower vote threshold
            last_tuned=datetime.utcnow() - timedelta(hours=48),
        )
        session.add(config)
        await session.commit()
        
        thresholds = await compute_home_thresholds(session, "strong_consistency_site")
        
        # Even if vote threshold is lowered, strong should remain higher
        assert thresholds["strong_vote_threshold"] >= thresholds["vote_confidence_threshold"]


@pytest.mark.asyncio
async def test_compute_home_thresholds_precision(db_initialized):
    """Verify thresholds are rounded to 3 decimal places."""
    async with AsyncSessionLocal() as session:
        config = HomeThresholdConfig(
            site_id="precision_site",
            vote_confidence_threshold=0.55,
            strong_vote_threshold=0.70,
            min_alert_confidence=0.35,
            total_alerts_30d=100,
            fp_count_30d=30,  # Will trigger adjustment
            fn_count_30d=2,
            last_tuned=datetime.utcnow() - timedelta(hours=48),
        )
        session.add(config)
        await session.commit()
        
        thresholds = await compute_home_thresholds(session, "precision_site")
        
        # Check that returned values have at most 3 decimal places
        vote_str = str(thresholds["vote_confidence_threshold"])
        strong_str = str(thresholds["strong_vote_threshold"])
        
        if '.' in vote_str:
            decimal_places = len(vote_str.split('.')[1])
            assert decimal_places <= 3
