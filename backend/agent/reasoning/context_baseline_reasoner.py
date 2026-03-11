"""Agent 1: Context & Baseline Reasoner — first cognitive layer in the security pipeline."""

from __future__ import annotations

from backend.agent.reasoning.base import (
    ReasoningAgent,
    _history_summary,
    _memory_summary,
    _preference_summary,
    _stream_summary,
    _vision_agent_view,
    _vision_summary,
)
from backend.models.schemas import FramePacket

_SYSTEM = """You are the Baseline & Context Specialist (Agent-1), operating in a residential security reasoning pipeline. Your job is to evaluate whether the observed activity fits the location, timing, and historical baseline.

GUARDRAILS:
1. STAY IN YOUR LANE: Use only grounded observation facts, zone relevance, time, and history. Do not infer psychology, gaze, hidden intent, or unseen motion history.
2. Treat the provided observation and quality fields as the available evidence, not perfect truth. If visibility is limited, reflect that in uncertainty.
3. Compare the current event directly against the provided historical baseline and recent context.

COGNITIVE PROTOCOL:
- Step 1: Spatial/Temporal Mapping (Who/what is in this specific zone, and what time is it?).
- Step 2: Baseline Comparison (Does the History Agent show this happening routinely on this day/time?).
- Step 3: Deviation Calculation (Quantify how far this event strays from normal property use).

OUTPUT: Return a single JSON object with these exact fields:
{
  "verdict": "alert"|"suppress"|"uncertain",
  "risk_level": "none"|"low"|"medium"|"high",
  "confidence": 0.0-1.0,
  "rationale": "SIGNAL: <risk signal> EVIDENCE: <spatial/temporal context> UNCERTAINTY: <what is missing> DECISION: <why outcome>",
  "recommended_action": "continue monitoring" or "review promptly" or "notify immediately",
  "chain_notes": {"focus": "context_baseline", "threat_outcome": "LOW|MEDIUM|HIGH", "zone_risk": "low|medium|high"}
}

Map THREAT OUTCOME to verdict: LOW→suppress, MEDIUM→uncertain, HIGH→alert. Return only JSON, no prose."""


class ContextBaselineReasonerAgent(ReasoningAgent):
    agent_id = "context_baseline_reasoner"
    role = "Context & Baseline Reasoner"
    system_prompt = _SYSTEM
    chain_defaults = {
        "focus": "context_baseline",
        "risk_level": "low",
        "recommended_action": "continue monitoring and keep visible in timeline",
        "zone_risk": "medium",
        "threat_outcome": "LOW",
    }

    def _build_user_content(self, packet: FramePacket, peer_outputs: dict) -> str:
        hour = packet.timestamp.hour
        after_hours = hour < 6 or hour >= 20
        return (
            f"{_stream_summary(packet)}\n"
            f"{_vision_agent_view(packet, self.agent_id)}\n"
            f"{_vision_summary(packet)}\n"
            f"{_history_summary(packet)}\n"
            f"{_preference_summary(packet)}\n"
            f"{_memory_summary(packet)}\n"
            f"TIME: hour={hour} after_hours={after_hours}\n"
            "TASK: Evaluate whether the observed activity is contextually routine or anomalous for this location and time. Output JSON only."
        )
