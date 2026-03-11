from __future__ import annotations

import base64
import json
import os
import time
from pathlib import Path

import pytest


FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures" / "images"
MANIFEST_PATH = FIXTURES_DIR / "manifest.json"


pytestmark = pytest.mark.skipif(
    os.getenv("LIVE_MODEL_TESTS") != "1",
    reason="Set LIVE_MODEL_TESTS=1 to run live model smoke tests.",
)


def _load_manifest() -> list[dict]:
    if not MANIFEST_PATH.exists():
        pytest.skip(f"Manifest not found: {MANIFEST_PATH}")
    return json.loads(MANIFEST_PATH.read_text())


def _fixture_b64() -> str:
    manifest = _load_manifest()
    path = Path(manifest[0]["path"])
    if not path.exists():
        pytest.skip(f"Fixture image not found: {path}")
    return base64.b64encode(path.read_bytes()).decode("ascii")


def _fixture_url() -> str:
    for item in _load_manifest():
        url = item.get("url", "")
        if url.startswith("http://images.cocodataset.org"):
            return url
    pytest.skip("No public COCO fixture URL available")


def _assert_live_verdict(data: dict) -> None:
    assert data["action"] in ("alert", "suppress")
    assert data["summary"]
    assert data["narrative_summary"]
    assert data["reasoning_degraded"] is False
    assert len(data["agent_outputs"]) == 4
    assert all(not output["rationale"].startswith("Agent fallback:") for output in data["agent_outputs"])


class TestLiveModelSmoke:
    def test_canonical_b64_live_reasoning(self, client, ingest_headers):
        payload = {
            "cam_id": "live_smoke_b64",
            "home_id": "home",
            "image_b64": _fixture_b64(),
            "zone": "front_door",
        }

        started = time.perf_counter()
        resp = client.post("/api/novin/ingest", json=payload, headers=ingest_headers)
        elapsed = time.perf_counter() - started

        assert resp.status_code == 200
        data = resp.json()
        _assert_live_verdict(data)
        assert elapsed < 20.0

    def test_canonical_url_live_reasoning(self, client, ingest_headers):
        payload = {
            "cam_id": "live_smoke_url",
            "home_id": "home",
            "image_url": _fixture_url(),
            "zone": "front_door",
        }

        resp = client.post("/api/novin/ingest", json=payload, headers=ingest_headers)
        assert resp.status_code == 200
        _assert_live_verdict(resp.json())

    def test_repeated_runs_stay_live(self, client, ingest_headers):
        payload = {
            "cam_id": "live_smoke_repeat",
            "home_id": "home",
            "image_b64": _fixture_b64(),
            "zone": "front_door",
        }

        for _ in range(3):
            resp = client.post("/api/novin/ingest", json=payload, headers=ingest_headers)
            assert resp.status_code == 200
            _assert_live_verdict(resp.json())

    def test_status_reports_reasoning_live_after_ingest(self, client, ingest_headers):
        payload = {
            "cam_id": "live_smoke_status",
            "home_id": "home",
            "image_b64": _fixture_b64(),
            "zone": "front_door",
        }

        ingest_resp = client.post("/api/novin/ingest", json=payload, headers=ingest_headers)
        assert ingest_resp.status_code == 200
        _assert_live_verdict(ingest_resp.json())

        status_resp = client.get("/api/status", headers=ingest_headers)
        assert status_resp.status_code == 200
        status = status_resp.json()
        assert status["reasoning_live"] is True
        assert status["reasoning_degraded"] is False
        assert status["reasoning_provider"] in ("groq", "cerebras", "siliconflow", "together")
