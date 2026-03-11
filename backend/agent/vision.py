from __future__ import annotations

import base64
import io
import json
import logging
import time
from typing import Any

import numpy as np
from groq import AsyncGroq
from openai import AsyncOpenAI
from PIL import Image
from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception_type,
    before_sleep_log,
)

from backend.config import settings
from backend.models.schemas import BoundingBox, StreamMeta, VisionResult
from backend.policy import (
    ALLOWED_IDENTITY_LABELS,
    ALLOWED_RISK_LABELS,
    PROHIBITED_IDENTITY_TERMS,
)
from backend.provider import active_vision_model, get_siliconflow_client, get_together_client

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = """You are a vision model for a home security system. Extract only grounded, visible facts from a single camera frame.

RULES:
1. Output only valid JSON. No markdown, no prose, no explanation.
2. Report only visible facts from this frame. Do not infer intent, familiarity, trust, resident status, delivery role, or future actions.
3. Use "unknown" or "unclear_action" when visibility is insufficient.
4. Never infer identity (homeowner, intruder, guest, delivery driver). Human subjects are "person" only.
5. If the scene is environmental noise (wind, rain, shadows, bug on lens), set scene_status to "noise" and use clear/unknown values.
6. Keep evidence_notes short and tied to visible facts only.

OUTPUT SCHEMA (output ONLY this JSON):
{
  "scene_status": "active" | "noise",
  "setting": "porch_door" | "driveway" | "yard" | "indoor" | "street" | "garage" | "unknown",
  "observed_entities": ["person" | "pet" | "vehicle" | "package" | "unknown" | "clear" | "wildlife"],
  "observed_actions": ["approaching_entry" | "standing_at_entry" | "touching_entry_surface" | "passing_through" | "carrying_package" | "holding_object" | "loading_or_unloading" | "standing" | "moving" | "stationary" | "environmental_motion" | "unclear_action"],
  "spatial_tags": ["at_entry" | "near_entry" | "at_driveway" | "near_vehicle" | "near_fence" | "inside_threshold" | "on_walkway" | "unknown_location"],
  "object_labels": ["package" | "tool_like_object" | "phone" | "bag" | "unknown_object" | "none"],
  "visibility_tags": ["clear_view" | "low_light" | "blur" | "partial_subject" | "occluded" | "distant_subject" | "cropped_subject" | "weather_noise"],
  "risk_labels": ["entry_approach" | "entry_dwell" | "tamper" | "forced_entry" | "perimeter_progression" | "suspicious_presence" | "suspicious_person" | "wildlife_near_entry" | "delivery_pattern" | "resident_routine" | "benign_activity" | "clear" | "intrusion" | "motion"],
  "evidence_notes": ["<short visible fact>", "<short visible fact>"],
  "description": "<1-2 short sentences describing setting and visible action only>",
  "confidence": 0.0-1.0,
  "uncertainty": 0.0-1.0
}

Output ONLY the JSON object. Nothing else."""

_VALID_SEVERITIES = {"none", "low", "medium", "high", "critical"}
_VALID_LEGACY_CATEGORIES = {"person", "pet", "package", "vehicle", "intrusion", "motion", "clear"}
_ENTITY_TYPE_TO_CATEGORY = {"person": "person", "vehicle": "vehicle", "animal": "pet", "unknown": "motion"}
_ENTRY_ZONES = {"porch_entry", "perimeter_fence", "yard", "driveway", "front_door"}
_RISK_THREAT_HINTS = {
    "intrusion",
    "forced_entry",
    "suspicious_presence",
    "suspicious_person",
    "entry_approach",
    "entry_dwell",
    "tamper",
    "perimeter_progression",
    "wildlife_near_entry",
    "threat",
    "high_risk",
    "critical_risk",
}


def _to_list(value: object) -> list[str]:
    if isinstance(value, str):
        item = value.strip().lower()
        return [item] if item else []
    if not isinstance(value, list):
        return []
    values: list[str] = []
    for item in value:
        text = str(item).strip().lower()
        if text:
            values.append(text)
    return values


def _clamp01(raw: object, default: float = 0.0) -> float:
    try:
        value = float(raw)
    except (TypeError, ValueError):
        value = default
    return max(0.0, min(1.0, value))


def _normalise_severity(raw: object, threat: bool) -> str:
    severity = str(raw or "none").strip().lower()
    if severity not in _VALID_SEVERITIES:
        return "medium" if threat else "none"
    if threat and severity == "none":
        return "medium"
    if not threat and severity != "none":
        return "none"
    return severity


def _derive_legacy_categories(identity_labels: list[str], risk_labels: list[str], threat: bool) -> list[str]:
    categories: list[str] = []
    for label in identity_labels + risk_labels:
        if label in _VALID_LEGACY_CATEGORIES and label not in categories:
            categories.append(label)
    if not categories:
        categories = ["intrusion"] if threat else ["clear"]
    if threat and "intrusion" not in categories and "motion" not in categories:
        categories.append("intrusion")
    if not threat and "clear" not in categories and "intrusion" not in categories and "motion" not in categories:
        categories.append("clear")
    return categories


def _sanitize_identity_labels(identity_labels: list[str]) -> list[str]:
    sanitized: list[str] = []
    for label in identity_labels:
        clean = str(label).strip().lower()
        if not clean:
            continue
        if clean in PROHIBITED_IDENTITY_TERMS:
            sanitized.append("person")
        elif clean in ALLOWED_IDENTITY_LABELS:
            sanitized.append(clean)
        else:
            logger.warning("Vision identity label not in allowlist, mapping to person: %r", clean)
            sanitized.append("person")
    return sanitized or ["clear"]


def _sanitize_risk_labels(risk_labels: list[str], threat: bool) -> list[str]:
    sanitized: list[str] = []
    for label in risk_labels:
        clean = str(label).strip().lower()
        if not clean:
            continue
        if clean in ALLOWED_RISK_LABELS:
            sanitized.append(clean)
        else:
            logger.warning("Vision risk label not in allowlist, dropping: %r", clean)
            sanitized.append("suspicious_presence" if threat else "clear")
    if not sanitized:
        return ["clear"]
    return sanitized


def _sanitize_description(description: object) -> str:
    text = " ".join(str(description or "").split())
    lowered = text.lower()
    if any(term in lowered for term in PROHIBITED_IDENTITY_TERMS):
        return "Person or activity detected; identity is unknown from the image."
    return text[:150]


def _sanitize_setting(value: object) -> str:
    setting = str(value or "unknown").strip().lower()
    allowed = {"porch_door", "driveway", "yard", "indoor", "street", "garage", "unknown"}
    return setting if setting in allowed else "unknown"


def _sanitize_observed_actions(value: object) -> list[str]:
    allowed = {
        "approaching_entry",
        "standing_at_entry",
        "touching_entry_surface",
        "passing_through",
        "carrying_package",
        "holding_object",
        "loading_or_unloading",
        "standing",
        "moving",
        "stationary",
        "environmental_motion",
        "unclear_action",
    }
    actions = [item for item in _to_list(value) if item in allowed]
    return list(dict.fromkeys(actions)) or ["unclear_action"]


def _sanitize_spatial_tags(value: object) -> list[str]:
    allowed = {
        "at_entry",
        "near_entry",
        "at_driveway",
        "near_vehicle",
        "near_fence",
        "inside_threshold",
        "on_walkway",
        "unknown_location",
    }
    tags = [item for item in _to_list(value) if item in allowed]
    return list(dict.fromkeys(tags)) or ["unknown_location"]


def _sanitize_object_labels(value: object) -> list[str]:
    allowed = {"package", "tool_like_object", "phone", "bag", "unknown_object", "none"}
    labels = [item for item in _to_list(value) if item in allowed]
    return list(dict.fromkeys(labels)) or ["none"]


def _sanitize_visibility_tags(value: object, scene_status: str, uncertainty: float) -> list[str]:
    allowed = {"clear_view", "low_light", "blur", "partial_subject", "occluded", "distant_subject", "cropped_subject", "weather_noise"}
    tags = [item for item in _to_list(value) if item in allowed]
    tags = list(dict.fromkeys(tags))
    if tags:
        return tags
    if scene_status == "noise":
        return ["weather_noise"]
    if uncertainty >= 0.65:
        return ["partial_subject"]
    return ["clear_view"]


def _sanitize_evidence_notes(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    notes: list[str] = []
    for item in value:
        text = " ".join(str(item or "").split())[:80]
        lowered = text.lower()
        if text and not any(term in lowered for term in PROHIBITED_IDENTITY_TERMS):
            notes.append(text)
    return notes[:4]


def _parse_direct_schema(data: dict[str, Any]) -> VisionResult | None:
    """Parse direct output schema (scene_status, identity_labels, risk_labels). Returns None if not direct format."""
    scene_status = str(data.get("scene_status", "")).strip().lower()
    if scene_status not in ("active", "noise"):
        return None
    confidence = _clamp01(data.get("confidence"), default=0.8)
    uncertainty = _clamp01(data.get("uncertainty"), default=max(0.0, 1.0 - confidence))
    observed_entities = _sanitize_identity_labels(
        _to_list(data.get("observed_entities") or data.get("identity_labels")) or ["clear"]
    )
    identity_labels = _sanitize_identity_labels(_to_list(data.get("identity_labels")) or observed_entities)
    risk_labels = _to_list(data.get("risk_labels"))
    description = str(data.get("description", "")).strip() or "No description."
    setting = _sanitize_setting(data.get("setting"))
    observed_actions = _sanitize_observed_actions(data.get("observed_actions"))
    spatial_tags = _sanitize_spatial_tags(data.get("spatial_tags"))
    object_labels = _sanitize_object_labels(data.get("object_labels"))
    visibility_tags = _sanitize_visibility_tags(data.get("visibility_tags"), scene_status, uncertainty)
    evidence_notes = _sanitize_evidence_notes(data.get("evidence_notes"))

    if scene_status == "noise":
        return VisionResult(
            scene_status="noise",
            setting=setting,
            observed_entities=["clear"],
            observed_actions=["environmental_motion"],
            spatial_tags=spatial_tags,
            object_labels=["none"],
            visibility_tags=visibility_tags,
            evidence_notes=evidence_notes,
            threat=False,
            severity="none",
            categories=["clear"],
            identity_labels=["clear"],
            risk_labels=["clear"],
            uncertainty=0.1,
            description=_sanitize_description(description or "Environmental noise; no entity detected."),
            bbox=[],
            confidence=0.9,
            latency_ms=0.0,
        )

    identity_labels = _sanitize_identity_labels(identity_labels or observed_entities or ["clear"])
    threat = any(r in _RISK_THREAT_HINTS for r in risk_labels)
    risk_labels = _sanitize_risk_labels(risk_labels or ["clear"], threat)
    categories = _derive_legacy_categories(identity_labels, risk_labels, threat)
    severity = "high" if threat and "tamper" in risk_labels else ("medium" if threat else "none")

    return VisionResult(
        scene_status="active",
        setting=setting,
        observed_entities=observed_entities,
        observed_actions=observed_actions,
        spatial_tags=spatial_tags,
        object_labels=object_labels,
        visibility_tags=visibility_tags,
        evidence_notes=evidence_notes,
        threat=threat,
        severity=severity,
        categories=categories,
        identity_labels=identity_labels,
        risk_labels=risk_labels,
        uncertainty=uncertainty,
        description=_sanitize_description(description),
        bbox=[],
        confidence=confidence,
        latency_ms=0.0,
    )


def _map_entity_schema_to_vision_result(data: dict[str, Any]) -> VisionResult:
    """Map new entity-based vision schema to legacy VisionResult for downstream pipeline."""
    scene_status = str(data.get("scene_status", "active_entity")).strip().lower()
    entities = data.get("entities") or []
    physical_summary = str(data.get("physical_action_summary", "")).strip() or "No physical action observed."
    setting = _sanitize_setting(data.get("setting"))
    evidence_notes = _sanitize_evidence_notes(data.get("evidence_notes"))

    if scene_status == "clear_noise" or not entities:
        return VisionResult(
            scene_status="noise",
            setting=setting,
            observed_entities=["clear"],
            observed_actions=["environmental_motion"],
            spatial_tags=["unknown_location"],
            object_labels=["none"],
            visibility_tags=["weather_noise"],
            evidence_notes=evidence_notes,
            threat=False,
            severity="none",
            categories=["clear"],
            identity_labels=["clear"],
            risk_labels=["clear"],
            uncertainty=0.1,
            description=_sanitize_description(physical_summary or "Environmental noise; no entity detected."),
            bbox=[],
            confidence=0.9,
            latency_ms=0.0,
        )

    identity_labels: list[str] = []
    categories: list[str] = []
    risk_labels: list[str] = []
    observed_actions: list[str] = []
    spatial_tags: list[str] = []
    object_labels: list[str] = []
    threat = False

    for ent in entities:
        if not isinstance(ent, dict):
            continue
        etype = str(ent.get("type", "unknown")).strip().lower()
        zone = str(ent.get("zone", "")).strip().lower()
        movement = str(ent.get("movement_profile", "static")).strip().lower()
        held = ent.get("held_objects") or []
        attire = ent.get("attire_flags") or []

        cat = _ENTITY_TYPE_TO_CATEGORY.get(etype, "motion" if etype == "unknown" else etype)
        if cat == "pet":
            identity_labels.append("pet")
            categories.append("pet")
        elif cat == "person":
            identity_labels.append("person")
            categories.append("person")
        elif cat == "vehicle":
            identity_labels.append("vehicle")
            categories.append("vehicle")
        else:
            identity_labels.append("unknown")
            categories.append("motion")

        if zone in _ENTRY_ZONES:
            spatial_tags.append("at_entry")
            if movement in ("approaching", "loitering", "pacing", "crouching"):
                risk_labels.append("entry_approach")
                observed_actions.append("approaching_entry")
            if movement == "loitering":
                risk_labels.append("entry_dwell")
                observed_actions.append("standing_at_entry")
        if movement == "crouching" and zone in _ENTRY_ZONES:
            risk_labels.append("suspicious_presence")
        if "weapon" in (str(h).lower() for h in held):
            threat = True
            risk_labels.append("suspicious_presence")
            object_labels.append("unknown_object")
        if "tool" in (str(h).lower() for h in held) and zone in _ENTRY_ZONES:
            risk_labels.append("tamper")
            observed_actions.append("touching_entry_surface")
            object_labels.append("tool_like_object")
        if "package" in (str(h).lower() for h in held):
            risk_labels.append("delivery_pattern")
            observed_actions.append("carrying_package")
            object_labels.append("package")
        if "face_covered" in (str(a).lower() for a in attire) and zone in _ENTRY_ZONES:
            risk_labels.append("suspicious_presence")
        if not movement or movement == "static":
            observed_actions.append("stationary")
        elif movement in {"walking", "running", "passing", "moving"}:
            observed_actions.append("moving")

    identity_labels = list(dict.fromkeys(identity_labels)) or ["clear"]
    categories = list(dict.fromkeys(categories)) or ["clear"]
    risk_labels = list(dict.fromkeys(risk_labels)) or ["clear"]
    observed_actions = _sanitize_observed_actions(observed_actions)
    spatial_tags = _sanitize_spatial_tags(spatial_tags)
    object_labels = _sanitize_object_labels(object_labels)

    identity_labels = _sanitize_identity_labels(identity_labels)
    threat = threat or any(r in _RISK_THREAT_HINTS for r in risk_labels)
    risk_labels = _sanitize_risk_labels(risk_labels, threat)

    if not categories:
        categories = _derive_legacy_categories(identity_labels, risk_labels, threat)

    severity = "medium" if threat else "none"
    if threat and "tamper" in risk_labels:
        severity = "high"

    return VisionResult(
        scene_status="active",
        setting=setting,
        observed_entities=identity_labels,
        observed_actions=observed_actions,
        spatial_tags=spatial_tags,
        object_labels=object_labels,
        visibility_tags=_sanitize_visibility_tags(
            data.get("visibility_tags"),
            "active",
            0.15 if "unknown" in identity_labels else 0.05,
        ),
        evidence_notes=evidence_notes,
        threat=threat,
        severity=severity,
        categories=categories,
        identity_labels=identity_labels,
        risk_labels=risk_labels,
        uncertainty=0.15 if "unknown" in identity_labels else 0.05,
        description=_sanitize_description(physical_summary),
        bbox=[],
        confidence=0.85,
        latency_ms=0.0,
    )


def encode_frame(frame: np.ndarray) -> str:
    h, w = frame.shape[:2]
    
    # Validate image size before processing - prevent memory issues
    max_dimension = 4096
    if w > max_dimension or h > max_dimension:
        logger.warning(
            "Image too large (%dx%d) - capping to %d before processing", 
            w, h, max_dimension
        )
        import cv2
        scale = max_dimension / max(w, h)
        new_w = int(w * scale)
        new_h = int(h * scale)
        frame = cv2.resize(frame, (new_w, new_h), interpolation=cv2.INTER_AREA)
    
    if w > settings.frame_max_width:
        scale = settings.frame_max_width / w
        new_w = settings.frame_max_width
        new_h = int(h * scale)
        import cv2
        frame = cv2.resize(frame, (new_w, new_h), interpolation=cv2.INTER_AREA)

    img_rgb = frame[:, :, ::-1]
    pil_img = Image.fromarray(img_rgb)
    buf = io.BytesIO()
    pil_img.save(buf, format="JPEG", quality=settings.frame_jpeg_quality, optimize=True)
    buf.seek(0)
    return base64.b64encode(buf.read()).decode("utf-8")


def _vision_context_prompt(
    stream_meta: StreamMeta,
    ingest_metadata: dict[str, Any] | None,
    *,
    include_context: bool = True,
) -> str:
    """Build vision prompt. When include_context=False, use image-only (no camera/zone hints)."""
    if not include_context:
        return "Describe exactly what you see in this image."
    parts = [
        f"Camera label: {stream_meta.label[:48]}",
        f"Zone hint: {stream_meta.zone[:32]}",
    ]
    if isinstance(ingest_metadata, dict) and ingest_metadata:
        context_bits: list[str] = []
        event = ingest_metadata.get("event")
        if isinstance(event, dict):
            for key in ("label", "type", "zone"):
                value = str(event.get(key, "")).strip()
                if value:
                    context_bits.append(f"event_{key}={value[:32]}")
        preferences = ingest_metadata.get("preferences")
        if isinstance(preferences, dict) and preferences:
            context_bits.append("preferences_present=true")
        if context_bits:
            parts.append("Ingest context: " + ", ".join(context_bits[:4]))
    parts.append("Describe exactly what you see in this image. Use the context only as a weak hint and override it if the pixels disagree.")
    return " ".join(parts)


@retry(
    retry=retry_if_exception_type((Exception,)),
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=30),
    before_sleep=before_sleep_log(logger, logging.WARNING),
    reraise=True,
)
async def analyse_frame(
    b64: str,
    stream_meta: StreamMeta,
    client: AsyncGroq | AsyncOpenAI | None,
    ingest_metadata: dict[str, Any] | None = None,
) -> VisionResult:
    t0 = time.monotonic()
    include_context = True
    if isinstance(ingest_metadata, dict) and ingest_metadata.get("include_context") is False:
        include_context = False
    prompt_user = _vision_context_prompt(stream_meta, ingest_metadata, include_context=include_context)

    try:
        model_name = active_vision_model()
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {
                "role": "user",
                "content": [
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:image/jpeg;base64,{b64}"},
                    },
                    {"type": "text", "text": prompt_user},
                ],
            },
        ]
        if settings.vision_provider == "together":
            if not settings.together_api_key:
                raise RuntimeError("TOGETHER_API_KEY is required when VISION_PROVIDER=together")
            vision_client = get_together_client()
            response = await vision_client.chat.completions.create(
                model=model_name,
                messages=messages,
                max_tokens=400,
                temperature=0.1,
            )
        elif settings.vision_provider == "siliconflow":
            if not settings.siliconflow_api_key:
                raise RuntimeError("SILICONFLOW_API_KEY is required when VISION_PROVIDER=siliconflow")
            vision_client = get_siliconflow_client()
            response = await vision_client.chat.completions.create(
                model=model_name,
                messages=messages,
                max_tokens=400,
                temperature=0.1,
            )
        else:
            if client is None:
                raise RuntimeError("Groq vision client is not initialised")
            response = await client.chat.completions.create(
                model=model_name,
                messages=messages,
                max_tokens=400,
                temperature=0.1,
            )

        latency_ms = (time.monotonic() - t0) * 1000
        if latency_ms > 700:
            logger.warning("Vision latency %.0f ms exceeds 700 ms threshold", latency_ms)

        usage = {}
        if getattr(response, "usage", None) is not None:
            u = response.usage
            usage = {
                "prompt_tokens": getattr(u, "prompt_tokens", 0) or getattr(u, "input_tokens", 0),
                "completion_tokens": getattr(u, "completion_tokens", 0) or getattr(u, "output_tokens", 0),
                "total_tokens": getattr(u, "total_tokens", 0) or 0,
            }

        content = response.choices[0].message.content.strip()
        if content.startswith("```"):
            content = content.split("\n", 1)[-1].rsplit("```", 1)[0].strip()

        data = json.loads(content)

        direct_result = _parse_direct_schema(data)
        if direct_result is not None:
            return direct_result.model_copy(update={"latency_ms": round(latency_ms, 1), "usage": usage})

        if "scene_status" in data and "entities" in data:
            result = _map_entity_schema_to_vision_result(data)
            return result.model_copy(update={"latency_ms": round(latency_ms, 1), "usage": usage})

        bbox_list = []
        for b in data.get("bbox", []):
            if isinstance(b, (list, tuple)) and len(b) == 4:
                bbox_list.append(BoundingBox(x1=b[0], y1=b[1], x2=b[2], y2=b[3]))

        identity_labels = _to_list(data.get("identity_labels") or data.get("identity") or data.get("objects"))
        risk_labels = _to_list(data.get("risk_labels") or data.get("security_relevance") or data.get("risk"))
        legacy_categories = _to_list(data.get("categories"))
        setting = _sanitize_setting(data.get("setting"))
        observed_actions = _sanitize_observed_actions(data.get("observed_actions")) if data.get("observed_actions") is not None else []
        spatial_tags = _sanitize_spatial_tags(data.get("spatial_tags")) if data.get("spatial_tags") is not None else []
        object_labels = _sanitize_object_labels(data.get("object_labels")) if data.get("object_labels") is not None else []

        if not identity_labels:
            identity_labels = [c for c in legacy_categories if c != "intrusion"] or ["clear"]
        identity_labels = _sanitize_identity_labels(identity_labels)
        if not risk_labels:
            risk_labels = [c for c in legacy_categories if c in {"intrusion", "motion", "clear"}] or ["clear"]

        threat = bool(data.get("threat", any(label in _RISK_THREAT_HINTS for label in risk_labels)))
        risk_labels = _sanitize_risk_labels(risk_labels, threat)
        severity = _normalise_severity(data.get("severity"), threat)

        categories = [c for c in legacy_categories if c in _VALID_LEGACY_CATEGORIES]
        if not categories:
            categories = _derive_legacy_categories(identity_labels, risk_labels, threat)

        confidence = _clamp01(data.get("confidence"), default=0.0)
        uncertainty = _clamp01(data.get("uncertainty"), default=max(0.0, 1.0 - confidence))
        visibility_tags = _sanitize_visibility_tags(data.get("visibility_tags"), "active", uncertainty)
        evidence_notes = _sanitize_evidence_notes(data.get("evidence_notes"))
        observed_entities = _sanitize_identity_labels(_to_list(data.get("observed_entities")) or identity_labels)

        return VisionResult(
            scene_status="active",
            setting=setting,
            observed_entities=observed_entities,
            observed_actions=observed_actions,
            spatial_tags=spatial_tags,
            object_labels=object_labels,
            visibility_tags=visibility_tags,
            evidence_notes=evidence_notes,
            threat=threat,
            severity=severity,
            categories=categories,
            identity_labels=identity_labels,
            risk_labels=risk_labels,
            uncertainty=uncertainty,
            description=_sanitize_description(data.get("description", "")),
            bbox=bbox_list,
            confidence=confidence,
            latency_ms=round(latency_ms, 1),
            usage=usage,
        )

    except json.JSONDecodeError as exc:
        latency_ms = (time.monotonic() - t0) * 1000
        logger.error("Vision JSON parse error: %s | raw: %s", exc, content[:200] if "content" in dir() else "")
        return VisionResult(
            threat=False,
            severity="none",
            categories=["clear"],
            identity_labels=["clear"],
            risk_labels=["clear"],
            uncertainty=1.0,
            description="Vision parse error",
            confidence=0.0,
            latency_ms=round(latency_ms, 1),
        )
    except Exception as exc:
        latency_ms = (time.monotonic() - t0) * 1000
        logger.exception("Vision agent error: %s", exc)
        return VisionResult(
            threat=False,
            severity="none",
            categories=["clear"],
            identity_labels=["clear"],
            risk_labels=["clear"],
            uncertainty=1.0,
            description="Vision agent error",
            confidence=0.0,
            latency_ms=round(latency_ms, 1),
        )
