"""
Text-to-Text Simulation Sandbox: convert synthetic scenarios to FramePackets for reasoning-only testing.

No video required. Vision telemetry is simulated from scenario JSON.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from backend.models.schemas import (
    FramePacket,
    HistoryContext,
    StreamMeta,
    VisionResult,
)


def scenario_to_vision_result(scenario: dict[str, Any]) -> VisionResult:
    """Convert a synthetic scenario dict into a VisionResult (simulated vision telemetry)."""
    entity = str(scenario.get("entity", "person")).lower()
    risk_cues = scenario.get("risk_cues") or []
    cohort = str(scenario.get("cohort", "ambiguous")).lower()
    props = scenario.get("props") or []
    pathing = str(scenario.get("pathing", ""))

    # Map entity to categories (must use VisionResult allowed values)
    _allowed = {"person", "pet", "package", "vehicle", "intrusion", "motion", "clear"}
    cat_map = {
        "person": ["person"],
        "pet": ["pet"],
        "vehicle": ["vehicle"],
        "package": ["person", "package"],
        "motion": ["motion"],
        "clear": ["clear"],
    }
    categories = cat_map.get(entity, ["person"] if entity else ["clear"])
    categories = [c for c in categories if c in _allowed] or ["clear"]

    # Build description from scenario
    desc_parts = [pathing]
    if props:
        desc_parts.append(f"Props: {', '.join(props)}")
    if scenario.get("history_brief"):
        desc_parts.append(f"History: {scenario['history_brief']}")
    description = ". ".join(desc_parts)[:200]

    threat = cohort == "threat" or any(
        c in str(risk_cues).lower()
        for c in ["tamper", "forced_entry", "suspicious_person", "intrusion"]
    )
    severity = "high" if threat and cohort == "threat" else ("medium" if threat else "low")
    if not threat:
        severity = "none"

    risk_labels = [str(r).strip().lower() for r in risk_cues if r]
    if not risk_labels and threat:
        risk_labels = ["suspicious_presence"]
    if not risk_labels:
        risk_labels = ["clear"]

    confidence = 0.85 if cohort != "ambiguous" else 0.65
    uncertainty = 0.15 if cohort != "ambiguous" else 0.4

    return VisionResult(
        threat=threat,
        severity=severity,
        categories=categories,
        identity_labels=[],
        risk_labels=risk_labels,
        uncertainty=uncertainty,
        description=description,
        confidence=confidence,
        latency_ms=0.0,
    )


def scenario_to_history_context(scenario: dict[str, Any]) -> HistoryContext:
    """Convert scenario history_brief into HistoryContext."""
    brief = scenario.get("history_brief") or ""
    # Simple heuristic: if "common" or "routine" in brief, lower anomaly
    anomaly = 0.3 if "common" in brief.lower() or "routine" in brief.lower() else 0.6
    baseline = 1.0 if "common" in brief.lower() else 0.5
    return HistoryContext(
        recent_events=[],
        similar_events=[],
        camera_baseline={"person": baseline, "motion": baseline * 0.5},
        site_baseline={},
        anomaly_score=anomaly,
        memory_items=[],
    )


def scenario_to_frame_packet(scenario: dict[str, Any], frame_id: str | None = None) -> FramePacket:
    """Convert a synthetic scenario into a FramePacket for reasoning agents."""
    scenario_id = scenario.get("scenario_id", "unknown")
    zone = str(scenario.get("zone", "front_door"))
    time_iso = scenario.get("time_iso", "2026-03-08T12:00:00")
    try:
        ts = datetime.fromisoformat(time_iso.replace("Z", "+00:00"))
    except Exception:
        ts = datetime.utcnow()

    stream_id = f"sandbox_{zone}"
    stream_meta = StreamMeta(
        stream_id=stream_id,
        label=f"Sandbox {zone}",
        site_id="sandbox-home",
        zone=zone,
        uri="sandbox://synthetic",
    )

    vision = scenario_to_vision_result(scenario)
    history = scenario_to_history_context(scenario)

    return FramePacket(
        frame_id=frame_id or f"sandbox_{scenario_id}_{ts.timestamp():.0f}",
        stream_id=stream_id,
        timestamp=ts,
        b64_frame="",  # No image in text-to-text sandbox
        stream_meta=stream_meta,
        vision=vision,
        history=history,
        event_context=None,
    )
