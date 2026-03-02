from __future__ import annotations

import logging
from datetime import datetime
from typing import Sequence

from backend.models.schemas import AgentOutput, FramePacket, VisionResult

logger = logging.getLogger(__name__)


class SecurityEventNarrator:
    """Builds concise, operator-friendly summaries grounded in observed facts."""

    _CATEGORY_LABELS = {
        "intrusion": "restricted-area intrusion",
        "crowd": "unusual crowding",
        "object": "suspicious object",
        "behaviour": "abnormal behaviour",
    }

    def generate_headline(
        self,
        *,
        packet: FramePacket,
        action: str,
        final_confidence: float,
    ) -> str:
        label = self._event_label(packet.vision)
        severity = packet.vision.severity.lower()
        confidence = max(0.0, min(1.0, final_confidence))

        if action == "alert":
            return (
                f"{severity.capitalize()}-severity {label} detected in {packet.stream_meta.zone}; "
                f"alert confidence {confidence:.0%}."
            )

        if packet.vision.threat:
            return (
                f"{severity.capitalize()}-severity {label} observed in {packet.stream_meta.zone}, "
                f"but signal was suppressed at {confidence:.0%} confidence."
            )

        return (
            f"No actionable security threat detected in {packet.stream_meta.zone}; "
            f"event suppressed at {confidence:.0%} confidence."
        )

    def generate_narrative(
        self,
        *,
        packet: FramePacket,
        agent_outputs: Sequence[AgentOutput],
        action: str,
        final_confidence: float,
    ) -> str:
        bullets = [
            self._threat_bullet(packet.vision),
            self._location_time_bullet(packet.timestamp, packet),
            self._confidence_bullet(agent_outputs, final_confidence),
            self._action_bullet(packet, action),
            self._history_bullet(packet),
        ]
        return "\n".join(bullets)

    def _confidence_bullet(
        self,
        agent_outputs: Sequence[AgentOutput],
        final_confidence: float,
    ) -> str:
        alert_votes = sum(1 for output in agent_outputs if output.verdict == "alert")
        suppress_votes = sum(1 for output in agent_outputs if output.verdict == "suppress")
        uncertain_votes = sum(1 for output in agent_outputs if output.verdict == "uncertain")
        vote_text = (
            f"agent consensus: {alert_votes} alert, {suppress_votes} suppress"
            + (f", {uncertain_votes} uncertain" if uncertain_votes else "")
        )
        return f"• Confidence: {final_confidence:.0%} ({vote_text})."

    def _threat_bullet(self, vision: VisionResult) -> str:
        if not vision.threat:
            return f"• Threat: no actionable security threat detected (severity: {vision.severity})"
        label = self._event_label(vision)
        desc = (vision.description or "No additional scene details provided").strip().rstrip(".")
        return f"• Threat: {label} detected ({vision.severity} severity). Observed: {desc}."

    def _location_time_bullet(self, timestamp: datetime, packet: FramePacket) -> str:
        zone = packet.stream_meta.zone or "zone not specified"
        return f"• Location/Time: {zone} at {timestamp.strftime('%H:%M:%S UTC')}."

    def _action_bullet(self, packet: FramePacket, action: str) -> str:
        severity = packet.vision.severity.lower()
        if action == "alert":
            if severity in {"high", "critical"}:
                text = "Dispatch on-site verification now and preserve footage for incident review"
            else:
                text = "Notify site team to verify the scene and keep active monitoring"
            return f"• Recommended action: {text}."

        if packet.vision.threat and severity in {"medium", "high", "critical"}:
            return "• Recommended action: Keep this camera under observation for near-term escalation."
        return "• Recommended action: No immediate response required; continue standard monitoring."

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

    def _event_label(self, vision: VisionResult) -> str:
        categories = [cat for cat in vision.categories if cat and cat != "clear"]
        if not categories:
            return "security anomaly"
        return self._CATEGORY_LABELS.get(categories[0], categories[0].replace("_", " "))