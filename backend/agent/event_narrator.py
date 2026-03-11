from __future__ import annotations

import logging
from datetime import datetime
from typing import Sequence

from backend.models.schemas import AgentOutput, FramePacket, VisionResult

logger = logging.getLogger(__name__)


def _location_for_display(packet: FramePacket) -> str | None:
    """Return location/camera name only when JSON explicitly mentions it (vision or stream metadata)."""
    if packet.vision.setting and packet.vision.setting != "unknown":
        return packet.vision.setting.replace("_", " ")
    if packet.vision.spatial_tags:
        for tag in packet.vision.spatial_tags:
            if tag and tag != "unknown_location":
                return tag.replace("_", " ")
    label = (packet.stream_meta.label or "").strip()
    if label and label.lower() not in ("test camera", "camera", "cam", "pipeline_test", ""):
        return label
    return None


class SecurityEventNarrator:
    """Builds concise, homeowner-friendly summaries grounded in observed facts."""

    _CATEGORY_LABELS = {
        "person": "person detected",
        "pet": "pet or animal",
        "package": "package or delivery",
        "vehicle": "vehicle",
        "intrusion": "possible intrusion",
        "motion": "motion detected",
    }

    def generate_headline(
        self,
        *,
        packet: FramePacket,
        risk_level: str,
        final_confidence: float,
    ) -> str:
        identity_label = self._identity_label(packet.vision)
        risk_label = self._risk_label(packet.vision)
        severity = risk_level.lower()
        loc = _location_for_display(packet)
        loc_phrase = f" in {loc}" if loc else ""

        if risk_level == "high":
            return (
                f"{severity.capitalize()} home-security risk ({risk_label}){loc_phrase}; "
                f"observed {identity_label}."
            )

        if risk_level == "medium":
            return (
                f"{severity.capitalize()} home-security concern{loc_phrase}; "
                f"observed {identity_label} with elevated risk signal ({risk_label})."
            )

        if risk_level == "low":
            return (
                f"Observed {identity_label}{loc_phrase}; "
                f"low home-security risk ({risk_label}), visible for homeowner review."
            )

        return (
            f"Observed {identity_label}{loc_phrase}; "
            f"no home-security concern, suppressed from the main feed."
        )

    def generate_narrative(
        self,
        *,
        packet: FramePacket,
        agent_outputs: Sequence[AgentOutput],
        risk_level: str,
        final_confidence: float,
    ) -> str:
        bullets = [
            self._threat_bullet(packet.vision),
            self._location_time_bullet(packet.timestamp, packet),
            self._consensus_bullet(agent_outputs),
            self._action_bullet(packet, risk_level),
            self._history_bullet(packet),
        ]
        return "\n".join(bullets)

    def _consensus_bullet(
        self,
        agent_outputs: Sequence[AgentOutput],
    ) -> str:
        alert_votes = sum(1 for output in agent_outputs if output.verdict == "alert")
        suppress_votes = sum(1 for output in agent_outputs if output.verdict == "suppress")
        uncertain_votes = sum(1 for output in agent_outputs if output.verdict == "uncertain")
        vote_text = (
            f"agent consensus: {alert_votes} alert, {suppress_votes} suppress"
            + (f", {uncertain_votes} uncertain" if uncertain_votes else "")
        )
        if uncertain_votes > 0:
            return f"• Agent consensus: {vote_text}; ambiguity remains and monitoring continues."
        return f"• Agent consensus: {vote_text}."

    def _threat_bullet(self, vision: VisionResult) -> str:
        identity = self._identity_label(vision)
        risk = self._risk_label(vision)
        if not vision.threat and risk == "no explicit risk signal":
            return f"• Activity identity: {identity}. Security interpretation: no home security concern (risk level: none)."
        desc = (vision.description or "No additional scene details provided").strip().rstrip(".")
        return (
            f"• Activity identity: {identity}. Security interpretation: {risk} "
            f"({vision.severity} risk). Observed: {desc}."
        )

    def _location_time_bullet(self, timestamp: datetime, packet: FramePacket) -> str:
        loc = _location_for_display(packet)
        if loc:
            return f"• Location/Time: {loc} at {timestamp.strftime('%H:%M:%S UTC')}."
        return f"• Time: {timestamp.strftime('%H:%M:%S UTC')}."

    def _action_bullet(self, packet: FramePacket, risk_level: str) -> str:
        severity = risk_level.lower()
        if risk_level == "high":
            if severity == "high":
                text = "Check the app immediately and consider emergency response if the threat is active"
            else:
                text = "Check the app immediately and verify the threat"
            return f"• Recommended action: {text}."

        if risk_level == "medium":
            return "• Recommended action: Review this event promptly; risk is elevated but not yet routed as an urgent notification."
        if risk_level == "low":
            return "• Recommended action: Keep this visible in history for homeowner awareness; no urgent action needed."
        return "• Recommended action: Keep this suppressed from the main feed unless new evidence raises the risk."

    def _history_bullet(self, packet: FramePacket) -> str:
        recent_count = len(packet.history.recent_events)
        similar_count = len(packet.history.similar_events)
        anomaly = packet.history.anomaly_score

        if recent_count == 0 and similar_count == 0:
            return "• Historical context: No prior events recorded for this location."
        if anomaly <= 0:
            return (
                f"• Historical context: {recent_count} recent events, {similar_count} similar events; "
                "limited anomaly signal available."
            )
        if anomaly >= 0.6:
            return (
                f"• Historical context: {recent_count} recent events, {similar_count} similar events; "
                f"anomaly score {anomaly:.2f} (elevated)."
            )
        return (
            f"• Historical context: {recent_count} recent events, {similar_count} similar events; "
            f"anomaly score {anomaly:.2f} (moderate)."
        )

    def _identity_label(self, vision: VisionResult) -> str:
        labels = [label for label in vision.identity_labels if label and label != "clear"]
        if not labels:
            labels = [cat for cat in vision.categories if cat and cat not in {"clear", "intrusion", "motion"}]
        if not labels:
            return "home activity"
        return labels[0].replace("_", " ")

    def _risk_label(self, vision: VisionResult) -> str:
        labels = [label for label in vision.risk_labels if label and label != "clear"]
        if not labels:
            labels = [cat for cat in vision.categories if cat and cat in {"intrusion", "motion"}]
        if not labels:
            return "no explicit risk signal"
        label = labels[0].replace("_", " ")
        return self._CATEGORY_LABELS.get(labels[0], label)
