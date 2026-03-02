from __future__ import annotations

from backend.agent.reasoning.base import (
    ReasoningAgent,
    _history_summary,
    _peer_summary,
    _stream_summary,
    _vision_summary,
)
from backend.models.schemas import FramePacket

_SYSTEM = """You are Threat Escalation Analyst.
Decide whether this scene is a credible escalating threat.
Rules: output JSON only, no prose.
{
  "verdict": "alert"|"suppress"|"uncertain",
  "confidence": 0..1,
  "rationale": "<=120 words",
  "chain_notes": {
    "escalation_trajectory": "stable"|"escalating"|"de-escalating",
    "threat_credibility": "confirmed"|"probable"|"possible"|"unlikely",
    "immediate_danger": bool
  }
}
Favor concise, operational language."""


class ThreatEscalationAgent(ReasoningAgent):
    agent_id = "threat_escalation"
    role = "Threat Escalation Analyst"
    system_prompt = _SYSTEM
    chain_defaults = {
        "escalation_trajectory": "stable",
        "threat_credibility": "possible",
        "immediate_danger": False,
    }

    def _build_user_content(self, packet: FramePacket, peer_outputs: dict) -> str:
        return (
            f"{_stream_summary(packet)}\n"
            f"{_vision_summary(packet)}\n"
            f"{_history_summary(packet)}\n\n"
            f"{_peer_summary(peer_outputs)}\n"
            "TASK: Assess threat credibility + escalation trajectory; weigh anomaly against baseline. "
            "Return only JSON."
        )
