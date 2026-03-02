from __future__ import annotations

from backend.agent.reasoning.base import (
    ReasoningAgent,
    _history_summary,
    _peer_summary,
    _stream_summary,
    _vision_summary,
)
from backend.models.schemas import FramePacket

_SYSTEM = """You are Behavioural Pattern Analyst.
Classify intent and behavioural risk from scene + history + peers.
Rules: output JSON only, no prose.
{
  "verdict": "alert"|"suppress"|"uncertain",
  "confidence": 0..1,
  "rationale": "<=120 words",
  "chain_notes": {
    "intent_assessment": "hostile"|"neutral"|"ambiguous",
    "behaviour_type": "loitering|forced_entry|routine_movement|suspicious_surveillance|other",
    "cross_camera_pattern": bool
  }
}
Favor concise, operational language."""


class BehaviouralPatternAgent(ReasoningAgent):
    agent_id = "behavioural_pattern"
    role = "Behavioural Pattern Analyst"
    system_prompt = _SYSTEM
    chain_defaults = {
        "intent_assessment": "ambiguous",
        "behaviour_type": "other",
        "cross_camera_pattern": False,
    }

    def _build_user_content(self, packet: FramePacket, peer_outputs: dict) -> str:
        return (
            f"{_stream_summary(packet)}\n"
            f"{_vision_summary(packet)}\n"
            f"{_history_summary(packet)}\n\n"
            f"{_peer_summary(peer_outputs)}\n\n"
            "TASK: Decide if behavior is hostile, neutral, or ambiguous; include cross-camera pattern signal. "
            "Return only JSON."
        )
