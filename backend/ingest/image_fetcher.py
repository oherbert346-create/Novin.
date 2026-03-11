"""Fetch frame from image URL."""

from __future__ import annotations

import logging

import httpx
import numpy as np
from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception_type,
    before_sleep_log,
)

logger = logging.getLogger(__name__)


@retry(
    retry=retry_if_exception_type((httpx.TimeoutException, httpx.ConnectError, httpx.HTTPStatusError)),
    stop=stop_after_attempt(2),
    wait=wait_exponential(multiplier=1, min=1, max=10),
    before_sleep=before_sleep_log(logger, logging.WARNING),
    reraise=True,
)
async def fetch_frame_from_url(
    url: str,
    timeout: float | None = None,
    headers: dict[str, str] | None = None,
) -> np.ndarray:
    """Fetch image from URL and decode to numpy array (BGR, cv2-compatible)."""
    import cv2

    request_headers = dict(headers) if headers else {}
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.get(url, headers=request_headers)
        resp.raise_for_status()
        arr = np.frombuffer(resp.content, np.uint8)
        frame = cv2.imdecode(arr, cv2.IMREAD_COLOR)
        if frame is None:
            raise ValueError("Could not decode image from URL")
        return frame
