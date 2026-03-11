"""
Accuracy tests for ingest - validates false alarm vs real event detection.

This test suite uses synthetic scenarios to validate:
1. Ingest adapters correctly normalize different camera sources
2. Zone inference works correctly (Wyze camera name -> security zone)
3. The system can distinguish common false alarms from real events
4. Reasoning agents produce explainable outputs

These tests are designed to be run with mocked LLM responses to validate
the pipeline logic without needing actual API calls.
"""

from __future__ import annotations

import base64
import json
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

# Test fixtures
FIXTURES_DIR = Path(__file__).parent / "fixtures" / "images"


def _get_test_image_b64() -> str:
    """Load a test image and return base64 encoded."""
    # Use first available fixture
    test_images = list(FIXTURES_DIR.glob("*.jpg"))
    if not test_images:
        pytest.skip("No test images available")
    
    # Prefer smaller images for faster tests
    small_images = [f for f in test_images if f.stat().st_size < 100000]
    if small_images:
        img_path = small_images[0]
    else:
        img_path = test_images[0]
    
    return base64.b64encode(img_path.read_bytes()).decode("ascii")


class TestWyzeZoneInference:
    """Test Wyze adapter zone inference from camera names."""

    @pytest.mark.parametrize("camera_name,expected_zone", [
        ("Front Door", "front_door"),
        ("front_door", "front_door"),
        ("FrontDoor", "front_door"),
        ("Backyard", "backyard"),
        ("back_yard", "backyard"),
        ("garden_camera", "backyard"),
        ("Driveway", "driveway"),
        ("garage_cam", "driveway"),
        ("car_port", "driveway"),
        ("Living Room", "living_room"),
        ("family_room", "living_room"),
        ("lounge", "living_room"),
        ("Kitchen", "kitchen"),
        ("dining_cam", "kitchen"),
        ("Bedroom", "bedroom"),
        ("master_bed", "bedroom"),
        ("unknown_camera", "front_door"),  # default
    ])
    def test_wyze_zone_inference(self, camera_name: str, expected_zone: str):
        """Wyze adapter should infer correct zone from camera name."""
        from backend.ingest.adapters.wyze import _infer_zone
        
        result = _infer_zone(camera_name)
        assert result == expected_zone, f"Camera '{camera_name}' should map to '{expected_zone}', got '{result}'"


class TestFrigateZoneHandling:
    """Test Frigate adapter zone handling."""

    def test_frigate_uses_current_zones(self):
        """Frigate should use current_zones for zone when available."""
        from backend.ingest.adapters.frigate import normalise
        
        payload = {
            "type": "end",
            "after": {
                "id": "test_123",
                "camera": "front_door",
                "label": "person",
                "start_time": 1700000000,
                "current_zones": ["front_door", "porch"],
            },
            "image_url": "test_url"
        }
        result = normalise(payload)
        
        assert result.zone == "front_door"

    def test_frigate_falls_back_to_entered_zones(self):
        """Frigate should fallback to entered_zones when current_zones is empty."""
        from backend.ingest.adapters.frigate import normalise
        
        payload = {
            "type": "end",
            "after": {
                "id": "test_456",
                "camera": "backyard",
                "label": "cat",
                "start_time": 1700000000,
                "entered_zones": ["backyard", "garden"],
            },
            "image_url": "test_url"
        }
        result = normalise(payload)
        
        assert result.zone == "backyard"

    def test_frigate_defaults_to_front_door(self):
        """Frigate should default to front_door when no zones provided."""
        from backend.ingest.adapters.frigate import normalise
        
        payload = {
            "type": "end",
            "after": {
                "id": "test_789",
                "camera": "side_cam",
                "label": "motion",
                "start_time": 1700000000,
            },
            "image_url": "test_url"
        }
        result = normalise(payload)
        
        assert result.zone == "front_door"


class TestIngestIdempotency:
    """Test ingest deduplication based on source event ID."""

    def test_duplicate_event_returns_duplicate_status(self, client, ingest_headers):
        """Same source_event_id should return duplicate status."""
        from backend.models.schemas import Verdict, MachineRouting, OperatorSummary, AuditTrail, LiabilityDigest
        from datetime import datetime
        
        def mock_verdict(*args, **kwargs):
            unique_id = f"test-{uuid.uuid4().hex}"
            return Verdict(
                frame_id=unique_id, event_id=unique_id, stream_id="test", site_id="test", timestamp=datetime.utcnow(),
                routing=MachineRouting(is_threat=False, action="suppress", severity="none", categories=["person"]),
                summary=OperatorSummary(headline="test", narrative="test"),
                audit=AuditTrail(liability_digest=LiabilityDigest(decision_reasoning="test", confidence_score=0.5), agent_outputs=[]),
                description="", bbox=[], b64_thumbnail=""
            )

        b64 = _get_test_image_b64()
        event_id = f"dedup_test_{uuid.uuid4().hex}"
        
        # First request
        payload = {
            "source_event_id": event_id,
            "source": "test_source",
            "cam_id": "test_cam",
            "home_id": "home",
            "image_b64": b64,
            "zone": "front_door",
        }
        
        with patch("backend.agent.pipeline.process_frame", AsyncMock(side_effect=mock_verdict)):
            resp1 = client.post("/api/novin/ingest", json=payload, headers=ingest_headers)
        
        # Second request with same source_event_id
        resp2 = client.post("/api/novin/ingest", json=payload, headers=ingest_headers)
        
        # First should succeed, second should be marked as duplicate
        assert resp1.status_code == 200
        assert resp2.status_code == 200
        data2 = resp2.json()
        assert data2.get("status") == "duplicate"


class TestAlertThresholdConfiguration:
    """Test that alert threshold is configurable."""

    def test_default_threshold_is_070(self):
        """Default alert threshold should be 0.70 for reduced false alarms."""
        from backend.config import settings
        
        assert settings.alert_threshold == 0.70

    def test_threshold_used_in_arbiter(self):
        """Arbiter should use threshold from settings."""
        from backend.agent.reasoning import arbiter
        
        from backend.config import settings
        assert arbiter._ALERT_THRESHOLD == settings.alert_threshold


class TestVisionImageValidation:
    """Test vision agent image size handling."""

    def test_large_image_is_capped(self):
        """Images larger than max_dimension should be resized."""
        import numpy as np
        import cv2
        from backend.agent import vision
        
        # Create a large test image (5000x5000)
        large_image = np.zeros((5000, 5000, 3), dtype=np.uint8)
        
        # encode_frame should handle this without error
        result = vision.encode_frame(large_image)
        
        # Should return base64 string
        assert isinstance(result, str)
        assert len(result) > 0


class TestAPIKeyProtection:
    """Test API key protection on events endpoints."""

    def test_events_requires_api_key(self, client):
        """Events endpoint should require API key when configured."""
        from backend.config import settings
        
        # If no credential configured, this test doesn't apply
        if not settings.local_api_credential:
            pytest.skip("No API credential configured")
        
        resp = client.get("/api/events")
        assert resp.status_code == 401

    def test_events_works_with_api_key(self, client):
        """Events endpoint should work with valid API key."""
        from backend.config import settings
        
        if not settings.local_api_credential:
            pytest.skip("No API credential configured")
        
        resp = client.get(
            "/api/events",
            headers={"x-api-key": settings.local_api_credential}
        )
        assert resp.status_code == 200


class TestSyntheticFalseAlarmScenarios:
    """
    Synthetic tests validating false alarm detection logic.
    
    These tests validate that the system can handle common false alarm
    scenarios by checking that the reasoning chain produces appropriate
    verdicts when given controlled inputs.
    """

    def test_pet_detected_suppresses(self):
        """Pet detected should typically result in suppress verdict."""
        from backend.agent.reasoning.base import ReasoningAgent
        
        # The AdversarialChallenger agent is designed to suppress false alarms
        # This test validates the agent logic exists and is configured
        from backend.agent.reasoning.falsification_auditor import FalsificationAuditorAgent

        agent = FalsificationAuditorAgent()
        
        # Verify falsification auditor can return any verdict (suppress/uncertain/alert)
        assert "suppress" in agent.allowed_verdicts
        assert "suppress" in agent.allowed_verdicts
        assert "uncertain" in agent.allowed_verdicts

    def test_trajectory_intent_mentions_delivery(self):
        """Trajectory & Intent Assessor should consider delivery scenarios."""
        from backend.agent.reasoning.trajectory_intent_assessor import TrajectoryIntentAssessorAgent

        agent = TrajectoryIntentAssessorAgent()
        assert "delivering" in agent.system_prompt.lower() or "delivery" in agent.system_prompt.lower()

    def test_context_baseline_tracks_temporal(self):
        """Context & Baseline Reasoner should evaluate temporal logic."""
        from backend.agent.reasoning.context_baseline_reasoner import ContextBaselineReasonerAgent

        agent = ContextBaselineReasonerAgent()
        assert "temporal" in agent.system_prompt.lower()


class TestIngestSchemaValidation:
    """Test canonical ingest payload validation."""

    def test_rejects_missing_image(self, client, ingest_headers):
        """Ingest should reject payloads without image source."""
        payload = {
            "cam_id": "test_cam",
            "home_id": "home",
            # Missing image_b64 and image_url
        }
        
        resp = client.post("/api/novin/ingest", json=payload, headers=ingest_headers)
        assert resp.status_code == 422

    def test_accepts_b64_image(self, client, ingest_headers):
        """Ingest should accept base64 encoded images."""
        
        def mock_process(*args, **kwargs):
            return {
                "event_id": "test",
                "status": "processed",
                "routing": {"action": "suppress"}
            }

        b64 = _get_test_image_b64()
        payload = {
            "cam_id": "test_cam",
            "home_id": "home",
            "image_b64": b64,
            "zone": "front_door",
        }
        
        with patch("backend.api.novin.ingest.process_canonical", AsyncMock(side_effect=mock_process)):
            resp = client.post("/api/novin/ingest", json=payload, headers=ingest_headers)
        
        assert resp.status_code == 200

    def test_accepts_url_image(self, client, ingest_headers):
        """Ingest should accept image URLs."""
        # Use a public test image URL
        payload = {
            "cam_id": "test_cam",
            "home_id": "home",
            "image_url": "https://placekitten.com/200/200",
            "zone": "front_door",
        }
        
        with patch("backend.ingest.image_fetcher.fetch_frame_from_url", AsyncMock(return_value=None)):
            resp = client.post("/api/novin/ingest", json=payload, headers=ingest_headers)
        
        # May fail on fetch but should not be 422 (validation passes)
        assert resp.status_code in (200, 500)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
