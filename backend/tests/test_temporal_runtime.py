from __future__ import annotations

from datetime import datetime
import uuid
from unittest.mock import AsyncMock, patch

import pytest

from backend.database import AsyncSessionLocal, init_db
from backend.hub import _persist_verdict
from backend.models.db import Event, Stream
from backend.models.schemas import (
    AgentOutput,
    AuditTrail,
    EventContext,
    LiabilityDigest,
    MachineRouting,
    OperatorSummary,
    Verdict,
)


def _make_verdict(*, event_id: str, stream_id: str, cam_id: str, zone: str = "front_door") -> Verdict:
    return Verdict(
        frame_id="frame-1",
        event_id=event_id,
        stream_id=stream_id,
        site_id="home",
        timestamp=datetime.utcnow(),
        routing=MachineRouting(
            is_threat=False,
            action="suppress",
            risk_level="low",
            severity="low",
            categories=["package"],
        ),
        summary=OperatorSummary(headline="Package seen", narrative="Benign delivery"),
        audit=AuditTrail(
            liability_digest=LiabilityDigest(
                decision_reasoning="delivery pattern",
                confidence_score=0.8,
            ),
            agent_outputs=[
                AgentOutput(
                    agent_id="trajectory_intent_assessor",
                    role="Behavioural Pattern",
                    verdict="suppress",
                    risk_level="low",
                    confidence=0.8,
                    rationale="delivery",
                )
            ],
        ),
        description="package at front door",
        event_context=EventContext(zone=zone, home_id="home", cam_id=cam_id),
    )


@pytest.mark.asyncio
async def test_persist_verdict_resolves_stream_by_db_id_and_refreshes_schedule():
    await init_db()
    event_id = f"event-{uuid.uuid4()}"
    stream = Stream(
        uri=f"rtsp://cam-front/{uuid.uuid4()}",
        label="Front Door",
        site_id="home",
        zone="front_door",
    )

    async with AsyncSessionLocal() as db:
        db.add(stream)
        await db.commit()
        await db.refresh(stream)
        verdict = _make_verdict(event_id=event_id, stream_id=stream.id, cam_id="cam-front")
        with patch("backend.agent.memory.update_memory", AsyncMock()), patch(
            "backend.agent.schedule.ScheduleLearner.refresh_schedule_if_due",
            AsyncMock(),
        ) as mock_refresh:
            await _persist_verdict(db, verdict)
            mock_refresh.assert_awaited_once()

    async with AsyncSessionLocal() as db:
        row = await db.get(Event, event_id)
        assert row is not None
        assert row.stream_id == stream.id
        assert row.zone == "front_door"


@pytest.mark.asyncio
async def test_persist_verdict_supports_legacy_uri_stream_identifier():
    await init_db()
    event_id = f"event-{uuid.uuid4()}"
    stream_uri = f"cam-front-{uuid.uuid4()}"

    async with AsyncSessionLocal() as db:
        stream = Stream(
            uri=stream_uri,
            label="Front Door Legacy",
            site_id="home",
            zone="front_door",
        )
        db.add(stream)
        await db.commit()
        await db.refresh(stream)
        verdict = _make_verdict(event_id=event_id, stream_id=stream_uri, cam_id=stream_uri)
        with patch("backend.agent.memory.update_memory", AsyncMock()), patch(
            "backend.agent.schedule.ScheduleLearner.refresh_schedule_if_due",
            AsyncMock(),
        ):
            await _persist_verdict(db, verdict)

    async with AsyncSessionLocal() as db:
        row = await db.get(Event, event_id)
        assert row is not None
        assert row.stream_id == stream.id


@pytest.mark.asyncio
async def test_persist_verdict_auto_creates_stream_for_unknown_identifier():
    await init_db()
    event_id = f"event-{uuid.uuid4()}"
    missing_stream_id = f"missing-stream-{uuid.uuid4()}"
    verdict = _make_verdict(
        event_id=event_id,
        stream_id=missing_stream_id,
        cam_id="cam-missing",
        zone="backyard",
    )

    async with AsyncSessionLocal() as db:
        with patch("backend.agent.memory.update_memory", AsyncMock()), patch(
            "backend.agent.schedule.ScheduleLearner.refresh_schedule_if_due",
            AsyncMock(),
        ):
            await _persist_verdict(db, verdict)

    # Verify the stream was auto-created with correct fields
    async with AsyncSessionLocal() as db:
        from sqlalchemy import select

        result = await db.execute(
            select(Stream).where(Stream.uri == missing_stream_id)
        )
        stream = result.scalar_one_or_none()
        assert stream is not None, "Stream should have been auto-created"
        assert stream.site_id == "home"
        assert stream.zone == "backyard"
        assert stream.label == f"Auto-created: {missing_stream_id}"

        # Verify the event was persisted referencing the auto-created stream
        row = await db.get(Event, event_id)
        assert row is not None
        assert row.stream_id == stream.id
