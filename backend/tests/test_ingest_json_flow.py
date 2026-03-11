from __future__ import annotations

from types import SimpleNamespace

from backend.ingest.adapters.registry import normalise
from backend.ingest.schemas import CanonicalIngestPayload
from backend.agent.vision import _vision_context_prompt
from backend.models.schemas import StreamMeta


def test_canonical_ingest_lifts_nested_embedded_image_and_metadata() -> None:
    payload = CanonicalIngestPayload.model_validate(
        {
            "cam_id": "cam-front",
            "home_id": "home-1",
            "image": {"base64": "ZmFrZQ==", "headers": {"Authorization": "Bearer t"}},
            "event": {"id": "evt-123", "label": "person", "zone": "driveway"},
            "attachments": [{"url": "https://example.com/frame.jpg", "kind": "snapshot"}],
            "preferences": {"notify_driveway": True},
            "metadata": {"source_hint": "json-webhook"},
        }
    )

    assert payload.image_b64 == "ZmFrZQ=="
    assert payload.image_url == "https://example.com/frame.jpg"
    assert payload.image_url_headers == {"Authorization": "Bearer t"}
    assert payload.source_event_id == "evt-123"
    assert payload.zone == "driveway"
    assert payload.metadata["source_hint"] == "json-webhook"
    assert payload.metadata["event"]["label"] == "person"
    assert payload.metadata["image"]["base64"] == "ZmFrZQ=="
    assert payload.metadata["attachments"][0]["kind"] == "snapshot"


def test_registry_normalise_accepts_attachment_style_canonical_json() -> None:
    payload = normalise(
        None,
        {
            "cam_id": "cam-yard",
            "home_id": "home-2",
            "attachment": {"image_b64": "YWJjZA==", "zone": "yard"},
            "event": {"type": "motion", "zone": "yard"},
            "metadata": {"scenario_id": "json-attachment-1"},
        },
    )

    assert payload.image_b64 == "YWJjZA=="
    assert payload.zone == "yard"
    assert payload.metadata["scenario_id"] == "json-attachment-1"
    assert payload.metadata["attachment"]["zone"] == "yard"
    assert payload.metadata["event"]["type"] == "motion"


def test_vision_context_prompt_includes_safe_ingest_context() -> None:
    stream_meta = StreamMeta(
        stream_id="cam-front",
        label="Front Camera",
        site_id="home",
        zone="front_door",
        uri="ingest",
    )
    prompt = _vision_context_prompt(
        stream_meta,
        {
            "event": {"label": "person", "type": "motion", "zone": "front_door"},
            "preferences": {"notify_front": True},
        },
    )

    assert "Camera label: Front Camera" in prompt
    assert "Zone hint: front_door" in prompt
    assert "event_label=person" in prompt
    assert "event_type=motion" in prompt
    assert "preferences_present=true" in prompt
    assert "override it if the pixels disagree" in prompt
