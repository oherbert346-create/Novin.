"""Pluggable adapters for vendor-specific ingest formats."""

from __future__ import annotations

from backend.ingest.adapters import frigate, wyze
from backend.ingest.adapters.registry import (
    get_adapter,
    normalise,
    register_adapter,
)
from backend.ingest.schemas import CanonicalIngestPayload

register_adapter("frigate", frigate.normalise)
register_adapter("wyze", wyze.normalise)

__all__ = ["normalise", "register_adapter", "get_adapter", "CanonicalIngestPayload"]
