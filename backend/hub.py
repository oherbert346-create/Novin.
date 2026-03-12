from __future__ import annotations

import json
import logging
import uuid
from typing import Callable

from fastapi import WebSocket
from groq import AsyncGroq
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import settings
from backend.models.schemas import StreamMeta, Verdict
from backend.public import public_verdict

logger = logging.getLogger(__name__)


def _resolved_event_zone(verdict: Verdict) -> str | None:
    if verdict.event_context and verdict.event_context.zone:
        return verdict.event_context.zone
    if getattr(verdict, "case", None) and getattr(verdict.case, "observation", None):
        zone = verdict.case.observation.zone
        if zone:
            return zone
    return None


class WebSocketManager:
    def __init__(self) -> None:
        self._connections: list[WebSocket] = []

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        self._connections.append(ws)
        logger.info("WS client connected. Total: %d", len(self._connections))

    def disconnect(self, ws: WebSocket) -> None:
        self._connections = [c for c in self._connections if c is not ws]
        logger.info("WS client disconnected. Total: %d", len(self._connections))

    async def broadcast(self, data: dict) -> None:
        dead = []
        for ws in self._connections:
            try:
                await ws.send_text(json.dumps(data, default=str))
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.disconnect(ws)

    @property
    def connection_count(self) -> int:
        return len(self._connections)


class PipelineManager:
    def __init__(self) -> None:
        self._pipelines: dict[str, object] = {}
        self._groq_client: AsyncGroq | None = None
        self._db_factory: Callable | None = None

    def init(self, db_factory: Callable) -> None:
        self._groq_client = AsyncGroq(api_key=settings.groq_api_key) if settings.groq_api_key else None
        self._db_factory = db_factory

    async def start(
        self,
        stream_id: str,
        uri: str,
        label: str,
        site_id: str,
        zone: str,
    ) -> None:
        if stream_id in self._pipelines:
            await self.stop(stream_id)

        if self._groq_client is None or self._db_factory is None:
            raise RuntimeError("PipelineManager not initialised")

        from backend.agent.pipeline import StreamPipeline

        meta = StreamMeta(
            stream_id=stream_id,
            label=label,
            site_id=site_id,
            zone=zone,
            uri=uri,
        )

        pipeline = StreamPipeline(
            stream_meta=meta,
            db_factory=self._db_factory,
            groq_client=self._groq_client,
            on_verdict=self._handle_verdict,
        )
        pipeline.start()
        self._pipelines[stream_id] = pipeline
        logger.info("Started pipeline for stream %s", stream_id)

    async def stop(self, stream_id: str) -> None:
        pipeline = self._pipelines.pop(stream_id, None)
        if pipeline:
            await pipeline.stop()
            logger.info("Stopped pipeline for stream %s", stream_id)

    async def stop_all(self) -> None:
        for stream_id in list(self._pipelines.keys()):
            await self.stop(stream_id)

    async def _handle_verdict(self, verdict: Verdict, frame) -> None:
        from backend.notifications import notifier

        if self._db_factory is None:
            raise RuntimeError("PipelineManager DB factory is not initialised")

        # Persist to DB
        async with self._db_factory() as db:
            await _persist_verdict(db, verdict)

        # Broadcast via WebSocket
        await ws_manager.broadcast(public_verdict(verdict))

        # Fire notifications (alert only)
        await notifier.dispatch(verdict)

    @property
    def active_count(self) -> int:
        return len(self._pipelines)

    @property
    def active_stream_ids(self) -> list[str]:
        return list(self._pipelines.keys())

    @property
    def groq_client(self) -> AsyncGroq | None:
        return self._groq_client


async def _persist_verdict(
    db: AsyncSession,
    verdict: Verdict,
    source_event_id: str | None = None,
    source: str | None = None,
) -> None:
    """Persist verdict to database with event, agent outputs, and threshold config."""
    import json as _json
    from backend.models.db import Event, AgentTrace
    from backend.public import public_case_fields
    from backend.agent.memory import update_memory

    source = source or (verdict.event_context.source if verdict.event_context else None)
    source_event_id = source_event_id or (
        verdict.event_context.source_event_id if verdict.event_context else None
    )
    persisted_context = verdict.event_context.model_dump(mode="json") if verdict.event_context else {}
    metadata = persisted_context.get("metadata", {}) if isinstance(persisted_context, dict) else {}
    if not isinstance(metadata, dict):
        metadata = {}
    metadata["routing"] = {
        "risk_level": verdict.routing.risk_level,
        "visibility_policy": verdict.routing.visibility_policy,
        "notification_policy": verdict.routing.notification_policy,
        "storage_policy": verdict.routing.storage_policy,
        "decision_reason": verdict.audit.liability_digest.decision_reasoning,
    }
    metadata["case"] = public_case_fields(verdict)["case"]
    persisted_context["metadata"] = metadata

    stream_db_id = await _resolve_stream_db_id(db, verdict.stream_id)
    if not stream_db_id:
        # Auto-create stream when not found (e.g. ingest-created verdicts with
        # a stream_id that differs from the auto-provisioned URI).
        from backend.models.db import Stream

        new_stream = Stream(
            uri=verdict.stream_id,
            site_id=verdict.site_id,
            zone=_resolved_event_zone(verdict) or "unknown",
            label=f"Auto-created: {verdict.stream_id}",
        )
        db.add(new_stream)
        await db.flush()
        stream_db_id = new_stream.id
        logger.info("Auto-created stream %s for verdict persist", verdict.stream_id)

    event = Event(
        id=verdict.event_id,
        stream_id=stream_db_id,
        zone=_resolved_event_zone(verdict),
        timestamp=verdict.timestamp,
        severity=verdict.routing.severity,
        categories=_json.dumps(verdict.routing.categories),
        description=verdict.description,
        bbox=_json.dumps([b.model_dump() for b in verdict.bbox]),
        b64_thumbnail=verdict.b64_thumbnail,
        verdict_action=verdict.routing.action,
        final_confidence=verdict.audit.liability_digest.confidence_score,
        summary=verdict.summary.headline,
        narrative_summary=verdict.summary.narrative,
        alert_reason=(
            verdict.audit.liability_digest.decision_reasoning
            if verdict.routing.notification_policy == "immediate"
            else None
        ),
        suppress_reason=(
            verdict.audit.liability_digest.decision_reasoning
            if verdict.routing.visibility_policy == "hidden"
            else None
        ),
        source_event_id=source_event_id,
        source=source,
        event_context=_json.dumps(persisted_context),
    )
    db.add(event)

    for agent_output in verdict.audit.agent_outputs:
        trace = AgentTrace(
            id=str(uuid.uuid4()),
            event_id=verdict.event_id,
            agent_id=agent_output.agent_id,
            role=agent_output.role,
            verdict=agent_output.verdict,
            confidence=agent_output.confidence,
            rationale=agent_output.rationale,
            chain_notes=_json.dumps(agent_output.chain_notes),
        )
        db.add(trace)

    await update_memory(db, verdict)
    await db.commit()

    from backend.agent.schedule import ScheduleLearner

    learner = ScheduleLearner()
    await learner.refresh_schedule_if_due(db, verdict.site_id)


async def _resolve_stream_db_id(db: AsyncSession, stream_identifier: str) -> str | None:
    """Resolve a persisted stream foreign key for both live and ingest-style identifiers."""
    from backend.models.db import Stream

    stream_result = await db.execute(
        select(Stream.id).where(Stream.id == stream_identifier)
    )
    stream_db_id = stream_result.scalar_one_or_none()
    if stream_db_id:
        return stream_db_id

    legacy_result = await db.execute(
        select(Stream.id).where(Stream.uri == stream_identifier)
    )
    return legacy_result.scalar_one_or_none()


ws_manager = WebSocketManager()
pipeline_manager = PipelineManager()
