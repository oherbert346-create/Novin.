#!/usr/bin/env python3
"""
Test vision analysis on a single image. Fetches from URL or uses local file.
Usage:
  PYTHONPATH=. python scripts/test_vision_image.py [--url URL] [--path PATH]
"""

from __future__ import annotations

import argparse
import asyncio
import sys

import httpx
import numpy as np

# Default: security-style image (person at front door, COCO)
DEFAULT_URL = "http://images.cocodataset.org/val2017/000000000285.jpg"
# Alternative: person with dog
ALT_URL = "http://images.cocodataset.org/val2017/000000000139.jpg"


def _fetch_image(url: str) -> bytes:
    with httpx.Client(timeout=15) as c:
        resp = c.get(url)
        resp.raise_for_status()
        return resp.content


def _load_image(path: str) -> bytes:
    with open(path, "rb") as f:
        return f.read()


def _bytes_to_b64_and_frame(data: bytes) -> tuple[str, np.ndarray]:
    import cv2
    arr = np.frombuffer(data, np.uint8)
    frame = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    if frame is None:
        raise ValueError("Could not decode image")
    from backend.agent.vision import encode_frame
    b64 = encode_frame(frame)
    return b64, frame


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default=DEFAULT_URL, help="Image URL to fetch")
    parser.add_argument("--path", help="Local image path (overrides --url)")
    parser.add_argument("--debug", action="store_true", help="Print raw vision model JSON")
    args = parser.parse_args()

    print("=" * 60)
    print("VISION TEST — direct schema")
    print("=" * 60)

    try:
        if args.path:
            print(f"\n1. Loading image from: {args.path}")
            data = _load_image(args.path)
        else:
            print(f"\n1. Fetching image from: {args.url}")
            data = _fetch_image(args.url)
        print(f"   Size: {len(data):,} bytes")

        b64, frame = _bytes_to_b64_and_frame(data)
        print(f"   Decoded: {frame.shape[1]}x{frame.shape[0]}")

        from backend.agent.vision import analyse_frame
        from backend.models.schemas import StreamMeta

        meta = StreamMeta(
            stream_id="test_cam",
            site_id="home",
            zone="front_door",
            label="Front Door",
            uri="",
        )

        print("\n2. Running vision analysis...")

        if args.debug:
            from backend.agent.vision import _SYSTEM_PROMPT
            from backend.config import settings
            from backend.provider import active_vision_model, get_siliconflow_client, get_together_client
            messages = [
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": [
                    {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
                    {"type": "text", "text": f"Camera: {meta.label} | Zone: {meta.zone}\nAnalyse this home security camera frame."},
                ]},
            ]
            prov = settings.vision_provider
            if prov == "groq":
                from groq import AsyncGroq
                c = AsyncGroq()
            elif prov == "siliconflow":
                c = get_siliconflow_client()
            else:
                c = get_together_client()
            r = await c.chat.completions.create(model=active_vision_model(), messages=messages, max_tokens=400, temperature=0.1)
            raw = (r.choices[0].message.content or "").strip()
            if raw.startswith("```"):
                raw = raw.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
            print("\n   [RAW MODEL OUTPUT]:")
            print(raw)

        result = await analyse_frame(b64, meta, client=None)
        print(f"   Latency: {result.latency_ms:.0f} ms")

        print("\n3. VisionResult:")
        print(f"   threat:      {result.threat}")
        print(f"   severity:   {result.severity}")
        print(f"   categories: {result.categories}")
        print(f"   identity:   {result.identity_labels}")
        print(f"   risk:       {result.risk_labels}")
        print(f"   confidence: {result.confidence:.2f}")
        print(f"   uncertainty: {result.uncertainty:.2f}")
        print(f"   description: {result.description}")

        print("\n" + "=" * 60)
        return 0

    except Exception as e:
        print(f"\nERROR: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
