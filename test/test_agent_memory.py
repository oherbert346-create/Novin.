from __future__ import annotations

from datetime import datetime

import pytest
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession

from backend.agent.history import query_history
from backend.agent.memory import update_memory
from backend.models.db import Base, Stream
from backend.models.schemas import (
    AuditTrail,
    EventContext,
    LiabilityDigest,
    MachineRouting,
    OperatorSummary,
    Verdict,
)


@pytest.mark.asyncio
async def test_agent_memory_persists_and_returns_in_history():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with session_factory() as db:
        db.add(Stream(id="cam-1", uri="test://cam", label="Front Door", site_id="home-1", zone="front_door"))
        await db.commit()

        verdict = Verdict(
            frame_id="evt-1",
            event_id="evt-1",
            stream_id="cam-1",
            site_id="home-1",
            timestamp=datetime.utcnow(),
            routing=MachineRouting(
                is_threat=False,
                action="suppress",
                risk_level="low",
                severity="none",
                categories=["person"],
                visibility_policy="timeline",
                notification_policy="none",
                storage_policy="timeline",
            ),
            summary=OperatorSummary(
                headline="Routine visitor at the front door.",
                narrative="Routine activity matched prior context.",
            ),
            audit=AuditTrail(
                liability_digest=LiabilityDigest(
                    decision_reasoning="Benign activity.",
                    confidence_score=0.9,
                ),
                agent_outputs=[],
            ),
            description="person at front door",
            bbox=[],
            b64_thumbnail="",
            event_context=EventContext(
                source="sim_webhook",
                source_event_id="src-1",
                cam_id="cam-1",
                home_id="home-1",
                zone="front_door",
                ingest_mode="webhook",
            ),
        )
        await update_memory(db, verdict)
        await db.commit()

        history = await query_history(
            db=db,
            stream_id="cam-1",
            site_id="home-1",
            event_types=["person"],
        )

    assert history.memory_items
    summaries = [item.summary for item in history.memory_items]
    assert any("Routine visitor at the front door" in summary for summary in summaries)
    assert any(item.scope_type == "site" for item in history.memory_items)
    assert any(item.scope_type == "stream" for item in history.memory_items)
