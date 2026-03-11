"""Process canonical payload through Phase 1 pipeline."""

from __future__ import annotations

import base64
import logging
import uuid
from time import perf_counter

import numpy as np
from sqlalchemy import select

from backend.ingest.image_fetcher import fetch_frame_from_url
from backend.ingest.normaliser import to_stream_meta
from backend.ingest.schemas import CanonicalIngestPayload
from backend.models.schemas import EventContext
from backend.public import public_verdict

logger = logging.getLogger(__name__)


async def process_canonical(
    payload: CanonicalIngestPayload,
    db_factory,
    groq_client,
    on_verdict,
) -> dict:
    """
    Process canonical payload: resolve frame, run process_frame, persist, broadcast, notify.
    Returns verdict dict for sync mode; for async, caller uses event_id from payload.
    """
    from backend.agent.pipeline import process_frame
    from backend.hub import _persist_verdict, ws_manager
    from backend.models.db import Event
    from backend.notifications import notifier

    e2e_start = perf_counter()

    # Ensure stream exists (required for PostgreSQL foreign key constraint)
    from backend.models.db import Stream
    async with db_factory() as db:
        stream_result = await db.execute(
            select(Stream).where(Stream.uri == payload.cam_id)
        )
        existing_stream = stream_result.scalar_one_or_none()
        if not existing_stream:
            new_stream = Stream(
                uri=payload.cam_id,
                site_id=payload.home_id,
                zone=payload.zone or "unknown",
                label=f"Auto-created: {payload.cam_id}",
            )
            db.add(new_stream)
            await db.commit()
            logger.info("Auto-created stream: %s for home: %s", payload.cam_id, payload.home_id)
    
    # Idempotency check
    if payload.source_event_id and payload.source:
        async with db_factory() as db:
            result = await db.execute(
                select(Event.id).where(
                    Event.source == payload.source,
                    Event.source_event_id == payload.source_event_id,
                )
            )
            existing = result.scalar_one_or_none()
            if existing:
                return {
                    "event_id": existing,
                    "status": "duplicate",
                    "cam_id": payload.cam_id,
                    "home_id": payload.home_id,
                }

    # Resolve frame
    fetch_start = perf_counter()
    if payload.image_b64:
        import cv2

        img_bytes = base64.b64decode(payload.image_b64)
        arr = np.frombuffer(img_bytes, np.uint8)
        frame = cv2.imdecode(arr, cv2.IMREAD_COLOR)
        if frame is None:
            raise ValueError("Could not decode base64 image")
        fetch_ms = round((perf_counter() - fetch_start) * 1000, 0)
        logger.info("E2E: image from b64 decode in %.0f ms", fetch_ms)
    elif payload.image_url:
        frame = await fetch_frame_from_url(
            payload.image_url,
            headers=payload.image_url_headers,
        )
        fetch_ms = round((perf_counter() - fetch_start) * 1000, 0)
        logger.info("E2E: image from URL fetch in %.0f ms", fetch_ms)
    else:
        raise ValueError("No image_url or image_b64")

    event_id = payload.event_id or str(uuid.uuid4())
    stream_meta = to_stream_meta(payload)
    event_context = EventContext(
        source=payload.source,
        source_event_id=payload.source_event_id,
        cam_id=payload.cam_id,
        home_id=payload.home_id,
        zone=payload.zone,
        label=payload.label,
        ingest_mode="webhook" if payload.source != "generic" else "canonical",
        metadata=payload.metadata,
    )

    async with db_factory() as db:
        verdict = await process_frame(
            frame=frame,
            stream_meta=stream_meta,
            db=db,
            groq_client=groq_client,
            event_id=event_id,
            event_context=event_context,
        )

        await _persist_verdict(
            db,
            verdict,
            source_event_id=payload.source_event_id,
            source=payload.source,
        )

    payload_out = public_verdict(verdict)
    await ws_manager.broadcast(payload_out)
    await notifier.dispatch(verdict)

    e2e_ms = round((perf_counter() - e2e_start) * 1000, 0)
    pipeline_ms = verdict.telemetry.get("pipeline_latency_ms", 0)
    other_ms = max(0, e2e_ms - pipeline_ms - fetch_ms)
    logger.info(
        "E2E: total=%.0f ms | fetch=%.0f + pipeline=%.0f + other=%.0f",
        e2e_ms,
        fetch_ms,
        pipeline_ms,
        other_ms,
    )

    return payload_out
