"""Wyze Bridge webhook adapter. Parses headers X-Camera, X-Attach, X-Event."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from backend.ingest.schemas import CanonicalIngestPayload


# Zone inference mapping - camera names to security zones
_ZONE_MAP = {
    "front_door": ["front", "door", "entrance", "porch"],
    "backyard": ["back", "yard", "garden", "patio", "deck"],
    "driveway": ["drive", "garage", "car", "parking"],
    "living_room": ["living", "lounge", "family", "livingroom"],
    "kitchen": ["kitchen", "dining"],
    "bedroom": ["bed", "sleeping", "master", "bedroom"],
}


def _infer_zone(camera_name: str) -> str:
    """Infer security zone from camera name using keyword matching."""
    cam_lower = camera_name.lower()
    for zone, keywords in _ZONE_MAP.items():
        if any(kw in cam_lower for kw in keywords):
            return zone
    # Default to front_door if no match
    return "front_door"


def normalise(body: Any, headers: dict | None = None) -> CanonicalIngestPayload:
    """
    Normalise Wyze Bridge webhook to canonical.
    Headers: X-Camera, X-Attach (image URL), X-Event, X-Title, X-Tags
    Body: "Motion detected on cam-name at hh:mm:ss"
    """
    headers = headers or {}
    if not isinstance(headers, dict):
        headers = {}

    cam_id = headers.get("X-Camera") or headers.get("x-camera") or "unknown"
    image_url = headers.get("X-Attach") or headers.get("x-attach")
    label = headers.get("X-Event") or headers.get("x-event") or "motion"

    if not image_url:
        raise ValueError("Wyze adapter requires X-Attach header with image URL")

    # Parse body for timestamp if present
    timestamp = None
    if isinstance(body, str):
        match = re.search(r"at\s+(\d{1,2}:\d{2}:\d{2})", body, re.I)
        if match:
            try:
                from datetime import time as dt_time

                t = datetime.strptime(match.group(1), "%H:%M:%S").time()
                now = datetime.utcnow()
                timestamp = now.replace(hour=t.hour, minute=t.minute, second=t.second)
            except ValueError:
                pass

    # Infer zone from camera name
    zone = _infer_zone(cam_id)

    return CanonicalIngestPayload(
        home_id="home",
        cam_id=cam_id,
        event_id=None,
        source_event_id=None,
        source="wyze",
        timestamp=timestamp,
        image_url=image_url,
        image_b64=None,
        label=label,
        zone=zone,
        metadata={"wyze_body": body[:200] if isinstance(body, str) else str(body)[:200]},
    )
