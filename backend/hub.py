from __future__ import annotations

import json
import logging
import uuid
from typing import Callable

from fastapi import WebSocket
from groq import AsyncGroq
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import settings
from backend.models.schemas import StreamMeta, Verdict

logger = logging.getLogger(__name__)


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
        self._groq_client = AsyncGroq(api_key=settings.groq_api_key)
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
        payload = verdict.model_dump(mode="json")
        payload.pop("b64_frame", None)
        await ws_manager.broadcast(payload)

        # Fire notifications (alert only)
        await notifier.dispatch(verdict)

    @property
    def active_count(self) -> int:
        return len(self._pipelines)

    @property
    def active_stream_ids(self) -> list[str]:
        return list(self._pipelines.keys())

    @property
    def groq_client(self) -> AsyncGroq:
        if self._groq_client is None:
            raise RuntimeError("PipelineManager is not initialised")
        return self._groq_client


async def _persist_verdict(db: AsyncSession, verdict: Verdict) -> None:
    import json as _json
    from backend.models.db import AgentTrace, Event

    event = Event(
        id=verdict.frame_id,
        stream_id=verdict.stream_id,
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
        alert_reason=verdict.audit.liability_digest.decision_reasoning if verdict.routing.action == "alert" else None,
        suppress_reason=verdict.audit.liability_digest.decision_reasoning if verdict.routing.action == "suppress" else None,
    )
    db.add(event)

    for agent_output in verdict.audit.agent_outputs:
        trace = AgentTrace(
            id=str(uuid.uuid4()),
            event_id=verdict.frame_id,
            agent_id=agent_output.agent_id,
            role=agent_output.role,
            verdict=agent_output.verdict,
            confidence=agent_output.confidence,
            rationale=agent_output.rationale,
            chain_notes=_json.dumps(agent_output.chain_notes),
        )
        db.add(trace)

    await db.commit()


ws_manager = WebSocketManager()
pipeline_manager = PipelineManager()
