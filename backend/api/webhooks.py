"""Webhook endpoints for vendor integrations (Frigate, Wyze, Generic)."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database import get_db
from backend.ingest.adapters import frigate, wyze
from backend.ingest.schemas import CanonicalIngestPayload

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/webhooks", tags=["webhooks"])


@router.post("/frigate")
async def frigate_webhook(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """
    Accept Frigate NVR webhook.
    
    Payload example:
    {
        "type": "end",
        "after": {
            "id": "event_id",
            "camera": "front_door",
            "label": "person",
            "current_zones": ["front_door"],
            "start_time": 1234567890.0
        }
    }
    
    Or provide image_url directly in payload.
    """
    try:
        body = await request.json()
    except Exception as e:
        raise HTTPException(400, f"Invalid JSON: {e}")
    
    try:
        canonical = frigate.normalise(body, dict(request.headers))
    except ValueError as e:
        raise HTTPException(400, f"Frigate adapter error: {e}")
    
    return await _process_webhook(canonical, db)


@router.post("/wyze")
async def wyze_webhook(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """
    Accept Wyze Bridge webhook.
    
    Headers:
    - X-Camera: Camera name/ID
    - X-Attach: Image URL
    - X-Event: Event type (motion, person, etc.)
    
    Body: Text message like "Motion detected on camera at 14:30:00"
    """
    headers = dict(request.headers)
    
    try:
        body = await request.body()
        body_str = body.decode("utf-8") if body else ""
    except Exception as e:
        raise HTTPException(400, f"Invalid body: {e}")
    
    try:
        canonical = wyze.normalise(body_str, headers)
    except ValueError as e:
        raise HTTPException(400, f"Wyze adapter error: {e}")
    
    return await _process_webhook(canonical, db)


@router.post("/generic")
async def generic_webhook(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """
    Accept generic webhook with image URL.
    
    Payload:
    {
        "image_url": "http://example.com/image.jpg",  # required (or image_b64)
        "image_b64": "base64...",  # alternative to image_url
        "cam_id": "camera_1",  # required
        "home_id": "home",  # optional, defaults to "home"
        "zone": "front_door",  # optional
        "label": "motion",  # optional
        "metadata": {...}  # optional
    }
    """
    try:
        body = await request.json()
    except Exception as e:
        raise HTTPException(400, f"Invalid JSON: {e}")
    
    if not isinstance(body, dict):
        raise HTTPException(400, "Payload must be JSON object")
    
    # Validate required fields
    if "cam_id" not in body:
        raise HTTPException(400, "Missing required field: cam_id")
    
    if "image_url" not in body and "image_b64" not in body:
        raise HTTPException(400, "Must provide either image_url or image_b64")
    
    # Build canonical payload
    canonical = CanonicalIngestPayload(
        home_id=body.get("home_id", "home"),
        cam_id=body["cam_id"],
        zone=body.get("zone"),
        image_url=body.get("image_url"),
        image_b64=body.get("image_b64"),
        label=body.get("label"),
        source="generic_webhook",
        metadata=body.get("metadata", {}),
    )
    
    return await _process_webhook(canonical, db)


async def _process_webhook(
    canonical: CanonicalIngestPayload,
    db: AsyncSession,
) -> dict:
    """Process canonical payload and return response."""
    from backend.ingest.processor import process_canonical
    from backend.hub import pipeline_manager
    
    try:
        result = await process_canonical(
            payload=canonical,
            db_factory=lambda: db,
            groq_client=pipeline_manager.groq_client,
            on_verdict=pipeline_manager._handle_verdict,
        )
        
        return {
            "status": "processed",
            "event_id": result.get("event_id"),
            "cam_id": canonical.cam_id,
            "home_id": canonical.home_id,
        }
    
    except Exception as e:
        logger.error("Webhook processing failed: %s", e, exc_info=True)
        raise HTTPException(500, f"Processing failed: {e}")
