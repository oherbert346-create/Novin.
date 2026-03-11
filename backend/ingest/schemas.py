"""Canonical ingest payload — universal contract for any brand."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field, model_validator

from backend.policy import UNKNOWN_ZONE


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _first_string(*values: Any) -> str | None:
    for value in values:
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _merge_metadata(*values: Any) -> dict[str, Any]:
    merged: dict[str, Any] = {}
    for value in values:
        if isinstance(value, dict):
            merged.update(value)
    return merged


class CanonicalIngestPayload(BaseModel):
    """Universal ingest schema. Any brand sends this (directly or via adapter)."""

    home_id: str = "home"
    cam_id: str
    event_id: str | None = None
    source_event_id: str | None = None
    source: str = "generic"
    timestamp: datetime | None = None
    image_url: str | None = None
    image_b64: str | None = None
    image_url_headers: dict[str, str] | None = None  # e.g. {"Authorization": "Bearer ..."}
    label: str = ""
    zone: str = UNKNOWN_ZONE
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def lift_embedded_image_fields(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value

        data = dict(value)
        image = _as_dict(data.get("image"))
        attachment = _as_dict(data.get("attachment"))
        attachments = data.get("attachments")
        first_attachment = attachments[0] if isinstance(attachments, list) and attachments else None
        first_attachment_dict = _as_dict(first_attachment)
        snapshot = _as_dict(data.get("snapshot"))
        media = _as_dict(data.get("media"))
        event = _as_dict(data.get("event"))

        if not data.get("image_b64"):
            data["image_b64"] = _first_string(
                image.get("b64"),
                image.get("base64"),
                image.get("embedded"),
                attachment.get("image_b64"),
                attachment.get("b64"),
                first_attachment_dict.get("image_b64"),
                first_attachment_dict.get("b64"),
                snapshot.get("b64"),
                snapshot.get("base64"),
                media.get("image_b64"),
                media.get("b64"),
            )

        if not data.get("image_url"):
            data["image_url"] = _first_string(
                image.get("url"),
                attachment.get("url"),
                attachment.get("image_url"),
                first_attachment_dict.get("url"),
                first_attachment_dict.get("image_url"),
                snapshot.get("url"),
                media.get("image_url"),
                media.get("url"),
            )

        if not data.get("image_url_headers"):
            headers = _merge_metadata(
                image.get("headers"),
                attachment.get("headers"),
                first_attachment_dict.get("headers"),
                snapshot.get("headers"),
                media.get("headers"),
            )
            data["image_url_headers"] = headers or None

        metadata = _merge_metadata(
            _as_dict(data.get("metadata")),
            {"image": image} if image else {},
            {"attachment": attachment} if attachment else {},
            {"attachments": attachments[:3]} if isinstance(attachments, list) and attachments else {},
            {"snapshot": snapshot} if snapshot else {},
            {"media": media} if media else {},
            {"event": event} if event else {},
            {"payload_keys": sorted(str(key) for key in data.keys())},
        )
        data["metadata"] = metadata

        if not data.get("source_event_id"):
            data["source_event_id"] = _first_string(event.get("id"), data.get("id"), data.get("event_id"))
        if not data.get("label"):
            data["label"] = _first_string(data.get("title"), event.get("label"), image.get("label")) or ""
        if not data.get("zone") or data.get("zone") == "":
            data["zone"] = _first_string(event.get("zone"), attachment.get("zone"), snapshot.get("zone"), UNKNOWN_ZONE)

        return data

    @model_validator(mode="after")
    def require_image(self) -> "CanonicalIngestPayload":
        has_url = self.image_url and str(self.image_url).strip()
        has_b64 = self.image_b64 and str(self.image_b64).strip()
        if not has_url and not has_b64:
            raise ValueError("At least one of image_url or image_b64 is required")
        return self
