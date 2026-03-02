from __future__ import annotations

from backend.agent.reasoning.base import (
    ReasoningAgent,
    _history_summary,
    _peer_summary,
    _stream_summary,
    _vision_summary,
)
from backend.models.schemas import FramePacket

_SYSTEM = """You are Adversarial Challenger.
Primary objective: reduce false positives by arguing for suppression when plausible.
Rules: never output alert, output JSON only.
{
  "verdict": "suppress"|"uncertain",
  "confidence": 0..1,
  "rationale": "<=120 words",
  "chain_notes": {
    "benign_explanation": "string",
    "false_positive_risk": "high"|"medium"|"low",
    "challenge_strength": "strong"|"moderate"|"weak",
    "history_supports_suppress": bool
  }
}
Favor concise, operational language."""


class AdversarialChallengerAgent(ReasoningAgent):
    agent_id = "adversarial_challenger"
    role = "Adversarial Challenger"
    system_prompt = _SYSTEM
    allowed_verdicts = ("suppress", "uncertain")
    chain_defaults = {
        "benign_explanation": "insufficient evidence",
        "false_positive_risk": "medium",
        "challenge_strength": "moderate",
        "history_supports_suppress": False,
    }

    def _build_user_content(self, packet: FramePacket, peer_outputs: dict) -> str:
        return (
            f"{_stream_summary(packet)}\n"
            f"{_vision_summary(packet)}\n"
            f"{_history_summary(packet)}\n\n"
            f"{_peer_summary(peer_outputs)}\n\n"
            "TASK: Challenge peers, propose strongest benign explanation, and rate false-positive risk. "
            "Return only JSON with suppress or uncertain verdict."
        )
