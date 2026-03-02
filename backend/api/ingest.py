from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database import get_db
from backend.hub import pipeline_manager
from backend.models.schemas import StreamMeta, Verdict

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/ingest", tags=["ingest"])


class FrameIngestRequest(BaseModel):
    b64_frame: str
    stream_id: str
    label: str = "direct"
    site_id: str = "default"
    zone: str = "general"


@router.post("/frame", response_model=dict)
async def ingest_frame(body: FrameIngestRequest, db: AsyncSession = Depends(get_db)):
    from backend.agent.pipeline import process_frame
    from backend.agent.ingest import Base64FrameSource
    from backend.hub import ws_manager
    from backend.notifications import notifier
    from backend.hub import _persist_verdict

    meta = StreamMeta(
        stream_id=body.stream_id,
        label=body.label,
        site_id=body.site_id,
        zone=body.zone,
        uri="direct",
    )

    source = Base64FrameSource(body.b64_frame)
    frame = None
    async for f in source.stream():
        frame = f
        break

    if frame is None:
        raise HTTPException(400, "Could not decode frame")

    groq_client = pipeline_manager.groq_client
    verdict = await process_frame(
        frame=frame,
        stream_meta=meta,
        db=db,
        groq_client=groq_client,
    )

    await _persist_verdict(db, verdict)

    payload = verdict.model_dump(mode="json")
    payload.pop("b64_frame", None)
    await ws_manager.broadcast(payload)
    await notifier.dispatch(verdict)

    return payload
