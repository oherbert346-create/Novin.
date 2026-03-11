"""Universal ingest API — any brand via canonical schema or X-Source adapter."""

from __future__ import annotations

import asyncio
import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import settings
from backend.database import AsyncSessionLocal, get_db
from backend.hub import pipeline_manager
from backend.ingest.adapters import normalise
from backend.ingest.processor import process_canonical
from backend.ingest.schemas import CanonicalIngestPayload
from backend.metrics import get_metrics
from backend.policy import UNKNOWN_ZONE
from backend.public import public_verdict

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/novin", tags=["novin"])

_async_ingest_failures: int = 0


def async_ingest_failure_count() -> int:
    return _async_ingest_failures


class FrameIngestRequest(BaseModel):
    b64_frame: str
    stream_id: str
    label: str = "direct"
    site_id: str = "home"
    zone: str = UNKNOWN_ZONE


@router.post("/ingest", response_model=dict)
async def ingest_universal(request: Request):
    """
    Universal ingest — accepts canonical JSON or vendor payload + X-Source header.
    Returns 200 quickly with event_id and status; processes async.
    """
    source_header = (request.headers.get("X-Source") or request.headers.get("x-source") or "").strip() or None
    content_type = request.headers.get("content-type", "")
    raw = await request.body()

    # Parse body: JSON or raw
    if "application/json" in content_type:
        import json

        try:
            body = json.loads(raw)
        except json.JSONDecodeError:
            body = raw.decode("utf-8", errors="replace")
    else:
        body = raw.decode("utf-8", errors="replace") if raw else ""

    headers = dict(request.headers)

    try:
        payload = normalise(source_header, body, headers)
    except ValueError as e:
        raise HTTPException(422, f"Invalid payload: {e}") from e

    event_id = payload.event_id or str(uuid.uuid4())
    payload.event_id = event_id

    if not settings.ingest_async_default:
        # Sync mode
        try:
            result = await process_canonical(
                payload,
                db_factory=AsyncSessionLocal,
                groq_client=pipeline_manager.groq_client,
                on_verdict=None,
            )
            if "status" in result and result["status"] == "duplicate":
                return result
            return result
        except Exception as exc:
            logger.exception("Ingest processing failed: %s", exc)
            raise HTTPException(500, str(exc)) from exc

    # Async: queue and return 200
    async def _run() -> None:
        global _async_ingest_failures
        metrics = get_metrics()
        try:
            await process_canonical(
                payload,
                db_factory=AsyncSessionLocal,
                groq_client=pipeline_manager.groq_client,
                on_verdict=None,
            )
        except Exception as exc:
            _async_ingest_failures += 1
            metrics.increment_error("async_ingest_failure")
            logger.exception("Async ingest failed for event %s: %s", event_id, exc)

    asyncio.create_task(_run())
    return {
        "event_id": event_id,
        "status": "queued",
        "cam_id": payload.cam_id,
        "home_id": payload.home_id,
    }


@router.post("/ingest/frame", response_model=dict)
async def ingest_frame(
    body: FrameIngestRequest,
    async_mode: bool = False,
    db: AsyncSession = Depends(get_db),
):
    """
    Direct b64 frame ingest (legacy). Sync by default; ?async=1 for async.
    """
    from backend.agent.ingest import Base64FrameSource
    from backend.agent.pipeline import process_frame
    from backend.hub import _persist_verdict, ws_manager
    from backend.models.schemas import EventContext, StreamMeta
    from backend.notifications import notifier

    source = Base64FrameSource(body.b64_frame)
    frame = None
    async for f in source.stream():
        frame = f
        break

    if frame is None:
        raise HTTPException(400, "Could not decode frame")

    meta = StreamMeta(
        stream_id=body.stream_id,
        label=body.label,
        site_id=body.site_id,
        zone=body.zone,
        uri="direct",
    )

    groq_client = pipeline_manager.groq_client
    verdict = await process_frame(
        frame=frame,
        stream_meta=meta,
        db=db,
        groq_client=groq_client,
        event_context=EventContext(
            source="direct",
            cam_id=body.stream_id,
            home_id=body.site_id,
            zone=body.zone,
            label=body.label,
            ingest_mode="direct_frame",
            metadata={"uri": "direct"},
        ),
    )

    await _persist_verdict(db, verdict)
    await notifier.dispatch(verdict)

    payload = public_verdict(verdict)
    await ws_manager.broadcast(payload)

    if async_mode:
        return {
            "event_id": verdict.event_id,
            "status": "processed",
            "cam_id": verdict.stream_id,
            "home_id": verdict.site_id,
        }
    return payload
