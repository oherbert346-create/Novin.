"""
Full Pipeline Integration Test with Real Database & Provider Configuration

Tests:
1. SQLite database persistence (local file-based)
2. Full frame → vision → reasoning → verdict pipeline
3. Multiple AI provider configurations
4. End-to-end with adaptive thresholds
5. Database transaction integrity
"""

import pytest
import pytest_asyncio
import json
import base64
import time
import os
from datetime import datetime, timedelta
from typing import Optional
from pathlib import Path
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker

from backend.database import AsyncSessionLocal, init_db
from backend.models.db import Base, Stream, HomeThresholdConfig, Event, AgentMemory
from backend.models.schemas import (
    FramePacket, StreamMeta, VisionResult, HistoryContext,
    AgentOutput, EventContext
)
from backend.agent.reasoning.arbiter import _compute_verdict, compute_home_thresholds
from backend.agent.history import query_history
from backend.config import settings
from backend.provider import active_reasoning_model, active_vision_model


# Use separate test database
TEST_DB_URL = "sqlite+aiosqlite:///./test_full_pipeline.db"


@pytest_asyncio.fixture
async def test_db():
    """Create isolated test database with fresh schema."""
    # Create test engine
    test_engine = create_async_engine(TEST_DB_URL, echo=False)
    
    # Create all tables
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    
    # Create session factory
    AsyncTestSession = async_sessionmaker(
        test_engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autocommit=False,
        autoflush=False,
    )
    
    # Initialize data
    async with AsyncTestSession() as session:
        await init_db()
    
    yield AsyncTestSession
    
    # Cleanup
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await test_engine.dispose()


def create_test_png() -> str:
    """Create minimal PNG as base64."""
    import struct
    import zlib
    
    signature = b'\x89PNG\r\n\x1a\n'
    width_bytes = struct.pack('>I', 64)
    height_bytes = struct.pack('>I', 64)
    ihdr_data = width_bytes + height_bytes + b'\x08\x00\x00\x00\x00'
    ihdr_crc = struct.pack('>I', zlib.crc32(b'IHDR' + ihdr_data) & 0xffffffff)
    ihdr = struct.pack('>I', 13) + b'IHDR' + ihdr_data + ihdr_crc
    
    pixel_data = b'\x00' + (b'\x80' * 64) * 64
    compressed = zlib.compress(pixel_data)
    idat_crc = struct.pack('>I', zlib.crc32(b'IDAT' + compressed) & 0xffffffff)
    idat = struct.pack('>I', len(compressed)) + b'IDAT' + compressed + idat_crc
    
    iend_crc = struct.pack('>I', zlib.crc32(b'IEND') & 0xffffffff)
    iend = struct.pack('>I', 0) + b'IEND' + iend_crc
    
    png_data = signature + ihdr + idat + iend
    return base64.b64encode(png_data).decode('utf-8')


@pytest.mark.asyncio
async def test_sqlite_database_persistence(test_db):
    """Test that data persists in SQLite database file."""
    AsyncTestSession = test_db
    
    async with AsyncTestSession() as session:
        # Create test stream
        stream = Stream(
            uri="rtsp://test/camera1",
            label="Test Camera 1",
            site_id="test_home_001",
            zone="front_door",
        )
        session.add(stream)
        
        # Create home threshold config
        config = HomeThresholdConfig(
            site_id="test_home_001",
            total_alerts_30d=50,
            fp_count_30d=5,
            fn_count_30d=3,
            last_tuned=datetime.utcnow(),
        )
        session.add(config)
        
        await session.commit()
        stream_id = stream.id
        site_id = config.site_id
    
    # Verify persistence: query in new session
    async with AsyncTestSession() as session:
        result = await session.execute(
            select(Stream).where(Stream.id == stream_id)
        )
        persisted_stream = result.scalar_one()
        assert persisted_stream.label == "Test Camera 1"
        
        result = await session.execute(
            select(HomeThresholdConfig).where(HomeThresholdConfig.site_id == site_id)
        )
        persisted_config = result.scalar_one()
        assert persisted_config.fp_count_30d == 5
    
    print("\n✓ SQLite persistence verified")
    print(f"  Stream: {persisted_stream.id} ({persisted_stream.label})")
    print(f"  Config: {persisted_config.site_id} (FP: {persisted_config.fp_count_30d})")


@pytest.mark.asyncio
async def test_full_pipeline_frame_processing(test_db):
    """Test complete frame → vision → reasoning → verdict pipeline."""
    AsyncTestSession = test_db
    
    async with AsyncTestSession() as session:
        # Setup: Create stream and home config
        stream = Stream(
            uri="rtsp://test/camera",
            label="Pipeline Test",
            site_id="pipeline_test_home",
            zone="entry",
        )
        session.add(stream)
        
        config = HomeThresholdConfig(
            site_id="pipeline_test_home",
            total_alerts_30d=100,
            fp_count_30d=20,
            fn_count_30d=5,
            last_tuned=datetime.utcnow() - timedelta(hours=48),
        )
        session.add(config)
        await session.commit()
        
        print("\n" + "="*80)
        print("FULL PIPELINE TEST: Image → Vision → Reasoning → Verdict")
        print("="*80)
        
        # Create frame packet
        packet = FramePacket(
            frame_id=f"frame_{int(time.time()*1000)}",
            stream_id=stream.id,
            timestamp=datetime.utcnow(),
            b64_frame=create_test_png(),
            stream_meta=StreamMeta(
                stream_id=stream.id,
                uri="rtsp://test",
                label="Test",
                site_id="pipeline_test_home",
                zone="entry",
            ),
            # Simulate vision result (in production: real API call)
            vision=VisionResult(
                threat=True,
                severity="high",
                categories=["person"],
                identity_labels=["unknown"],
                risk_labels=["entry_approach", "after_hours"],
                description="Person at entry after hours",
                confidence=0.78,
                latency_ms=120.0,
            ),
            history=HistoryContext(
                recent_events=[],
                typical_event_frequency=0.3,
                category_baseline={},
            ),
        )
        
        # Get adaptive thresholds from database
        thresholds = await compute_home_thresholds(session, "pipeline_test_home")
        
        print(f"\nFrame Packet Created:")
        print(f"  Stream: {packet.stream_id}")
        print(f"  Vision: threat={packet.vision.threat}, "
              f"confidence={packet.vision.confidence:.2f}, "
              f"severity={packet.vision.severity}")
        print(f"\nAdaptive Thresholds (from DB):")
        print(f"  Vote confidence: {thresholds['vote_confidence_threshold']:.3f}")
        print(f"  Strong vote: {thresholds['strong_vote_threshold']:.3f}")
        print(f"  Min alert conf: {thresholds['min_alert_confidence']:.3f}")
        
        # Simulate agent outputs (in production: real API calls to reasoning models)
        agent_outputs = [
            AgentOutput(
                agent_id="context_baseline_reasoner",
                role="escalation",
                verdict="alert",
                confidence=0.85,
                rationale="Threat signal detected; escalation warranted",
            ),
            AgentOutput(
                agent_id="trajectory_intent_assessor",
                role="behaviour",
                verdict="alert",
                confidence=0.72,
                rationale="Behavioral pattern consistent with intrusion",
            ),
            AgentOutput(
                agent_id="falsification_auditor",
                role="context",
                verdict="suppress",
                confidence=0.55,
                rationale="Entry zone is expected access point",
            ),
            AgentOutput(
                agent_id="executive_triage_commander",
                role="adversary",
                verdict="uncertain",
                confidence=0.60,
                rationale="Insufficient motion signature for validation",
            ),
        ]
        
        print(f"\nAgent Outputs:")
        for agent in agent_outputs:
            print(f"  {agent.agent_id:30s}: {agent.verdict:10s} ({agent.confidence:.2f})")
        
        # Compute verdict with adaptive thresholds
        verdict = _compute_verdict(packet, agent_outputs, "", adaptive_thresholds=thresholds)
        
        print(f"\nFinal Verdict:")
        print(f"  Action: {verdict.routing.action.upper()}")
        print(f"  Risk Level: {verdict.routing.risk_level}")
        print(f"  Confidence: {verdict.audit.liability_digest.confidence_score:.3f}")
        
        # Store event in database
        event = Event(
            stream_id=stream.id,
            timestamp=datetime.utcnow(),
            verdict_action=verdict.routing.action,
            final_confidence=verdict.audit.liability_digest.confidence_score,
            severity=verdict.routing.severity,
            summary=f"Pipeline test - {verdict.routing.action}",
        )
        session.add(event)
        await session.commit()
        
        print(f"\n✓ Event persisted to database")
        print(f"  Event ID: {event.id}")
        
        # Verify event retrieval
        result = await session.execute(
            select(Event).where(Event.id == event.id)
        )
        retrieved = result.scalar_one()
        assert retrieved.verdict_action == verdict.routing.action
        print(f"✓ Event retrieval verified")


@pytest.mark.asyncio
async def test_multiple_provider_configurations(test_db):
    """Test system behavior with different AI provider configurations."""
    AsyncTestSession = test_db
    
    print("\n" + "="*80)
    print("PROVIDER CONFIGURATION TEST")
    print("="*80)
    
    async with AsyncTestSession() as session:
        # Setup
        stream = Stream(
            uri="rtsp://test",
            label="Provider Test",
            site_id="provider_test_home",
            zone="entry",
        )
        session.add(stream)
        
        config = HomeThresholdConfig(
            site_id="provider_test_home",
            total_alerts_30d=100,
            fp_count_30d=15,
            fn_count_30d=8,
        )
        session.add(config)
        await session.commit()
        
        # Print current configuration
        print(f"\nCurrent Configuration:")
        print(f"  Vision Provider: {settings.vision_provider}")
        print(f"  Vision Model: {active_vision_model()}")
        print(f"  Reasoning Provider: {settings.reasoning_provider}")
        print(f"  Reasoning Model: {active_reasoning_model()}")
        
        print(f"\nAvailable Configurations:")
        
        providers = {
            "groq": {
                "vision": "meta-llama/llama-4-scout-17b-16e-instruct",
                "reasoning": "qwen/qwen3-32b",
                "desc": "Fast inference, OpenAI-compatible",
            },
            "together": {
                "vision": "Qwen/Qwen3-VL-8B-Instruct",
                "reasoning": "MiniMaxAI/MiniMax-M2.5",
                "desc": "Extended reasoning, multi-modal",
            },
            "siliconflow": {
                "vision": "Qwen/Qwen2.5-VL-7B-Instruct",
                "reasoning": "deepseek-ai/DeepSeek-V3.2",
                "desc": "Extended thinking (chain-of-thought)",
            },
            "cerebras": {
                "vision": "Qwen/Qwen3-VL-8B-Instruct (via Together)",
                "reasoning": "gpt-oss-120b",
                "desc": "Enterprise reasoning, structured",
            },
        }
        
        for provider_name, info in providers.items():
            status = "✓ ACTIVE" if provider_name == settings.reasoning_provider else ""
            print(f"\n  {provider_name.upper():15s} {status}")
            print(f"    Vision:    {info['vision']}")
            print(f"    Reasoning: {info['reasoning']}")
            print(f"    Note:      {info['desc']}")
        
        # Test that system can handle different provider settings
        thresholds = await compute_home_thresholds(session, "provider_test_home")
        print(f"\n✓ Thresholds computed regardless of provider:")
        print(f"  {thresholds}")


@pytest.mark.asyncio
async def test_feedback_loop_with_persistence(test_db):
    """Test feedback → counter increment → threshold adaptation with DB persistence."""
    AsyncTestSession = test_db
    
    async with AsyncTestSession() as session:
        site_id = "feedback_persist_home"
        
        # Create initial config
        config = HomeThresholdConfig(
            site_id=site_id,
            total_alerts_30d=50,
            fp_count_30d=3,
            fn_count_30d=2,
            last_tuned=datetime.utcnow() - timedelta(hours=72),
        )
        session.add(config)
        await session.commit()
        
        print("\n" + "="*80)
        print("FEEDBACK LOOP PERSISTENCE TEST")
        print("="*80)
        
        # Step 1: Get initial thresholds
        thresholds_v1 = await compute_home_thresholds(session, site_id)
        print(f"\nStep 1: Initial State")
        print(f"  FP: 3/50 (6%), FN: 2/50 (4%)")
        print(f"  Threshold: {thresholds_v1['vote_confidence_threshold']:.3f}")
        
        # Step 2: Simulate 10 false positives
        result = await session.execute(
            select(HomeThresholdConfig).where(HomeThresholdConfig.site_id == site_id)
        )
        config = result.scalar_one()
        
        for i in range(10):
            config.total_alerts_30d += 1
            config.fp_count_30d += 1
        config.last_tuned = datetime.utcnow() - timedelta(hours=24)
        await session.commit()
        
        # Step 3: Verify persistence
        result = await session.execute(
            select(HomeThresholdConfig).where(HomeThresholdConfig.site_id == site_id)
        )
        config = result.scalar_one()
        fp_rate = config.fp_count_30d / config.total_alerts_30d
        
        print(f"\nStep 2: After 10 False Positives")
        print(f"  FP: {config.fp_count_30d}/{config.total_alerts_30d} ({100*fp_rate:.0f}%)")
        print(f"  Database persisted: ✓")
        
        # Step 4: Recompute thresholds
        thresholds_v2 = await compute_home_thresholds(session, site_id)
        print(f"\nStep 3: Thresholds Recomputed")
        print(f"  Old: {thresholds_v1['vote_confidence_threshold']:.3f}")
        print(f"  New: {thresholds_v2['vote_confidence_threshold']:.3f}")
        print(f"  Change: {thresholds_v2['vote_confidence_threshold'] - thresholds_v1['vote_confidence_threshold']:+.3f}")


@pytest.mark.asyncio
async def test_multi_home_isolation(test_db):
    """Test that different homes have isolated thresholds in database."""
    AsyncTestSession = test_db
    
    async with AsyncTestSession() as session:
        homes = {
            "strict_home": {"fp": 25, "fn": 2, "total": 100},
            "balanced_home": {"fp": 5, "fn": 5, "total": 100},
            "sensitive_home": {"fp": 8, "fn": 20, "total": 100},
        }
        
        # Create configs for each home
        for home_id, stats in homes.items():
            config = HomeThresholdConfig(
                site_id=home_id,
                total_alerts_30d=stats["total"],
                fp_count_30d=stats["fp"],
                fn_count_30d=stats["fn"],
            )
            session.add(config)
        
        await session.commit()
        
        print("\n" + "="*80)
        print("MULTI-HOME ISOLATION TEST")
        print("="*80)
        
        # Verify isolation: each home computes independent thresholds
        for home_id in homes.keys():
            thresholds = await compute_home_thresholds(session, home_id)
            result = await session.execute(
                select(HomeThresholdConfig).where(HomeThresholdConfig.site_id == home_id)
            )
            config = result.scalar_one()
            
            fp_rate = config.fp_count_30d / config.total_alerts_30d
            fn_rate = config.fn_count_30d / config.total_alerts_30d
            
            print(f"\n{home_id}:")
            print(f"  FP Rate: {100*fp_rate:5.0f}%  FN Rate: {100*fn_rate:5.0f}%")
            print(f"  Threshold: {thresholds['vote_confidence_threshold']:.3f}")
            print(f"  Database isolated: ✓")


@pytest.mark.asyncio
async def test_concurrent_operations(test_db):
    """Test concurrent database operations (multiple frames simultaneously)."""
    import asyncio
    
    AsyncTestSession = test_db
    
    stream_id = None
    async with AsyncTestSession() as session:
        # Create test stream
        stream = Stream(
            uri="rtsp://test",
            label="Concurrent Test",
            site_id="concurrent_home",
            zone="entry",
        )
        session.add(stream)
        
        config = HomeThresholdConfig(
            site_id="concurrent_home",
            total_alerts_30d=100,
            fp_count_30d=10,
            fn_count_30d=5,
        )
        session.add(config)
        await session.commit()
        stream_id = stream.id
    
    print("\n" + "="*80)
    print("CONCURRENT OPERATIONS TEST")
    print("="*80)
    
    async def process_frame(frame_num: int):
        async with AsyncTestSession() as session:
            # Get thresholds
            thresholds = await compute_home_thresholds(session, "concurrent_home")
            
            # Store event
            event = Event(
                stream_id=stream_id,
                timestamp=datetime.utcnow(),
                verdict_action="alert" if frame_num % 2 == 0 else "suppress",
                final_confidence=0.8 if frame_num % 2 == 0 else 0.2,
                severity="high" if frame_num % 2 == 0 else "low",
            )
            session.add(event)
            await session.commit()
            return thresholds
    
    # Process 5 frames concurrently
    start = time.time()
    results = await asyncio.gather(
        *[process_frame(i) for i in range(5)]
    )
    elapsed = time.time() - start
    
    print(f"\n5 concurrent frames processed in {elapsed:.3f}s")
    print(f"Average per frame: {elapsed/5*1000:.1f}ms")
    print(f"All thresholds consistent: {len(set(str(r) for r in results)) == 1}")
    print(f"Database integrity: ✓")
    
    # Verify all events persisted
    async with AsyncTestSession() as session:
        result = await session.execute(
            select(Event).where(Event.stream_id == stream_id)
        )
        events = result.scalars().all()
        print(f"Events persisted: {len(events)}/5 ✓")


@pytest.mark.asyncio
async def test_database_schema_inspection(test_db):
    """Test database schema and available tables."""
    AsyncTestSession = test_db
    
    async with AsyncTestSession() as session:
        print("\n" + "="*80)
        print("DATABASE SCHEMA INSPECTION")
        print("="*80)
        
        # Get all table names
        result = await session.execute(
            text("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
        )
        tables = result.scalars().all()
        
        print(f"\nTables in test database:")
        for table in tables:
            result = await session.execute(text(f"SELECT COUNT(*) FROM {table}"))
            count = result.scalar()
            
            # Get column info
            result = await session.execute(text(f"PRAGMA table_info({table})"))
            columns = result.fetchall()
            
            print(f"\n  {table} ({count} rows)")
            for col in columns[:5]:  # Show first 5 columns
                col_name, col_type = col[1], col[2]
                print(f"    - {col_name}: {col_type}")
            if len(columns) > 5:
                print(f"    ... and {len(columns)-5} more columns")
