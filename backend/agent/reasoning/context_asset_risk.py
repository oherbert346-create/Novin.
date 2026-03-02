from __future__ import annotations

from backend.agent.reasoning.base import (
    ReasoningAgent,
    _history_summary,
    _peer_summary,
    _stream_summary,
    _vision_summary,
)
from backend.models.schemas import FramePacket

_SYSTEM = """You are Context & Asset Risk Analyst.
Estimate contextual business impact and zone/asset risk.
Rules: output JSON only, no prose.
{
  "verdict": "alert"|"suppress"|"uncertain",
  "confidence": 0..1,
  "rationale": "<=120 words",
  "chain_notes": {
    "zone_risk_multiplier": 0.5..3.0,
    "asset_risk_level": "critical"|"high"|"medium"|"low",
    "coordinated_attack_indicator": bool,
    "after_hours": bool
  }
}
Favor concise, operational language."""


class ContextAssetRiskAgent(ReasoningAgent):
    agent_id = "context_asset_risk"
    role = "Context & Asset Risk Analyst"
    system_prompt = _SYSTEM
    chain_defaults = {
        "zone_risk_multiplier": 1.0,
        "asset_risk_level": "medium",
        "coordinated_attack_indicator": False,
        "after_hours": False,
    }

    def _build_user_content(self, packet: FramePacket, peer_outputs: dict) -> str:
        hour = packet.timestamp.hour
        after_hours = hour < 6 or hour >= 20

        return (
            f"{_stream_summary(packet)}\n"
            f"{_vision_summary(packet)}\n"
            f"{_history_summary(packet)}\n"
            f"TIME: hour={hour} after_hours={after_hours}\n"
            f"{_peer_summary(peer_outputs)}\n\n"
            "TASK: Quantify contextual risk and decide if context elevates to alert. "
            "Return only JSON."
        )
