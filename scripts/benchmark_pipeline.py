#!/usr/bin/env python3
"""
Pipeline benchmark — measure latency (ingest → verdict) for production.
Reports p50, p95, p99 per frame. Uses real Groq.

  PYTHONPATH=. uv run python scripts/benchmark_pipeline.py [--n 5]
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import statistics
import sys
import time
from pathlib import Path

os.environ.setdefault("INGEST_ASYNC_DEFAULT", "false")
os.environ.setdefault("INGEST_API_KEY", "bench-key")
os.environ.setdefault("DB_URL", "sqlite+aiosqlite:///./bench.db")

FIXTURES = Path(__file__).resolve().parent.parent / "test" / "fixtures" / "images"
MANIFEST = FIXTURES / "manifest.json"


def load_fixtures(n: int) -> list[tuple[str, str]]:
    """List of (path, b64) for up to n COCO fixtures."""
    if not MANIFEST.exists():
        print("ERROR: Run scripts/download_dataset_images.py first")
        sys.exit(2)
    data = json.loads(MANIFEST.read_text())
    out = []
    for item in data:
        if len(out) >= n:
            break
        if "coco" in item.get("source", ""):
            p = Path(item["path"])
            if p.exists():
                out.append((str(p), base64.b64encode(p.read_bytes()).decode("ascii")))
    return out


def run_benchmark(n: int) -> int:
    from fastapi.testclient import TestClient

    from backend.main import app

    fixtures = load_fixtures(n)
    if not fixtures:
        print("ERROR: No fixtures")
        return 2

    headers = {"x-api-key": "bench-key", "Content-Type": "application/json", "x-novin-benchmark": "on"}
    latencies_ms: list[float] = []

    print("=" * 60)
    print(f"PIPELINE BENCHMARK — {n} frames, real Groq")
    print("=" * 60)

    with TestClient(app) as client:
        for i, (_, b64) in enumerate(fixtures):
            t0 = time.monotonic()
            r = client.post(
                "/api/novin/ingest",
                json={"cam_id": "bench", "image_b64": b64},
                headers=headers,
            )
            elapsed_ms = (time.monotonic() - t0) * 1000
            if r.status_code == 200:
                latencies_ms.append(elapsed_ms)
                data = r.json()
                action = data.get("routing", {}).get("action", "?")
                bt = data.get("benchmark_telemetry", {})
                tokens = bt.get("vision_total_tokens", 0) + bt.get("reasoning_total_tokens", 0) if bt else 0
                print(f"  frame {i+1}/{n}: {elapsed_ms:.0f}ms action={action} tokens={tokens}")
            else:
                print(f"  frame {i+1}/{n}: FAIL {r.status_code}")

    if not latencies_ms:
        print("\nNo successful runs")
        return 1

    latencies_ms.sort()
    p50 = latencies_ms[len(latencies_ms) * 50 // 100] if latencies_ms else 0
    p95 = latencies_ms[len(latencies_ms) * 95 // 100] if latencies_ms else 0
    p99 = latencies_ms[len(latencies_ms) * 99 // 100] if latencies_ms else 0

    print("\n" + "-" * 40)
    print("LATENCY (ms)")
    print(f"  p50: {p50:.0f}")
    print(f"  p95: {p95:.0f}")
    print(f"  p99: {p99:.0f}")
    print(f"  mean: {statistics.mean(latencies_ms):.0f}")
    print(f"  n: {len(latencies_ms)}")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--n", type=int, default=3, help="Number of frames to benchmark")
    args = p.parse_args()
    sys.exit(run_benchmark(args.n))
