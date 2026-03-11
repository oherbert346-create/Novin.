"""Build StreamMeta from canonical payload."""

from __future__ import annotations

from backend.ingest.schemas import CanonicalIngestPayload
from backend.models.schemas import StreamMeta


def to_stream_meta(payload: CanonicalIngestPayload) -> StreamMeta:
    """Convert canonical payload to StreamMeta for process_frame."""
    return StreamMeta(
        stream_id=payload.cam_id,
        label=payload.label or payload.cam_id,
        site_id=payload.home_id,
        zone=payload.zone,
        uri="ingest",
    )
