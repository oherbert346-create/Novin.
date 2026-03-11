"""Legacy ingest API — delegates to /api/novin for backward compatibility."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database import get_db
from backend.policy import UNKNOWN_ZONE

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/ingest", tags=["ingest"])


class FrameIngestRequest(BaseModel):
    b64_frame: str
    stream_id: str
    label: str = "direct"
    site_id: str = "home"
    zone: str = UNKNOWN_ZONE


@router.post("/frame", response_model=dict)
async def ingest_frame(body: FrameIngestRequest, db: AsyncSession = Depends(get_db)):
    """Delegate to novin ingest. Kept for backward compatibility."""
    from backend.api.novin.ingest import ingest_frame as novin_ingest_frame

    return await novin_ingest_frame(body=body, async_mode=False, db=db)
