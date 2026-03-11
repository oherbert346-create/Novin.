"""Pluggable adapter registry for any brand."""

from __future__ import annotations

import logging
from typing import Any, Callable

from backend.ingest.schemas import CanonicalIngestPayload

logger = logging.getLogger(__name__)

ADAPTER_REGISTRY: dict[str, Callable[[Any, dict | None], CanonicalIngestPayload]] = {}


def register_adapter(name: str, fn: Callable[[Any, dict | None], CanonicalIngestPayload]) -> None:
    """Register an adapter by name."""
    ADAPTER_REGISTRY[name.lower()] = fn
    logger.debug("Registered ingest adapter: %s", name)


def get_adapter(name: str) -> Callable[[Any, dict | None], CanonicalIngestPayload] | None:
    """Get adapter by name."""
    return ADAPTER_REGISTRY.get(name.lower())


def normalise(
    name: str | None,
    body: Any,
    headers: dict[str, str] | None = None,
) -> CanonicalIngestPayload:
    """
    Normalise incoming payload to canonical format.
    - If name is None and body validates as CanonicalIngestPayload, return as-is.
    - If name is set, dispatch to registered adapter.
    """
    headers = headers or {}

    if name:
        adapter = get_adapter(name)
        if not adapter:
            raise ValueError(f"Unknown adapter: {name}")
        return adapter(body, headers)

    # Try as canonical JSON
    if isinstance(body, dict):
        try:
            return CanonicalIngestPayload.model_validate(body)
        except Exception as e:
            raise ValueError(f"Body is not valid canonical payload: {e}") from e

    raise ValueError("Body must be canonical JSON when X-Source is not provided")
