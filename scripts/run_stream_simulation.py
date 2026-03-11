#!/usr/bin/env python3
"""
Simulate a stream by pushing multiple images (paths or URLs) through the ingest API.
Shows verdicts and token usage per frame.

Usage:
  PYTHONPATH=. uv run python scripts/run_stream_simulation.py [--delay 2] [--limit 3] <path_or_url> ...
  PYTHONPATH=. uv run python scripts/run_stream_simulation.py datasets/pipeline_test_samples/*.jpg

Default: 3 images from pipeline_test_samples if no args.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
from pathlib import Path

os.environ.setdefault("INGEST_ASYNC_DEFAULT", "false")
os.environ.setdefault("INGEST_API_KEY", "test-ingest-key")
os.environ.setdefault("DB_URL", "sqlite+aiosqlite:///./demo_novin.db")

logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("backend").setLevel(logging.WARNING)


def _is_url(s: str) -> bool:
    return s.startswith("http://") or s.startswith("https://")


def main() -> int:
    parser = argparse.ArgumentParser(description="Simulate stream by pushing images through ingest")
    parser.add_argument("--delay", type=float, default=0, help="Seconds between frames")
    parser.add_argument("--limit", type=int, default=0, help="Max frames to process (0=all)")
    parser.add_argument("sources", nargs="*", help="Image paths or URLs")
    args = parser.parse_args()

    if not args.sources:
        root = Path(__file__).resolve().parents[1]
        default = [
            str(root / "datasets/pipeline_test_samples/1006_jpg.rf.e873527d4d76f54db2d0165df1449705.jpg"),
            str(root / "datasets/pipeline_test_samples/17_jpg.rf.6fc3c4ff0a05642c38c7b89f5f55af78.jpg"),
            str(root / "datasets/pipeline_test_samples/avenue_01.jpg"),
        ]
        args.sources = default

    import httpx
    from fastapi.testclient import TestClient

    from backend.main import app

    headers = {
        "x-api-key": "test-ingest-key",
        "Content-Type": "application/json",
        "x-novin-benchmark": "on",
    }

    print("=" * 70)
    print("STREAM SIMULATION — pushing frames through ingest API")
    print("=" * 70)

    total_tokens = 0
    with TestClient(app) as client:
        for i, src in enumerate(args.sources):
            if args.limit and i >= args.limit:
                break
            if args.delay and i > 0:
                import time
                time.sleep(args.delay)

            if _is_url(src):
                payload = {"cam_id": "sim_cam", "home_id": "home", "image_url": src, "zone": "driveway"}
            else:
                path = Path(src)
                if not path.exists():
                    print(f"SKIP {src}: not found")
                    continue
                b64 = path.read_bytes()
                import base64
                payload = {
                    "cam_id": "sim_cam",
                    "home_id": "home",
                    "image_b64": base64.b64encode(b64).decode("utf-8"),
                    "zone": "driveway",
                }

            print(f"\n--- Frame {i + 1}: {Path(src).name if not _is_url(src) else src[:60]}... ---")
            resp = client.post("/api/novin/ingest", json=payload, headers=headers)
            if resp.status_code != 200:
                print(f"  ERROR HTTP {resp.status_code}: {resp.text[:200]}")
                continue

            data = resp.json()
            action = data.get("action") or data.get("routing", {}).get("action", "?")
            summary = data.get("summary")
            if isinstance(summary, dict):
                summary = summary.get("headline", "")
            else:
                summary = str(summary or "")[:80]
            print(f"  action: {action} | {str(summary)[:60]}")

            bt = data.get("benchmark_telemetry", {})
            if bt:
                vt = bt.get("vision_total_tokens", 0) or 0
                rt = bt.get("reasoning_total_tokens", 0) or 0
                frame_tok = vt + rt
                total_tokens += frame_tok
                print(f"  tokens: vision={vt} reasoning={rt} frame_total={frame_tok}")

    print("\n" + "=" * 70)
    n = min(len(args.sources), args.limit) if args.limit else len(args.sources)
    print(f"Frames processed: {n}")
    print(f"Total tokens: {total_tokens}")
    print("=" * 70)
    return 0


if __name__ == "__main__":
    sys.exit(main())
