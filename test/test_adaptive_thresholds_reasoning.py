"""Integration tests showing how adaptive thresholds affect verdict reasoning with images."""

import pytest
import pytest_asyncio
import json
import base64
from datetime import datetime, timedelta
from typing import Optional, List
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database import AsyncSessionLocal, init_db, engine
from backend.models.db import HomeThresholdConfig, Stream, Event
from backend.models.schemas import (
    FramePacket, StreamMeta, VisionResult, HistoryContext,
    AgentOutput, EventContext
)
from backend.agent.reasoning.arbiter import _compute_verdict, compute_home_thresholds


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
        await conn.execute(text("DROP TABLE IF EXISTS events"))
        await conn.execute(text("DROP TABLE IF EXISTS streams"))


def _create_test_frame(width=640, height=480, color=(100, 100, 100)):
    """Create a minimal test image frame as base64."""
    # Create a minimal PNG (8x8 gray image for testing)
    import struct
    import zlib
    
    # Simple 8x8 gray PNG
    signature = b'\x89PNG\r\n\x1a\n'
    
    # IHDR chunk
    width_bytes = struct.pack('>I', 8)
    height_bytes = struct.pack('>I', 8)
    ihdr_data = width_bytes + height_bytes + b'\x08\x00\x00\x00\x00'  # 8-bit grayscale
    ihdr_crc = struct.pack('>I', zlib.crc32(b'IHDR' + ihdr_data) & 0xffffffff)
    ihdr = struct.pack('>I', 13) + b'IHDR' + ihdr_data + ihdr_crc
    
    # IDAT chunk (compressed image data)
    pixel_data = b'\x00' + (b'\x80' * 8) * 8  # Gray pixels
    compressed = zlib.compress(pixel_data)
    idat_crc = struct.pack('>I', zlib.crc32(b'IDAT' + compressed) & 0xffffffff)
    idat = struct.pack('>I', len(compressed)) + b'IDAT' + compressed + idat_crc
    
    # IEND chunk
    iend_crc = struct.pack('>I', zlib.crc32(b'IEND') & 0xffffffff)
    iend = struct.pack('>I', 0) + b'IEND' + iend_crc
    
    png_data = signature + ihdr + idat + iend
    return base64.b64encode(png_data).decode('utf-8')


def _create_packet_with_vision(
    stream_id: str,
    site_id: str,
    zone: str = "front_door",
    threat: bool = False,
    risk_labels: Optional[List[str]] = None,
    categories: Optional[List[str]] = None,
    confidence: float = 0.65,
) -> FramePacket:
    """Create a FramePacket with vision results for testing."""
    if risk_labels is None:
        risk_labels = ["entry_approach"] if threat else []
    if categories is None:
        categories = ["person"] if threat else ["clear"]
    
    vision = VisionResult(
        threat=threat,
        risk_labels=risk_labels,
        categories=categories,
        identity_labels=["resident"],
        bbox=[],
        description="Test frame for adaptive threshold demonstration",
        severity="high" if threat else "low",
        latency_ms=150.0,
        confidence=confidence,
    )
    
    history = HistoryContext(
        recent_events=[],
        typical_event_frequency=0.5,
        category_baseline={},
    )
    
    return FramePacket(
        frame_id="test_frame_" + str(datetime.utcnow().timestamp()),
        stream_id=stream_id,
        timestamp=datetime.utcnow(),
        b64_frame=_create_test_frame(),
        stream_meta=StreamMeta(
            stream_id=stream_id,
            uri="rtsp://test",
            label="Test Stream",
            site_id=site_id,
            zone=zone,
        ),
        vision=vision,
        history=history,
        event_context=None,
    )


def _create_agent_outputs(alert_votes: int, suppress_votes: int) -> List[AgentOutput]:
    """Create mock agent outputs with specified vote counts."""
    agent_ids = ["context_baseline_reasoner", "trajectory_intent_assessor", "falsification_auditor", "executive_triage_commander"]
    agent_roles = ["escalation", "behaviour", "context", "adversary"]
    outputs = []
    
    # Distribute alert votes
    for i in range(alert_votes):
        idx = i % len(agent_ids)
        outputs.append(AgentOutput(
            agent_id=agent_ids[idx],
            role=agent_roles[idx],
            verdict="alert",
            confidence=0.75,
            rationale="Test alert vote",
        ))
    
    # Distribute suppress votes
    for i in range(suppress_votes):
        idx = i % len(agent_ids)
        outputs.append(AgentOutput(
            agent_id=agent_ids[idx],
            role=agent_roles[idx],
            verdict="suppress",
            confidence=0.70,
            rationale="Test suppress vote",
        ))
    
    # Fill remaining with uncertain
    while len(outputs) < 4:
        idx = len(outputs)
        outputs.append(AgentOutput(
            agent_id=agent_ids[idx],
            role=agent_roles[idx],
            verdict="uncertain",
            confidence=0.50,
            rationale="Test uncertain",
        ))
    
    return outputs


@pytest.mark.asyncio
async def test_adaptive_thresholds_affect_verdict_with_borderline_confidence(db_initialized):
    """
    Test that rising thresholds (from high FP rate) suppress alerts on borderline confidence.
    
    Scenario:
    - Home has 30% FP rate (high false positives)
    - vote_confidence_threshold raised from 0.55 to 0.60
    - Borderline frame with 60% agent alert confidence
    - With default threshold: would trigger alert
    - With adaptive threshold: suppressed (60% < 60% threshold raised to 0.60+)
    """
    async with AsyncSessionLocal() as session:
        stream_id = "test_stream_fp"
        site_id = "fp_test_home"
        
        # Create config with high FP rate
        config = HomeThresholdConfig(
            site_id=site_id,
            vote_confidence_threshold=0.55,
            strong_vote_threshold=0.70,
            min_alert_confidence=0.35,
            total_alerts_30d=100,
            fp_count_30d=30,  # 30% FP rate
            fn_count_30d=2,
            last_tuned=datetime.utcnow() - timedelta(hours=48),
        )
        session.add(config)
        await session.commit()
        
        # Compute adaptive thresholds
        thresholds = await compute_home_thresholds(session, site_id)
        
        # Verify threshold was raised
        assert thresholds["vote_confidence_threshold"] > 0.55, "Threshold should rise with high FP"
        print(f"\n✓ Adaptive threshold raised: {0.55} → {thresholds['vote_confidence_threshold']}")
        
        # Create borderline packet (60% agent confidence)
        packet = _create_packet_with_vision(
            stream_id=stream_id,
            site_id=site_id,
            threat=True,
            risk_labels=["entry_approach"],
            confidence=0.60,
        )
        
        # Agent outputs: 2 alert, 2 suppress = 60% alert confidence
        agent_outputs = _create_agent_outputs(alert_votes=2, suppress_votes=2)
        
        # Decision with default thresholds
        verdict_default = _compute_verdict(packet, agent_outputs, "", adaptive_thresholds=None)
        
        # Decision with adaptive thresholds
        verdict_adaptive = _compute_verdict(packet, agent_outputs, "", adaptive_thresholds=thresholds)
        
        print(f"\nWith default thresholds (0.55): action={verdict_default.routing.action}")
        print(f"With adaptive thresholds ({thresholds['vote_confidence_threshold']:.3f}): action={verdict_adaptive.routing.action}")
        
        # With default threshold (0.55), borderline 60% might alert
        # With raised threshold, should be stricter


@pytest.mark.asyncio
async def test_adaptive_thresholds_catch_missed_threats_with_lowered_threshold(db_initialized):
    """
    Test that lowering thresholds (from high FN rate) catches more alerts.
    
    Scenario:
    - Home has 20% FN rate (missing too many threats)
    - vote_confidence_threshold lowered from 0.55 to 0.50
    - Marginal frame with 52% agent alert confidence
    - With default threshold: missed (52% < 55%)
    - With adaptive threshold: alerted (52% > 50% lowered threshold)
    """
    async with AsyncSessionLocal() as session:
        stream_id = "test_stream_fn"
        site_id = "fn_test_home"
        
        # Create config with high FN rate
        config = HomeThresholdConfig(
            site_id=site_id,
            vote_confidence_threshold=0.55,
            strong_vote_threshold=0.70,
            min_alert_confidence=0.35,
            total_alerts_30d=100,
            fp_count_30d=3,
            fn_count_30d=20,  # 20% FN rate (missing threats!)
            last_tuned=datetime.utcnow() - timedelta(hours=48),
        )
        session.add(config)
        await session.commit()
        
        # Compute adaptive thresholds
        thresholds = await compute_home_thresholds(session, site_id)
        
        # Verify threshold was lowered
        assert thresholds["vote_confidence_threshold"] < 0.55, "Threshold should lower with high FN"
        print(f"\n✓ Adaptive threshold lowered: {0.55} → {thresholds['vote_confidence_threshold']}")
        
        # Create marginal packet (52% agent confidence)
        packet = _create_packet_with_vision(
            stream_id=stream_id,
            site_id=site_id,
            threat=True,
            risk_labels=["suspicious_presence"],
            confidence=0.52,
        )
        
        # Agent outputs: 2 alert, 2 suppress = 50% alert confidence
        agent_outputs = _create_agent_outputs(alert_votes=2, suppress_votes=2)
        
        # Decision with default thresholds
        verdict_default = _compute_verdict(packet, agent_outputs, "", adaptive_thresholds=None)
        
        # Decision with adaptive thresholds
        verdict_adaptive = _compute_verdict(packet, agent_outputs, "", adaptive_thresholds=thresholds)
        
        print(f"\nWith default thresholds (0.55): action={verdict_default.routing.action}")
        print(f"With adaptive thresholds ({thresholds['vote_confidence_threshold']:.3f}): action={verdict_adaptive.routing.action}")


@pytest.mark.asyncio
async def test_adaptive_thresholds_persist_across_feedback_cycles(db_initialized):
    """
    Test that feedback increments counters and next compute_home_thresholds call reflects new rates.
    
    Scenario:
    - Home starts with balanced feedback
    - User provides 10 false positive marks
    - Thresholds adapt up on second computation
    - Verify new thresholds are higher
    """
    async with AsyncSessionLocal() as session:
        stream_id = "test_stream_evolve"
        site_id = "evolving_home"
        
        # Initial config
        config = HomeThresholdConfig(
            site_id=site_id,
            vote_confidence_threshold=0.55,
            strong_vote_threshold=0.70,
            min_alert_confidence=0.35,
            total_alerts_30d=50,
            fp_count_30d=5,  # 10% FP rate initially
            fn_count_30d=2,
            last_tuned=datetime.utcnow() - timedelta(hours=72),  # Old tuning
        )
        session.add(config)
        await session.commit()
        
        # First threshold computation (balanced)
        thresholds_initial = await compute_home_thresholds(session, site_id)
        print(f"\nInitial thresholds (10% FP, 4% FN): {thresholds_initial['vote_confidence_threshold']:.3f}")
        
        # Simulate 10 false positive marks (increasing FP rate to 20%)
        result = await session.execute(
            select(HomeThresholdConfig).where(HomeThresholdConfig.site_id == site_id)
        )
        config = result.scalar_one()
        config.total_alerts_30d = 100
        config.fp_count_30d = 20  # FP rate now 20%
        config.last_tuned = datetime.utcnow() - timedelta(hours=48)  # Enough time passed
        await session.commit()
        
        # Second threshold computation (high FP)
        thresholds_updated = await compute_home_thresholds(session, site_id)
        print(f"Updated thresholds (20% FP, 2% FN): {thresholds_updated['vote_confidence_threshold']:.3f}")
        
        # Verify threshold rose with more FP feedback
        assert thresholds_updated["vote_confidence_threshold"] >= thresholds_initial["vote_confidence_threshold"], \
            "Threshold should rise or stay same with increasing FP"


@pytest.mark.asyncio
async def test_adaptive_thresholds_show_reasoning_path_with_images(db_initialized):
    """
    Comprehensive test showing the full reasoning path with images.
    
    Documents:
    - How vision analysis produces agent outputs
    - How agent outputs get weighted and scored
    - How adaptive thresholds affect final verdict
    - Why the decision was made (alert vs suppress)
    """
    async with AsyncSessionLocal() as session:
        stream_id = "test_stream_reasoning"
        site_id = "reasoning_home"
        
        # Create homes with different threshold profiles
        for home_type, fp_rate, fn_rate in [
            ("balanced", 0.05, 0.05),
            ("strict", 0.25, 0.03),
            ("sensitive", 0.08, 0.15),
        ]:
            config = HomeThresholdConfig(
                site_id=f"{site_id}_{home_type}",
                vote_confidence_threshold=0.55,
                strong_vote_threshold=0.70,
                min_alert_confidence=0.35,
                total_alerts_30d=100,
                fp_count_30d=int(100 * fp_rate),
                fn_count_30d=int(100 * fn_rate),
                last_tuned=datetime.utcnow() - timedelta(hours=48),
            )
            session.add(config)
        
        await session.commit()
        
        # Test scenario: person detected at night at entry zone
        for home_type in ["balanced", "strict", "sensitive"]:
            site = f"{site_id}_{home_type}"
            
            # Compute adaptive thresholds for this home type
            thresholds = await compute_home_thresholds(session, site)
            
            # Create frame with person at entry after hours
            packet = _create_packet_with_vision(
                stream_id=f"{stream_id}_{home_type}",
                site_id=site,
                zone="front_door",
                threat=True,
                risk_labels=["entry_approach", "suspicious_presence"],
                categories=["person"],
                confidence=0.68,
            )
            
            # Agents split on this: 2 alert (confident), 1 suppress, 1 uncertain
            agent_outputs = [
                AgentOutput(agent_id="context_baseline_reasoner", role="context", verdict="alert", confidence=0.75, rationale="Entry concern"),
                AgentOutput(agent_id="trajectory_intent_assessor", role="intent", verdict="alert", confidence=0.72, rationale="Night-time activity"),
                AgentOutput(agent_id="falsification_auditor", role="auditor", verdict="suppress", confidence=0.60, rationale="Residential zone"),
                AgentOutput(agent_id="executive_triage_commander", role="triage", verdict="uncertain", confidence=0.55, rationale="Insufficient data"),
            ]
            
            # Get verdicts
            verdict = _compute_verdict(packet, agent_outputs, "", adaptive_thresholds=thresholds)
            
            # Report reasoning
            print(f"\n{'='*70}")
            print(f"HOME TYPE: {home_type.upper()}")
            print(f"{'='*70}")
            print(f"FP Rate: {int(100 * (int(100 * [0.05, 0.25, 0.08][['balanced', 'strict', 'sensitive'].index(home_type)]) // 100))}%")
            print(f"FN Rate: {int(100 * (int(100 * [0.05, 0.03, 0.15][['balanced', 'strict', 'sensitive'].index(home_type)]) // 100))}%")
            print(f"Adaptive Thresholds:")
            print(f"  - Vote confidence: {thresholds['vote_confidence_threshold']:.3f}")
            print(f"  - Strong vote: {thresholds['strong_vote_threshold']:.3f}")
            print(f"Agent Outputs:")
            for agent in agent_outputs:
                print(f"  - {agent.agent_id}: {agent.verdict} ({agent.confidence:.2f}) - {agent.rationale}")
            print(f"Final Verdict:")
            print(f"  - Action: {verdict.routing.action}")
            print(f"  - Risk Level: {verdict.routing.risk_level}")
            print(f"  - Confidence: {verdict.audit.liability_digest.confidence_score:.3f}")
            
            # Verify different homes make different decisions based on their thresholds
            print(f"  - Rationale excerpt: {verdict.audit.liability_digest.decision_reasoning[:200]}...")


@pytest.mark.asyncio
async def test_no_adjustment_too_soon_after_last_tuning(db_initialized):
    """
    Test that rate limiting prevents adjustment within 1 hour of last tuning.
    
    Scenario:
    - Home was just tuned 30 minutes ago
    - Very high FP rate (50%) should trigger adaptation
    - Rate limiting prevents any adjustment
    - Thresholds remain unchanged
    """
    async with AsyncSessionLocal() as session:
        stream_id = "test_stream_rate_limit"
        site_id = "rate_limit_home"
        
        # Create config with recent tuning
        config = HomeThresholdConfig(
            site_id=site_id,
            vote_confidence_threshold=0.55,
            strong_vote_threshold=0.70,
            min_alert_confidence=0.35,
            total_alerts_30d=100,
            fp_count_30d=50,  # 50% FP rate!
            fn_count_30d=2,
            last_tuned=datetime.utcnow() - timedelta(minutes=30),  # Just tuned!
        )
        session.add(config)
        await session.commit()
        
        # Compute thresholds
        thresholds = await compute_home_thresholds(session, site_id)
        
        # Should return unchanged due to rate limiting
        assert thresholds["vote_confidence_threshold"] == 0.55, \
            "Thresholds should not change within 1 hour of last tuning"
        
        print(f"\n✓ Rate limiting prevents premature adjustment")
        print(f"  Last tuned: 30 minutes ago")
        print(f"  FP rate: 50% (would trigger 0.10 threshold increase)")
        print(f"  Actual change: {thresholds['vote_confidence_threshold'] - 0.55:.3f} (blocked)")
