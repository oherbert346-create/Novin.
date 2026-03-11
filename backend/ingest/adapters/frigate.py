"""Frigate NVR adapter. Accepts Frigate MQTT event JSON (as forwarded by mqttwarn etc)."""

from __future__ import annotations

import os
from datetime import datetime
from typing import Any

from backend.ingest.schemas import CanonicalIngestPayload


def normalise(body: Any, headers: dict | None = None) -> CanonicalIngestPayload:
    """
    Normalise Frigate event to canonical.
    Body: {"type": "end", "before": {...}, "after": {"id", "camera", "label", "start_time", "current_zones", ...}}
    """
    headers = headers or {}
    if not isinstance(body, dict):
        raise ValueError("Frigate payload must be JSON object")

    after = body.get("after") or body
    if not isinstance(after, dict):
        raise ValueError("Frigate payload must have 'after' or be event object")

    event_id = str(after.get("id", ""))
    camera = str(after.get("camera", "unknown"))
    label = str(after.get("label", "motion"))
    start_time = after.get("start_time")
    zones = after.get("current_zones") or after.get("entered_zones") or []
    zone = str(zones[0]) if zones else "front_door"

    # Timestamp: Frigate uses Unix seconds
    timestamp = None
    if start_time is not None:
        try:
            timestamp = datetime.utcfromtimestamp(float(start_time))
        except (TypeError, ValueError):
            pass

    # Image URL: from config or payload
    image_url = body.get("image_url") or after.get("image_url")
    if not image_url and event_id:
        base = os.environ.get(f"ADAPTER_IMAGE_BASE_URL_frigate".upper().replace("-", "_"))
        if base:
            image_url = f"{base.rstrip('/')}/api/events/{event_id}/snapshot.jpg"

    if not image_url and not body.get("image_b64"):
        raise ValueError("Frigate adapter needs image_url in payload or ADAPTER_IMAGE_BASE_URL_frigate")

    return CanonicalIngestPayload(
        home_id="home",
        cam_id=camera,
        event_id=None,
        source_event_id=event_id or None,
        source="frigate",
        timestamp=timestamp,
        image_url=image_url,
        image_b64=body.get("image_b64"),
        label=label,
        zone=zone,
        metadata={"frigate_zones": zones, "frigate_type": body.get("type")},
    )
