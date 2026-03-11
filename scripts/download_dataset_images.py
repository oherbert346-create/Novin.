#!/usr/bin/env python3
"""
Download sample images from public datasets for ingest testing.
Uses COCO val2017 (person-relevant) and a few other sources.
"""

from __future__ import annotations

import base64
import json
import sys
from pathlib import Path

import httpx

# COCO val2017 - person images (known IDs from dataset)
COCO_VAL_IDS = [
    "000000000139",  # person
    "000000000285",  # person
    "000000000632",
    "000000000724",
    "000000001000",
    "000000002000",
    "000000003000",
]
COCO_BASE = "http://images.cocodataset.org/val2017"

# Fallback: placeholder images if COCO is slow/unavailable
PLACEHOLDER_URLS = [
    "https://picsum.photos/800/600",
    "https://picsum.photos/640/480",
    "https://picsum.photos/1280/720",
]


def download_image(url: str, timeout: float = 15.0) -> bytes:
    """Download image bytes from URL."""
    with httpx.Client(timeout=timeout, follow_redirects=True) as client:
        resp = client.get(url)
        resp.raise_for_status()
        return resp.content


def main() -> None:
    out_dir = Path(__file__).resolve().parent.parent / "test" / "fixtures" / "images"
    out_dir.mkdir(parents=True, exist_ok=True)

    manifest = []
    sources = []

    # Try COCO first
    for img_id in COCO_VAL_IDS:
        url = f"{COCO_BASE}/{img_id}.jpg"
        sources.append((f"coco_{img_id}", url))

    # Add placeholders
    for i, url in enumerate(PLACEHOLDER_URLS[:2]):
        sources.append((f"picsum_{i}", url))

    downloaded = 0
    for source, url in sources:
        try:
            data = download_image(url)
            out_path = out_dir / f"{source}.jpg"
            out_path.write_bytes(data)
            b64 = base64.b64encode(data).decode("ascii")
            manifest.append({
                "source": source,
                "url": url,
                "path": str(out_path),
                "size": len(data),
                "b64_preview": b64[:80] + "..." if len(b64) > 80 else b64,
            })
            downloaded += 1
            print(f"Downloaded {source}: {len(data)} bytes -> {out_path}")
        except Exception as e:
            print(f"Skip {source}: {e}", file=sys.stderr)

    manifest_path = out_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2))
    print(f"\nSaved {downloaded} images to {out_dir}")
    print(f"Manifest: {manifest_path}")


if __name__ == "__main__":
    main()
