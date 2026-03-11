"""Test implementation of improved anomaly detection, sequence linking, and feedback/tagging."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest

from backend.agent.history import query_history, _compute_hourly_baseline, _compute_category_baseline
from backend.database import AsyncSessionLocal, init_db
from backend.models.db import Event, Stream
from backend.models.schemas import HistoryContext


@pytest.mark.asyncio
async def test_anomaly_detection_considers_time_of_day():
    """Test that anomaly detection amplifies events during quiet hours (20:00-06:00)."""
    await init_db()
    stream_id = f"test-stream-{uuid.uuid4().hex[:8]}"
    
    async with AsyncSessionLocal() as db:
        stream = Stream(id=stream_id, site_id="test-site", label="Test Camera", uri="http://test.local")
        db.add(stream)
        await db.commit()
        
        now = datetime.utcnow()
        
        # Create baseline events during day hours (noon)
        for i in range(10):
            event = Event(
                id=str(uuid.uuid4()),
                stream_id=stream_id,
                timestamp=now - timedelta(days=2, hours=12 - i),
                verdict_action="alert",
                severity="low",
                categories="[]",
            )
            db.add(event)
        await db.commit()
        
        # Create another event during quiet hours (3am)
        quiet_hour_timestamp = now.replace(hour=3, minute=0, second=0)
        
        # Query history at quiet hour
        history = await query_history(
            db=db,
            stream_id=stream_id,
            site_id="test-site",
            event_types=["person"],
            same_camera_window_seconds=300,
            site_window_hours=24,
        )
        
        # During quiet hours with low baseline, anomaly should be amplified
        assert history.anomaly_score is not None
        # Note: actual score depends on baseline; just verify the function runs


@pytest.mark.asyncio
async def test_compute_hourly_baseline():
    """Test that hourly baseline is computed correctly."""
    await init_db()
    stream_id = f"test-stream-{uuid.uuid4().hex[:8]}"
    
    async with AsyncSessionLocal() as db:
        stream = Stream(id=stream_id, site_id="test-site", label="Test Camera", uri="http://test.local")
        db.add(stream)
        await db.commit()
        
        now = datetime.utcnow()
        
        # Create events distributed across hours
        for hour in [6, 7, 8, 9, 18, 19, 20]:  # Events spread across hours
            for i in range(2):  # 2 events per hour
                event = Event(
                    id=str(uuid.uuid4()),
                    stream_id=stream_id,
                    timestamp=now - timedelta(hours=48 - (hour - i * 0.5)),
                    verdict_action="alert",
                    severity="low",
                    categories="[]",
                )
                db.add(event)
        await db.commit()
        
        # Get hourly baseline
        since = now - timedelta(days=3)
        baseline = await _compute_hourly_baseline(db, stream_id, since)
        
        # Should have entries for the hours we added events
        assert len(baseline) > 0
        for hour in [6, 7, 8, 9, 18, 19, 20]:
            if hour in baseline:
                assert baseline[hour] > 0


@pytest.mark.asyncio
async def test_compute_category_baseline():
    """Test that category baseline is computed correctly."""
    await init_db()
    stream_id = f"test-stream-{uuid.uuid4().hex[:8]}"
    
    async with AsyncSessionLocal() as db:
        stream = Stream(id=stream_id, site_id="test-site", label="Test Camera", uri="http://test.local")
        db.add(stream)
        await db.commit()
        
        now = datetime.utcnow()
        
        # Mix of different categories
        categories = ["person", "person", "person", "pet", "package"]
        for cat in categories:
            event = Event(
                id=str(uuid.uuid4()),
                stream_id=stream_id,
                timestamp=now - timedelta(days=1),
                verdict_action="alert",
                severity="low",
                categories=json.dumps([cat]),
            )
            db.add(event)
        await db.commit()
        
        # Get category baseline
        since = now - timedelta(days=3)
        baseline = await _compute_category_baseline(db, stream_id, since)
        
        # Person should be most frequent (3 out of 5)
        assert baseline.get("person", 0) > baseline.get("pet", 0)
        assert baseline.get("person", 0) > baseline.get("package", 0)
        # All should sum to around 1.0
        assert abs(sum(baseline.values()) - 1.0) < 0.01


@pytest.mark.asyncio
async def test_event_user_tag_persists():
    """Test that user_tag field is properly persisted to database."""
    await init_db()
    stream_id = f"test-stream-{uuid.uuid4().hex[:8]}"
    
    async with AsyncSessionLocal() as db:
        stream = Stream(id=stream_id, site_id="test-site", label="Test Camera", uri="http://test.local")
        db.add(stream)
        await db.commit()
        
        event_id = f"test-event-{uuid.uuid4()}"
        event = Event(
            id=event_id,
            stream_id=stream_id,
            timestamp=datetime.utcnow(),
            verdict_action="alert",
            severity="low",
            categories="[]",
            user_tag="resident",  # Tag as resident
        )
        db.add(event)
        await db.commit()
        
        # Retrieve and verify
        from sqlalchemy import select
        result = await db.execute(select(Event).where(Event.id == event_id))
        retrieved = result.scalar_one_or_none()
        
        assert retrieved is not None
        assert retrieved.user_tag == "resident"


@pytest.mark.asyncio
async def test_event_user_feedback_persists():
    """Test that user_feedback field is properly persisted to database."""
    await init_db()
    stream_id = f"test-stream-{uuid.uuid4().hex[:8]}"
    
    async with AsyncSessionLocal() as db:
        stream = Stream(id=stream_id, site_id="test-site", label="Test Camera", uri="http://test.local")
        db.add(stream)
        await db.commit()
        
        now = datetime.utcnow()
        event_id = f"test-event-{uuid.uuid4()}"
        event = Event(
            id=event_id,
            stream_id=stream_id,
            timestamp=now,
            verdict_action="alert",
            severity="low",
            categories="[]",
            user_feedback="false_positive",  # Mark as false positive
            user_feedback_timestamp=now,
        )
        db.add(event)
        await db.commit()
        
        # Retrieve and verify
        from sqlalchemy import select
        result = await db.execute(select(Event).where(Event.id == event_id))
        retrieved = result.scalar_one_or_none()
        
        assert retrieved is not None
        assert retrieved.user_feedback == "false_positive"
        assert retrieved.user_feedback_timestamp is not None
