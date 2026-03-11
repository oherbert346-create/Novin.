"""Adapter type for vendor normalisers."""

from __future__ import annotations

from typing import Any, Callable

from backend.ingest.schemas import CanonicalIngestPayload

AdapterFn = Callable[[Any, dict | None], CanonicalIngestPayload]
