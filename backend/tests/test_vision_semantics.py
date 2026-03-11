from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from backend.agent.vision import analyse_frame
from backend.models.schemas import StreamMeta, VisionResult


def _response_with_payload(payload: dict) -> SimpleNamespace:
    return SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(content=json.dumps(payload)),
            )
        ]
    )


@pytest.mark.asyncio
@patch("backend.agent.vision.settings")
async def test_analyse_frame_supports_separate_identity_risk_fields(mock_settings):
    mock_settings.vision_provider = "groq"
    payload = {
        "identity_labels": ["person"],
        "risk_labels": ["suspicious_presence"],
        "uncertainty": 0.35,
        "threat": True,
        "severity": "medium",
        "description": "unknown person lingering near back door",
        "bbox": [[0.1, 0.2, 0.5, 0.9]],
        "confidence": 0.7,
    }
    client = SimpleNamespace(
        chat=SimpleNamespace(
            completions=SimpleNamespace(
                create=AsyncMock(return_value=_response_with_payload(payload))
            )
        )
    )
    meta = StreamMeta(stream_id="cam1", label="Back Door", site_id="home", zone="backyard", uri="direct")

    result = await analyse_frame("abc", meta, client)

    assert result.identity_labels == ["person"]
    assert result.risk_labels == ["suspicious_presence"]
    assert result.uncertainty == pytest.approx(0.35)
    assert result.categories == ["person", "intrusion"]
    assert result.threat is True
    assert result.severity == "medium"


@pytest.mark.asyncio
@patch("backend.agent.vision.settings")
async def test_analyse_frame_direct_schema(mock_settings):
    mock_settings.vision_provider = "groq"
    payload = {
        "scene_status": "active",
        "setting": "porch_door",
        "observed_entities": ["person"],
        "observed_actions": ["approaching_entry"],
        "spatial_tags": ["at_entry"],
        "object_labels": ["none"],
        "visibility_tags": ["clear_view"],
        "identity_labels": ["person"],
        "risk_labels": ["entry_approach"],
        "evidence_notes": ["person near front door"],
        "description": "Person walking toward front door.",
        "confidence": 0.85,
        "uncertainty": 0.1,
    }
    client = SimpleNamespace(
        chat=SimpleNamespace(
            completions=SimpleNamespace(
                create=AsyncMock(return_value=_response_with_payload(payload))
            )
        )
    )
    meta = StreamMeta(stream_id="cam1", label="Front Door", site_id="home", zone="front_door", uri="direct")

    result = await analyse_frame("abc", meta, client)

    assert result.identity_labels == ["person"]
    assert result.risk_labels == ["entry_approach"]
    assert result.threat is True
    assert result.severity == "medium"
    assert result.categories == ["person", "intrusion"]
    assert result.description == "Person walking toward front door."
    assert result.confidence == pytest.approx(0.85)
    assert result.uncertainty == pytest.approx(0.1)
    assert result.setting == "porch_door"
    assert result.observed_entities == ["person"]
    assert result.observed_actions == ["approaching_entry"]
    assert result.spatial_tags == ["at_entry"]
    assert result.visibility_tags == ["clear_view"]
    assert result.evidence_notes == ["person near front door"]


@pytest.mark.asyncio
@patch("backend.agent.vision.settings")
async def test_analyse_frame_direct_schema_noise(mock_settings):
    mock_settings.vision_provider = "groq"
    payload = {
        "scene_status": "noise",
        "setting": "yard",
        "identity_labels": ["clear"],
        "risk_labels": ["clear"],
        "description": "Wind moving branches.",
        "confidence": 0.9,
        "uncertainty": 0.1,
    }
    client = SimpleNamespace(
        chat=SimpleNamespace(
            completions=SimpleNamespace(
                create=AsyncMock(return_value=_response_with_payload(payload))
            )
        )
    )
    meta = StreamMeta(stream_id="cam1", label="Backyard", site_id="home", zone="backyard", uri="direct")

    result = await analyse_frame("abc", meta, client)

    assert result.identity_labels == ["clear"]
    assert result.risk_labels == ["clear"]
    assert result.threat is False
    assert result.severity == "none"
    assert result.categories == ["clear"]
    assert result.scene_status == "noise"
    assert result.observed_actions == ["environmental_motion"]
    assert result.visibility_tags == ["weather_noise"]


@pytest.mark.asyncio
@patch("backend.agent.vision.settings")
async def test_analyse_frame_backfills_new_fields_from_legacy_output(mock_settings):
    mock_settings.vision_provider = "groq"
    payload = {
        "threat": False,
        "severity": "none",
        "categories": ["pet"],
        "description": "cat moving through driveway",
        "confidence": 0.8,
    }
    client = SimpleNamespace(
        chat=SimpleNamespace(
            completions=SimpleNamespace(
                create=AsyncMock(return_value=_response_with_payload(payload))
            )
        )
    )
    meta = StreamMeta(stream_id="cam2", label="Driveway", site_id="home", zone="driveway", uri="direct")

    result = await analyse_frame("abc", meta, client)

    assert result.identity_labels == ["pet"]
    assert result.risk_labels == ["clear"]
    assert result.uncertainty == pytest.approx(0.2)
    assert result.categories == ["pet"]
    assert result.observed_entities == ["pet"]
    assert result.observed_actions == ["moving"]
    assert result.object_labels == ["none"]


def test_vision_result_backfills_compatibility_fields():
    result = VisionResult(
        setting="yard",
        observed_entities=["unknown"],
        observed_actions=["moving"],
        spatial_tags=["near_entry"],
        object_labels=["unknown_object"],
        visibility_tags=["partial_subject"],
        evidence_notes=["partial figure near gate"],
        description="ambiguous subject by side gate",
        confidence=0.6,
        identity_labels=["unknown"],
        risk_labels=["intrusion"],
        categories=[],
    )

    assert result.threat is True
    assert result.severity == "medium"
    assert result.categories == ["intrusion"]
    assert result.uncertainty == pytest.approx(0.4)
    assert result.setting == "yard"
    assert result.observed_entities == ["unknown"]
    assert result.evidence_notes == ["partial figure near gate"]
