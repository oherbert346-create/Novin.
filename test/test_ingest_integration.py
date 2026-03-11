"""
Integration tests for the universal ingest API using real dataset images.
Tests canonical, frame, Wyze, and Frigate ingest formats.
"""

from __future__ import annotations

import base64
import json
import uuid
from datetime import datetime
from pathlib import Path
import pytest

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures" / "images"
INGEST_FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures" / "ingest"
MANIFEST_PATH = FIXTURES_DIR / "manifest.json"


def _load_manifest() -> list[dict]:
    if not MANIFEST_PATH.exists():
        pytest.skip(f"Manifest not found. Run: python scripts/download_dataset_images.py")
    return json.loads(MANIFEST_PATH.read_text())


def _get_first_fixture() -> tuple[str, str]:
    """Return (path, b64) for first available fixture image."""
    manifest = _load_manifest()
    if not manifest:
        pytest.skip("No fixtures in manifest")
    item = manifest[0]
    path = Path(item["path"])
    if not path.exists():
        pytest.skip(f"Fixture image not found: {path}")
    b64 = base64.b64encode(path.read_bytes()).decode("ascii")
    return str(path), b64


def _get_fixture_with_url() -> tuple[str, str, str]:
    """Return (path, b64, url) for a fixture that has a public URL (COCO)."""
    manifest = _load_manifest()
    for item in manifest:
        if item.get("url", "").startswith("http://images.cocodataset.org"):
            path = Path(item["path"])
            if path.exists():
                b64 = base64.b64encode(path.read_bytes()).decode("ascii")
                return str(path), b64, item["url"]
    pytest.skip("No COCO fixture with URL found")


def _assert_public_verdict_shape(data: dict) -> None:
    assert "risk_level" in data
    assert "action" in data
    assert "visibility_policy" in data
    assert "notification_policy" in data
    assert "storage_policy" in data
    assert "summary" in data
    assert "narrative_summary" in data
    assert "decision_reason" in data
    assert "agent_outputs" in data
    assert "reasoning_degraded" in data
    assert "case" in data
    assert "case_id" in data
    assert "case_status" in data
    assert "ambiguity_state" in data
    assert "confidence_band" in data
    assert "consumer_summary" in data
    assert "operator_summary" in data
    assert "evidence_digest" in data
    assert "recommended_next_action" in data
    assert "recommended_delivery_targets" in data
    assert isinstance(data["agent_outputs"], list)
    assert isinstance(data["case"], dict)
    assert isinstance(data["consumer_summary"], dict)
    assert isinstance(data["operator_summary"], dict)
    assert isinstance(data["evidence_digest"], list)
    assert isinstance(data["recommended_delivery_targets"], list)
    assert "final_confidence" not in data
    assert data["risk_level"] in ("none", "low", "medium", "high")
    assert data["action"] in ("alert", "suppress")
    assert data["case_status"] in ("routine", "interesting", "watch", "verify", "urgent", "active_threat", "closed_benign")
    assert data["ambiguity_state"] in ("resolved", "monitoring", "ambiguous", "contested")
    assert data["confidence_band"] in ("low", "medium", "high")
    assert isinstance(data["reasoning_degraded"], bool)
    if data["reasoning_degraded"] is False:
        assert all(not output["rationale"].startswith("Agent fallback:") for output in data["agent_outputs"])
    assert data["case_id"]
    assert data["consumer_summary"]["headline"]
    assert data["consumer_summary"]["reason"]
    assert data["consumer_summary"]["action_now"]
    assert data["operator_summary"]["what_observed"]
    assert data["operator_summary"]["why_flagged"]
    assert data["operator_summary"]["why_not_benign"]
    assert data["operator_summary"]["what_is_uncertain"]
    assert data["operator_summary"]["timeline_context"]
    assert data["operator_summary"]["recommended_next_step"]
    assert len(data["evidence_digest"]) >= 3


def _post_until_live(client, path: str, *, headers: dict, json_payload, attempts: int = 2):
    """Retry once for live-provider flakes; final response is returned either way."""
    response = None
    first_verdict_response = None
    payload = json.loads(json.dumps(json_payload))
    for attempt in range(attempts):
        if attempt > 0 and headers.get("X-Source") == "frigate" and isinstance(payload, dict):
            after = payload.get("after")
            if isinstance(after, dict) and after.get("id"):
                after["id"] = f"frigate_{uuid.uuid4().hex}"
        response = client.post(path, headers=headers, json=payload)
        data = response.json()
        if response.status_code == 200 and "action" in data and first_verdict_response is None:
            first_verdict_response = response
        if response.status_code == 200 and data.get("reasoning_degraded") is False:
            return response
        if response.status_code == 200 and data.get("status") == "duplicate" and first_verdict_response is not None:
            return first_verdict_response
    if first_verdict_response is not None:
        return first_verdict_response
    return response


class TestCanonicalIngest:
    """Test POST /api/novin/ingest with canonical JSON (image_b64, image_url)."""

    def test_canonical_ingest_b64_returns_verdict(self, client, ingest_headers):
        _, b64 = _get_first_fixture()
        payload = {
            "cam_id": "test_cam_1",
            "home_id": "home",
            "image_b64": b64,
            "zone": "front_door",
        }
        resp = client.post("/api/novin/ingest", json=payload, headers=ingest_headers)
        assert resp.status_code == 200
        data = resp.json()
        _assert_public_verdict_shape(data)
        assert "event_id" in data
        assert data["summary"]

    def test_canonical_ingest_url_returns_verdict(self, client, ingest_headers):
        """Canonical with image_url — real URL fetch (COCO)."""
        _, _, url = _get_fixture_with_url()
        payload = {
            "cam_id": "test_cam_url",
            "home_id": "home",
            "image_url": url,
            "zone": "front_door",
        }
        resp = client.post("/api/novin/ingest", json=payload, headers=ingest_headers)
        assert resp.status_code == 200
        data = resp.json()
        _assert_public_verdict_shape(data)

    def test_canonical_ingest_rejects_missing_image(self, client, ingest_headers):
        payload = {"cam_id": "cam1", "home_id": "home"}
        resp = client.post("/api/novin/ingest", json=payload, headers=ingest_headers)
        assert resp.status_code == 422

    def test_canonical_ingest_rejects_missing_api_key(self, client):
        _, b64 = _get_first_fixture()
        resp = client.post(
            "/api/novin/ingest",
            json={"cam_id": "c1", "image_b64": b64},
            headers={"Content-Type": "application/json"},
        )
        assert resp.status_code == 401


class TestFrameIngest:
    """Test POST /api/novin/ingest/frame (legacy b64 frame)."""

    def test_frame_ingest_returns_verdict(self, client, ingest_headers):
        _, b64 = _get_first_fixture()
        payload = {
            "b64_frame": b64,
            "stream_id": "test_stream",
            "label": "Test Camera",
            "site_id": "home",
            "zone": "front_door",
        }
        resp = client.post("/api/novin/ingest/frame", json=payload, headers=ingest_headers)
        assert resp.status_code == 200
        data = resp.json()
        _assert_public_verdict_shape(data)


class TestWyzeIngest:
    """Test POST /api/novin/ingest with X-Source: wyze (X-Attach URL)."""

    def test_wyze_ingest_with_real_url(self, client, ingest_headers):
        """Wyze adapter needs image URL; real fetch from COCO."""
        _, _, url = _get_fixture_with_url()
        h = dict(ingest_headers)
        h["X-Source"] = "wyze"
        h["X-Camera"] = "wyze_front_door"
        h["X-Attach"] = url
        h["X-Event"] = "motion"
        resp = _post_until_live(
            client,
            "/api/novin/ingest",
            headers=h,
            json_payload="Motion detected on cam at 12:00:00",
        )
        assert resp.status_code == 200
        data = resp.json()
        if data.get("reasoning_degraded") is True:
            pytest.skip("Live provider degraded after retry")
        _assert_public_verdict_shape(data)


class TestFrigateIngest:
    """Test POST /api/novin/ingest with X-Source: frigate."""

    def test_frigate_ingest_with_image_b64(self, client, ingest_headers):
        """Frigate adapter accepts image_b64 in body."""
        _, b64 = _get_first_fixture()
        payload = {
            "type": "end",
            "after": {
                "id": f"frigate_{uuid.uuid4().hex}",
                "camera": "front_door",
                "label": "person",
                "start_time": 1700000000,
            },
            "image_b64": b64,
        }
        h = dict(ingest_headers)
        h["X-Source"] = "frigate"
        resp = _post_until_live(
            client,
            "/api/novin/ingest",
            headers=h,
            json_payload=payload,
        )
        assert resp.status_code == 200
        data = resp.json()
        if data.get("reasoning_degraded") is True:
            pytest.skip("Live provider degraded after retry")
        _assert_public_verdict_shape(data)

    def test_frigate_ingest_with_image_url(self, client, ingest_headers):
        """Frigate adapter accepts image_url in body."""
        _, _, url = _get_fixture_with_url()
        payload = {
            "type": "end",
            "after": {
                "id": f"frigate_{uuid.uuid4().hex}",
                "camera": "front_door",
                "label": "person",
                "start_time": 1700000000,
            },
            "image_url": url,
        }
        h = dict(ingest_headers)
        h["X-Source"] = "frigate"
        resp = _post_until_live(
            client,
            "/api/novin/ingest",
            headers=h,
            json_payload=payload,
        )
        assert resp.status_code == 200
        data = resp.json()
        _assert_public_verdict_shape(data)


class TestIngestAgentOutputs:
    """Verify ingest returns full agent_outputs and summary (not empty fallbacks)."""

    def test_canonical_returns_agent_outputs_and_summary(self, client, ingest_headers):
        """Response must include public agent_outputs and summary with real content."""
        from unittest.mock import AsyncMock, patch

        from backend.models.schemas import (
            AgentOutput,
            AuditTrail,
            LiabilityDigest,
            MachineRouting,
            OperatorSummary,
            Verdict,
        )
        from datetime import datetime

        def _mock_v(frame, stream_meta, db, groq_client, event_id=None, event_context=None):
            eid = event_id or "test-eid"
            return Verdict(
                frame_id=eid,
                event_id=eid,
                stream_id=stream_meta.stream_id,
                site_id=stream_meta.site_id,
                timestamp=datetime.utcnow(),
                routing=MachineRouting(
                    is_threat=False,
                    action="suppress",
                    risk_level="low",
                    severity="none",
                    categories=["person"],
                    visibility_policy="timeline",
                    notification_policy="none",
                    storage_policy="timeline",
                ),
                summary=OperatorSummary(
                    headline="Test headline: routine activity at 75% confidence.",
                    narrative="Test narrative with agent consensus.",
                ),
                audit=AuditTrail(
                    liability_digest=LiabilityDigest(
                        decision_reasoning="Test reasoning",
                        confidence_score=0.75,
                    ),
                    agent_outputs=[
                        AgentOutput(
                            agent_id="executive_triage_commander",
                            role="Executive Triage",
                            verdict="suppress",
                            confidence=0.75,
                            rationale="Test rationale from agent.",
                            chain_notes={},
                        ),
                    ],
                ),
                description="test",
                bbox=[],
                b64_thumbnail="",
            )

        async def _mock_pf(frame, stream_meta, db, groq_client, event_id=None, event_context=None):
            return _mock_v(frame, stream_meta, db, groq_client, event_id, event_context)

        _, b64 = _get_first_fixture()
        with patch("backend.agent.pipeline.process_frame", AsyncMock(side_effect=_mock_pf)):
            resp = client.post(
                "/api/novin/ingest",
                json={"cam_id": "c1", "image_b64": b64},
                headers=ingest_headers,
            )
        assert resp.status_code == 200
        data = resp.json()
        _assert_public_verdict_shape(data)
        assert len(data["agent_outputs"]) >= 1
        assert data["agent_outputs"][0]["agent_id"] == "executive_triage_commander"
        assert data["agent_outputs"][0]["rationale"]

    def test_alert_webhook_carries_event_context(self, client, ingest_headers, monkeypatch):
        """Webhook payload should preserve canonical event context end to end."""
        from unittest.mock import AsyncMock, patch

        from backend.config import settings
        from backend.models.schemas import (
            AgentOutput,
            AuditTrail,
            EventContext,
            LiabilityDigest,
            MachineRouting,
            OperatorSummary,
            Verdict,
        )

        captured = {}

        class DummyResponse:
            def raise_for_status(self):
                return None

        async def fake_post(self, url, json):
            captured["url"] = url
            captured["json"] = json
            return DummyResponse()

        def _mock_v(frame, stream_meta, db, groq_client, event_id=None, event_context=None):
            eid = event_id or "test-eid"
            return Verdict(
                frame_id=eid,
                event_id=eid,
                stream_id=stream_meta.stream_id,
                site_id=stream_meta.site_id,
                timestamp=datetime.utcnow(),
                routing=MachineRouting(
                    is_threat=True,
                    action="alert",
                    risk_level="high",
                    severity="high",
                    categories=["person", "intrusion"],
                    visibility_policy="prominent",
                    notification_policy="immediate",
                    storage_policy="full",
                ),
                summary=OperatorSummary(
                    headline="Visitor approaching side gate.",
                    narrative="Agents agreed this event should be escalated.",
                ),
                audit=AuditTrail(
                    liability_digest=LiabilityDigest(
                        decision_reasoning="Alert due to approach toward restricted side gate.",
                        confidence_score=0.81,
                    ),
                    agent_outputs=[
                        AgentOutput(
                            agent_id="executive_triage_commander",
                            role="Executive Triage",
                            verdict="alert",
                            confidence=0.81,
                            rationale="Approach path and zone context support alerting.",
                            chain_notes={},
                        ),
                    ],
                ),
                description="single person approaching side gate",
                bbox=[],
                b64_thumbnail="",
                event_context=event_context or EventContext(source="canonical"),
            )

        async def _mock_pf(frame, stream_meta, db, groq_client, event_id=None, event_context=None):
            return _mock_v(frame, stream_meta, db, groq_client, event_id, event_context)

        _, b64 = _get_first_fixture()
        monkeypatch.setattr(settings, "webhook_url", "https://example.test/novin")

        async def fake_dispatch(verdict):
            captured["stream_id"] = verdict.stream_id
            captured["site_id"] = verdict.site_id
            captured["event_context"] = verdict.event_context

        src_event_id = f"evt-{uuid.uuid4().hex}"
        with patch("backend.agent.pipeline.process_frame", AsyncMock(side_effect=_mock_pf)), \
             patch("backend.notifications.notifier.dispatch", new=fake_dispatch):
            resp = client.post(
                "/api/novin/ingest",
                json={
                    "cam_id": "side_gate_cam",
                    "home_id": "home-west",
                    "zone": "backyard",
                    "label": "person",
                    "source": "custom_nvr",
                    "source_event_id": src_event_id,
                    "metadata": {"delivery_window": "none", "origin": "webhook-test"},
                    "image_b64": b64,
                },
                headers=ingest_headers,
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["event_context"]["source_event_id"] == src_event_id
        assert data["event_context"]["cam_id"] == "side_gate_cam"
        assert data["event_context"]["home_id"] == "home-west"
        assert data["event_context"]["ingest_mode"] == "webhook"
        # Confirm notifier was called with correct verdict context
        assert captured["stream_id"] == "side_gate_cam"
        assert captured["site_id"] == "home-west"
        assert captured["event_context"].cam_id == "side_gate_cam"
        assert captured["event_context"].home_id == "home-west"


class TestRealWorldIngest:
    """Tests using real-world event payloads from Frigate, Wyze, and canonical sources."""

    def test_frigate_real_payload_parses(self, client, ingest_headers):
        """Frigate MQTT event format (before/after, type) parses and returns verdict."""
        fixture_path = INGEST_FIXTURES_DIR / "frigate_event_end.json"
        if not fixture_path.exists():
            pytest.skip(f"Fixture not found: {fixture_path}")
        payload = json.loads(fixture_path.read_text())
        # Use unique id to avoid idempotency duplicate when tests run in sequence
        payload["after"]["id"] = f"frigate_real_{uuid.uuid4().hex}"
        _, b64 = _get_first_fixture()
        payload["image_b64"] = b64

        h = dict(ingest_headers)
        h["X-Source"] = "frigate"
        resp = client.post("/api/novin/ingest", json=payload, headers=h)
        assert resp.status_code == 200
        data = resp.json()
        if data.get("status") == "duplicate":
            pytest.skip("Idempotency hit (rare); rerun test")
        _assert_public_verdict_shape(data)
        assert "event_id" in data
        assert data.get("stream_id") == "front_door"

    def test_frigate_entered_zones_fallback(self, client, ingest_headers):
        """Frigate with entered_zones only (no current_zones) uses entered_zones[0] for zone."""
        fixture_path = INGEST_FIXTURES_DIR / "frigate_entered_zones_only.json"
        if not fixture_path.exists():
            pytest.skip(f"Fixture not found: {fixture_path}")
        payload = json.loads(fixture_path.read_text())
        payload["after"]["id"] = f"frigate_zones_{uuid.uuid4().hex}"
        _, b64 = _get_first_fixture()
        payload["image_b64"] = b64

        h = dict(ingest_headers)
        h["X-Source"] = "frigate"
        resp = client.post("/api/novin/ingest", json=payload, headers=h)
        assert resp.status_code == 200
        data = resp.json()
        if data.get("status") == "duplicate":
            pytest.skip("Idempotency hit (rare); rerun test")
        _assert_public_verdict_shape(data)
        assert data.get("stream_id") == "driveway"

    def test_wyze_real_headers_and_body(self, client, ingest_headers):
        """Wyze Bridge webhook with real headers and body format."""
        fixture_path = INGEST_FIXTURES_DIR / "wyze_motion.txt"
        if not fixture_path.exists():
            pytest.skip(f"Fixture not found: {fixture_path}")
        body = fixture_path.read_text()
        _, _, url = _get_fixture_with_url()

        h = dict(ingest_headers)
        h["X-Source"] = "wyze"
        h["X-Camera"] = "Front Door"
        h["X-Attach"] = url
        h["X-Event"] = "motion"
        resp = client.post("/api/novin/ingest", headers=h, json=body)
        assert resp.status_code == 200
        data = resp.json()
        _assert_public_verdict_shape(data)

    def test_wyze_rejects_missing_x_attach(self, client, ingest_headers):
        """Wyze adapter requires X-Attach header; missing it returns 422."""
        fixture_path = INGEST_FIXTURES_DIR / "wyze_motion.txt"
        if not fixture_path.exists():
            pytest.skip(f"Fixture not found: {fixture_path}")
        body = fixture_path.read_text()

        h = dict(ingest_headers)
        h["X-Source"] = "wyze"
        h["X-Camera"] = "Front Door"
        h["X-Event"] = "motion"
        # No X-Attach
        resp = client.post("/api/novin/ingest", headers=h, json=body)
        assert resp.status_code == 422

    def test_canonical_real_minimal(self, client, ingest_headers):
        """Minimal canonical payload with image_b64 injected."""
        fixture_path = INGEST_FIXTURES_DIR / "canonical_minimal.json"
        if not fixture_path.exists():
            pytest.skip(f"Fixture not found: {fixture_path}")
        payload = json.loads(fixture_path.read_text())
        _, b64 = _get_first_fixture()
        payload["image_b64"] = b64

        resp = client.post("/api/novin/ingest", json=payload, headers=ingest_headers)
        assert resp.status_code == 200
        data = resp.json()
        _assert_public_verdict_shape(data)
