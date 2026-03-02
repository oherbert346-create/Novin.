from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database import get_db
from backend.models.db import Stream
from backend.models.schemas import StreamCreate, StreamResponse

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/streams", tags=["streams"])


@router.post("", response_model=StreamResponse, status_code=201)
async def create_stream(body: StreamCreate, db: AsyncSession = Depends(get_db)):
    stream = Stream(
        id=str(uuid.uuid4()),
        uri=body.uri,
        label=body.label,
        site_id=body.site_id,
        zone=body.zone,
        created_at=datetime.utcnow(),
        active=False,
    )
    db.add(stream)
    await db.commit()
    await db.refresh(stream)

    # Start pipeline via app state
    from fastapi import Request
    return StreamResponse(
        id=stream.id,
        uri=stream.uri,
        label=stream.label,
        site_id=stream.site_id,
        zone=stream.zone,
        created_at=stream.created_at,
        active=stream.active,
    )


@router.post("/{stream_id}/start", response_model=StreamResponse)
async def start_stream(stream_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Stream).where(Stream.id == stream_id))
    stream = result.scalar_one_or_none()
    if not stream:
        raise HTTPException(404, "Stream not found")

    from backend.hub import pipeline_manager
    await pipeline_manager.start(stream_id, stream.uri, stream.label, stream.site_id, stream.zone)

    stream.active = True
    await db.commit()
    await db.refresh(stream)
    return StreamResponse(
        id=stream.id,
        uri=stream.uri,
        label=stream.label,
        site_id=stream.site_id,
        zone=stream.zone,
        created_at=stream.created_at,
        active=stream.active,
    )


@router.post("/{stream_id}/stop", response_model=StreamResponse)
async def stop_stream(stream_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Stream).where(Stream.id == stream_id))
    stream = result.scalar_one_or_none()
    if not stream:
        raise HTTPException(404, "Stream not found")

    from backend.hub import pipeline_manager
    await pipeline_manager.stop(stream_id)

    stream.active = False
    await db.commit()
    await db.refresh(stream)
    return StreamResponse(
        id=stream.id,
        uri=stream.uri,
        label=stream.label,
        site_id=stream.site_id,
        zone=stream.zone,
        created_at=stream.created_at,
        active=stream.active,
    )


@router.delete("/{stream_id}", status_code=204)
async def delete_stream(stream_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Stream).where(Stream.id == stream_id))
    stream = result.scalar_one_or_none()
    if not stream:
        raise HTTPException(404, "Stream not found")

    from backend.hub import pipeline_manager
    await pipeline_manager.stop(stream_id)

    await db.delete(stream)
    await db.commit()


@router.get("", response_model=list[StreamResponse])
async def list_streams(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Stream).order_by(Stream.created_at.desc()))
    streams = result.scalars().all()
    return [
        StreamResponse(
            id=s.id,
            uri=s.uri,
            label=s.label,
            site_id=s.site_id,
            zone=s.zone,
            created_at=s.created_at,
            active=s.active,
        )
        for s in streams
    ]


@router.get("/{stream_id}", response_model=StreamResponse)
async def get_stream(stream_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Stream).where(Stream.id == stream_id))
    stream = result.scalar_one_or_none()
    if not stream:
        raise HTTPException(404, "Stream not found")
    return StreamResponse(
        id=stream.id,
        uri=stream.uri,
        label=stream.label,
        site_id=stream.site_id,
        zone=stream.zone,
        created_at=stream.created_at,
        active=stream.active,
    )
