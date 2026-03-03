from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime

from groq import AsyncGroq
from sqlalchemy.ext.asyncio import AsyncSession

from backend.agent import history as history_agent
from backend.agent import vision as vision_agent
from backend.agent.bus import AgentMessageBus
from backend.agent.ingest import FrameSource, make_source
from backend.agent.reasoning.arbiter import run_reasoning
from backend.config import settings
from backend.models.schemas import FramePacket, StreamMeta, Verdict

logger = logging.getLogger(__name__)

_REASONING_AGENT_IDS = [
    "threat_escalation",
    "behavioural_pattern",
    "context_asset_risk",
    "adversarial_challenger",
]


async def process_frame(
    frame,
    stream_meta: StreamMeta,
    db: AsyncSession,
    groq_client: AsyncGroq,
) -> Verdict:
    frame_id = str(uuid.uuid4())
    timestamp = datetime.utcnow()

    b64 = vision_agent.encode_frame(frame)

    vision_result, history_ctx = await asyncio.gather(
        vision_agent.analyse_frame(b64, stream_meta, groq_client),
        history_agent.query_history(
            db=db,
            stream_id=stream_meta.stream_id,
            site_id=stream_meta.site_id,
            event_types=["person", "pet", "package", "vehicle", "intrusion", "motion"],
        ),
    )

    packet = FramePacket(
        frame_id=frame_id,
        stream_id=stream_meta.stream_id,
        timestamp=timestamp,
        b64_frame=b64,
        stream_meta=stream_meta,
        vision=vision_result,
        history=history_ctx,
    )

    bus = AgentMessageBus(_REASONING_AGENT_IDS)
    verdict = await run_reasoning(
        packet,
        b64_thumbnail=b64,
        bus=bus,
        client=groq_client,
    )

    return verdict


class StreamPipeline:
    def __init__(
        self,
        stream_meta: StreamMeta,
        db_factory,
        groq_client: AsyncGroq,
        on_verdict,
    ) -> None:
        self._meta = stream_meta
        self._db_factory = db_factory
        self._groq = groq_client
        self._on_verdict = on_verdict
        self._source: FrameSource | None = None
        self._task: asyncio.Task | None = None
        self._running = False

    def start(self) -> None:
        self._running = True
        self._task = asyncio.create_task(self._run(), name=f"pipeline-{self._meta.stream_id}")

    async def stop(self) -> None:
        self._running = False
        if self._source:
            await self._source.close()
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    async def _run(self) -> None:
        self._source = make_source(self._meta.uri)
        logger.info("Pipeline started for stream %s (%s)", self._meta.stream_id, self._meta.uri)
        try:
            async for frame in self._source.stream():
                if not self._running:
                    break
                try:
                    async with self._db_factory() as db:
                        verdict = await process_frame(
                            frame=frame,
                            stream_meta=self._meta,
                            db=db,
                            groq_client=self._groq,
                        )
                    await self._on_verdict(verdict, frame)
                except Exception as exc:
                    logger.exception("Pipeline frame error for %s: %s", self._meta.stream_id, exc)
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            logger.exception("Pipeline fatal error for %s: %s", self._meta.stream_id, exc)
        finally:
            logger.info("Pipeline stopped for stream %s", self._meta.stream_id)
