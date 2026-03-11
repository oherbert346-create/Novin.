#!/usr/bin/env python3
"""
Production smoke test — full pipeline.
Run before release. Exits 0 if all pass, 1 if any fail.

  PYTHONPATH=. uv run python scripts/release_smoke_test.py        # real configured providers
  PYTHONPATH=. uv run python scripts/release_smoke_test.py --mock  # no Groq (CI)

Requires: provider API keys for the configured vision/reasoning stack (unless --mock), INGEST_API_KEY
"""

from __future__ import annotations

import base64
import json
import os
import sys
import time
from pathlib import Path

# Production-like env
os.environ.setdefault("INGEST_ASYNC_DEFAULT", "false")
os.environ.setdefault("INGEST_API_KEY", os.environ.get("INGEST_API_KEY", "release-test-key"))
os.environ.setdefault("DB_URL", "sqlite+aiosqlite:///./release_smoke.db")

FIXTURES = Path(__file__).resolve().parent.parent / "test" / "fixtures" / "images"
MANIFEST = FIXTURES / "manifest.json"
REAL_URL = "http://images.cocodataset.org/val2017/000000000139.jpg"


def load_fixture() -> tuple[str, str]:
    """(path, b64) for first COCO fixture."""
    if not MANIFEST.exists():
        print("ERROR: Run scripts/download_dataset_images.py first")
        sys.exit(2)
    data = json.loads(MANIFEST.read_text())
    for item in data:
        if "coco" in item.get("source", ""):
            p = Path(item["path"])
            if p.exists():
                return str(p), base64.b64encode(p.read_bytes()).decode("ascii")
    print("ERROR: No COCO fixtures found")
    sys.exit(2)


def run_smoke(use_mock: bool = False) -> int:
    from unittest.mock import AsyncMock, patch

    from fastapi.testclient import TestClient

    from backend.main import app
    from backend.config import settings

    headers = {"x-api-key": "release-test-key", "Content-Type": "application/json"}

    if not use_mock:
        missing = []
        if settings.vision_provider == "siliconflow" and not settings.siliconflow_api_key:
            missing.append("SILICONFLOW_API_KEY")
        elif settings.vision_provider == "together" and not settings.together_api_key:
            missing.append("TOGETHER_API_KEY")
        elif settings.vision_provider == "groq" and not settings.groq_api_key:
            missing.append("GROQ_API_KEY")

        if settings.reasoning_provider == "cerebras" and not settings.cerebras_api_key:
            missing.append("CEREBRAS_API_KEY")
        elif settings.reasoning_provider == "siliconflow" and not settings.siliconflow_api_key:
            missing.append("SILICONFLOW_API_KEY")
        elif settings.reasoning_provider == "together" and not settings.together_api_key:
            missing.append("TOGETHER_API_KEY")
        elif settings.reasoning_provider == "groq" and not settings.groq_api_key:
            missing.append("GROQ_API_KEY")

        if missing:
            print("ERROR: Missing provider credentials for configured stack:", ", ".join(sorted(set(missing))))
            return 2

    if use_mock:
        from backend.models.schemas import (
            AgentOutput,
            AuditTrail,
            LiabilityDigest,
            MachineRouting,
            OperatorSummary,
            Verdict,
        )
        from datetime import datetime

        _smoke_counter = [0]

        def _mock_v(frame, stream_meta, db, groq_client, event_id=None):
            _smoke_counter[0] += 1
            eid = event_id or f"smoke-eid-{_smoke_counter[0]}"
            return Verdict(
                frame_id=eid,
                event_id=eid,
                stream_id=stream_meta.stream_id,
                site_id=stream_meta.site_id,
                timestamp=datetime.utcnow(),
                routing=MachineRouting(is_threat=False, action="suppress", severity="none", categories=["person"]),
                summary=OperatorSummary(headline="Smoke test", narrative="Mock"),
                audit=AuditTrail(
                    liability_digest=LiabilityDigest(decision_reasoning="mock", confidence_score=0.7),
                    agent_outputs=[AgentOutput(agent_id="x", role="x", verdict="suppress", confidence=0.7, rationale="mock", chain_notes={})],
                ),
                description="mock",
                bbox=[],
                b64_thumbnail="",
            )

        async def _mock_pf(frame, stream_meta, db, groq_client, event_id=None):
            return _mock_v(frame, stream_meta, db, groq_client, event_id)

        patch_ctx = patch("backend.agent.pipeline.process_frame", AsyncMock(side_effect=_mock_pf))
    else:
        from contextlib import nullcontext

        patch_ctx = nullcontext()
    failures = []

    def _post(path: str, **kwargs) -> dict:
        with TestClient(app) as client:
            r = client.post(path, headers=headers, **kwargs)
        if r.status_code != 200:
            failures.append((path, r.status_code, r.text[:200]))
            return {}
        return r.json()

    print("=" * 60)
    print("RELEASE SMOKE TEST — full pipeline" + (" (mock)" if use_mock else " (real providers)"))
    print("=" * 60)

    _, b64 = load_fixture()

    with patch_ctx:
        # 1. Canonical image_b64
        print("\n1. Canonical (image_b64)...")
        t0 = time.monotonic()
        data = _post("/api/novin/ingest", json={"cam_id": "smoke", "image_b64": b64})
        elapsed = (time.monotonic() - t0) * 1000
        if data and "routing" in data:
            print(f"   PASS ({elapsed:.0f}ms) action={data['routing']['action']}")
            if data.get("audit", {}).get("agent_outputs"):
                print(f"   agents={len(data['audit']['agent_outputs'])} summary={bool(data.get('summary', {}).get('headline'))}")
        else:
            print(f"   FAIL")

        # 2. Canonical image_url (real URL fetch)
        print("\n2. Canonical (image_url)...")
        t0 = time.monotonic()
        data = _post("/api/novin/ingest", json={"cam_id": "smoke", "image_url": REAL_URL})
        elapsed = (time.monotonic() - t0) * 1000
        if data and "routing" in data:
            print(f"   PASS ({elapsed:.0f}ms) action={data['routing']['action']}")
        else:
            print(f"   FAIL")

        # 3. Frame ingest
        print("\n3. Frame ingest...")
        t0 = time.monotonic()
        data = _post("/api/novin/ingest/frame", json={
            "b64_frame": b64,
            "stream_id": "smoke",
            "label": "smoke",
            "site_id": "home",
            "zone": "front_door",
        })
        elapsed = (time.monotonic() - t0) * 1000
        if data and "routing" in data:
            print(f"   PASS ({elapsed:.0f}ms) action={data['routing']['action']}")
        else:
            print(f"   FAIL")

        # 4. Wyze (X-Attach URL)
        print("\n4. Wyze (X-Attach URL)...")
        h = dict(headers)
        h["X-Source"], h["X-Camera"], h["X-Attach"], h["X-Event"] = "wyze", "wyze_smoke", REAL_URL, "motion"
        t0 = time.monotonic()
        with TestClient(app) as c:
            r = c.post("/api/novin/ingest", headers=h, json="Motion at 12:00:00")
        elapsed = (time.monotonic() - t0) * 1000
        if r.status_code == 200 and (d := r.json()) and "routing" in d:
            print(f"   PASS ({elapsed:.0f}ms) action={d['routing']['action']}")
        else:
            failures.append(("wyze", r.status_code, r.text[:200]))
            print(f"   FAIL")

        # 5. Frigate (image_b64)
        print("\n5. Frigate (image_b64)...")
        h = dict(headers)
        h["X-Source"] = "frigate"
        payload = {"type": "end", "after": {"id": "smoke_frigate_1", "camera": "front", "label": "person"}, "image_b64": b64}
        t0 = time.monotonic()
        with TestClient(app) as c:
            r = c.post("/api/novin/ingest", json=payload, headers=h)
        elapsed = (time.monotonic() - t0) * 1000
        if r.status_code == 200 and (d := r.json()) and "routing" in d:
            print(f"   PASS ({elapsed:.0f}ms) action={d['routing']['action']}")
        else:
            failures.append(("frigate", r.status_code, r.text[:200]))
            print(f"   FAIL")

        # 6. Credential rejection
        print("\n6. Auth (reject missing key)...")
        with TestClient(app) as c:
            r = c.post("/api/novin/ingest", json={"cam_id": "x", "image_b64": b64})
        if r.status_code == 401:
            print("   PASS (401 as expected)")
        else:
            failures.append(("auth", r.status_code, "expected 401"))
            print(f"   FAIL (got {r.status_code})")

    print("\n" + "=" * 60)
    if failures:
        print(f"FAILED: {len(failures)}")
        for path, code, msg in failures:
            print(f"  {path}: {code} {msg}")
        return 1
    print("ALL PASS — ready for release")
    return 0


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--mock", action="store_true", help="Use mock pipeline (no Groq)")
    args = p.parse_args()
    sys.exit(run_smoke(use_mock=args.mock))
