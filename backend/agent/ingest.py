from __future__ import annotations

import asyncio
import base64
from abc import ABC, abstractmethod
from pathlib import Path
from typing import AsyncIterator

import cv2
import numpy as np


class FrameSource(ABC):
    @abstractmethod
    async def stream(self) -> AsyncIterator[np.ndarray]:
        ...

    async def close(self) -> None:
        pass


class _CVSource(FrameSource):
    def __init__(self, uri: str | int) -> None:
        self._uri = uri
        self._cap: cv2.VideoCapture | None = None
        self._running = False

    def _open(self) -> None:
        self._cap = cv2.VideoCapture(self._uri)
        if not self._cap.isOpened():
            raise RuntimeError(f"Cannot open source: {self._uri}")

    def _read_frame(self) -> np.ndarray | None:
        if self._cap is None:
            return None
        ret, frame = self._cap.read()
        return frame if ret else None

    async def stream(self) -> AsyncIterator[np.ndarray]:
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, self._open)
        self._running = True
        try:
            while self._running:
                frame = await loop.run_in_executor(None, self._read_frame)
                if frame is None:
                    break
                yield frame
        finally:
            await self.close()

    async def close(self) -> None:
        self._running = False
        if self._cap is not None:
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, self._cap.release)
            self._cap = None


class RTSPSource(_CVSource):
    def __init__(self, uri: str) -> None:
        super().__init__(uri)


class HLSSource(_CVSource):
    def __init__(self, uri: str) -> None:
        super().__init__(uri)


class FileSource(_CVSource):
    def __init__(self, path: str) -> None:
        super().__init__(path)


class WebcamSource(_CVSource):
    def __init__(self, device_index: int = 0) -> None:
        super().__init__(device_index)


class ImageSource(FrameSource):
    def __init__(self, path: str) -> None:
        self._path = path

    async def stream(self) -> AsyncIterator[np.ndarray]:
        loop = asyncio.get_event_loop()
        frame = await loop.run_in_executor(None, cv2.imread, self._path)
        if frame is None:
            raise RuntimeError(f"Cannot read image: {self._path}")
        yield frame


class Base64FrameSource(FrameSource):
    def __init__(self, b64_data: str) -> None:
        self._b64 = b64_data

    async def stream(self) -> AsyncIterator[np.ndarray]:
        img_bytes = base64.b64decode(self._b64)
        arr = np.frombuffer(img_bytes, np.uint8)
        frame = cv2.imdecode(arr, cv2.IMREAD_COLOR)
        if frame is None:
            raise RuntimeError("Cannot decode base64 frame")
        yield frame


def make_source(uri: str) -> FrameSource:
    lower = uri.lower()
    if lower.startswith("rtsp://"):
        return RTSPSource(uri)
    if lower.startswith("rtmp://"):
        return RTSPSource(uri)
    if lower.startswith("http://") or lower.startswith("https://"):
        if any(ext in lower for ext in [".m3u8", ".m3u"]):
            return HLSSource(uri)
        return RTSPSource(uri)
    if lower.startswith("data:image") or (len(uri) > 100 and "/" not in uri[:20]):
        b64 = uri.split(",", 1)[-1] if "," in uri else uri
        return Base64FrameSource(b64)
    path = Path(uri)
    if path.suffix.lower() in {".jpg", ".jpeg", ".png", ".bmp", ".webp", ".tiff"}:
        return ImageSource(uri)
    if path.suffix.lower() in {".mp4", ".avi", ".mov", ".mkv", ".wmv", ".flv"}:
        return FileSource(uri)
    if uri.isdigit():
        return WebcamSource(int(uri))
    return FileSource(uri)
